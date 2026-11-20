"""ONNX Runtime session factory honouring ``Config.gpu``.

Adapted from IRL Shield ``bench/onnx_detectors.py`` ``_session`` (MIT, Copyright (c) 2026 Elan Nash;
see ``vision/LICENSE.irl-shield``). What changed: the CoreML branch is gone; the CUDA arena limit comes
from ``gpu.mem_limit_mb`` instead of a constant; ``onnxruntime-gpu`` DLL preloading is explicit; CPU is
the default; errors are specific; sessions are cached per model id by ``OrtSessionFactory``.

``disable_fallback`` semantics (kept from IRL Shield): after the session is built we call
``InferenceSession.disable_fallback()`` so that a *runtime* failure of the requested execution provider
raises instead of silently re-running on the CPU, and we assert the requested provider is really active.
Operators the CUDA provider does not implement are still partitioned to the CPU provider at load time
(that stays on, it is how PP-OCR runs at all); the returned metadata records it.

GPU use: wrap the whole process in ``flock /tmp/streamsarthi-gpu.lock`` (one GPU job at a time).
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import onnxruntime as ort

from contracts import Config

MIB = 1024 * 1024
DEFAULT_THREADS = 2  # spec: 2 ORT threads for the ACCURATE lane
CPU_EP = "CPUExecutionProvider"
CUDA_EP = "CUDAExecutionProvider"


class OrtProviderError(RuntimeError):
    """The requested execution provider is not available or did not activate."""


@dataclass(frozen=True, slots=True)
class OrtSettings:
    provider: Literal["cpu", "cuda"] = "cpu"
    threads: int = DEFAULT_THREADS
    mem_limit_mb: int = 0  # CUDA arena cap; 0 = not applicable (CPU)
    disable_fallback: bool = True
    device_id: int = 0

    def __post_init__(self) -> None:
        if self.provider not in ("cpu", "cuda"):
            raise ValueError("provider must be 'cpu' or 'cuda'")
        if isinstance(self.threads, bool) or not isinstance(self.threads, int) or self.threads < 1:
            raise ValueError("threads must be a positive integer")
        if self.provider == "cuda" and self.mem_limit_mb <= 0:
            raise ValueError("a cuda session needs a positive mem_limit_mb (the arena must be capped)")

    @classmethod
    def from_config(cls, config: Config, *, threads: int = DEFAULT_THREADS) -> OrtSettings:
        """CPU unless ``gpu.enabled`` and ``gpu.ort_provider == 'cuda'``."""
        if config.gpu.enabled and config.gpu.ort_provider == "cuda":
            return cls("cuda", threads=threads, mem_limit_mb=config.gpu.mem_limit_mb)
        return cls("cpu", threads=threads)


_preload_lock = threading.Lock()
_preloaded = False


def preload_gpu_libraries() -> None:
    """Make the pip-installed CUDA/cuDNN libraries visible to ``onnxruntime-gpu`` (idempotent).

    ``ort.preload_dlls()`` (onnxruntime >= 1.21) loads them from the ``nvidia-*-cu12`` wheels, so no
    system CUDA toolkit is needed; the Windows driver supplies ``libcuda`` through WSL2.
    """
    global _preloaded
    with _preload_lock:
        if _preloaded:
            return
        fn = getattr(ort, "preload_dlls", None)
        if fn is None:
            raise OrtProviderError(
                "this onnxruntime build has no preload_dlls(); install onnxruntime-gpu>=1.21"
            )
        fn()
        _preloaded = True


def available_providers() -> list[str]:
    return list(ort.get_available_providers())


def session_options(threads: int = DEFAULT_THREADS) -> ort.SessionOptions:
    ort.disable_telemetry_events()
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = threads
    opts.inter_op_num_threads = 1
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    opts.enable_cpu_mem_arena = False
    opts.log_severity_level = 3  # errors only: keeps provider chatter out of the logs
    return opts


def cuda_provider_options(mem_limit_mb: int, device_id: int = 0) -> dict[str, str]:
    return {
        "device_id": str(device_id),
        "gpu_mem_limit": str(int(mem_limit_mb) * MIB),
        "arena_extend_strategy": "kSameAsRequested",
        "cudnn_conv_algo_search": "HEURISTIC",
        "cudnn_conv_use_max_workspace": "0",
        "do_copy_in_default_stream": "1",
    }


def build_session(
    model_path: str | Path, settings: OrtSettings | None = None
) -> tuple[ort.InferenceSession, dict[str, Any]]:
    """Create an ``InferenceSession`` and a metadata dict (providers, threads, versions).

    Raises ``OrtProviderError`` when the requested provider is unavailable or did not activate, so a
    GPU run can never be mislabelled as a CPU run (or the reverse).
    """
    s = settings or OrtSettings()
    path = Path(model_path)
    if not path.is_file():
        raise FileNotFoundError(f"ONNX model not found: {path}")
    providers: list[Any]
    if s.provider == "cuda":
        preload_gpu_libraries()
        if CUDA_EP not in ort.get_available_providers():
            raise OrtProviderError(f"CUDAExecutionProvider unavailable; available: {available_providers()}")
        providers = [(CUDA_EP, cuda_provider_options(s.mem_limit_mb, s.device_id)), CPU_EP]
        requested = CUDA_EP
    else:
        providers = [CPU_EP]
        requested = CPU_EP
    session = ort.InferenceSession(str(path), sess_options=session_options(s.threads), providers=providers)
    if s.disable_fallback:
        session.disable_fallback()
    if requested not in session.get_providers():
        raise OrtProviderError(
            f"ONNX Runtime did not activate {requested} (active: {session.get_providers()}); "
            "check CUDA/cuDNN libraries and the driver"
        )
    meta = {
        "model_path": str(path),
        "execution_provider": requested,
        "active_execution_providers": session.get_providers(),
        "cpu_operator_partition_fallback_allowed": s.provider == "cuda",
        "disable_fallback": s.disable_fallback,
        "threads": s.threads,
        "gpu_mem_limit_mb": s.mem_limit_mb if s.provider == "cuda" else 0,
        "onnxruntime_version": ort.__version__,
    }
    return session, meta


@dataclass
class OrtSessionFactory:
    """Creates and caches one session per model id (a key of ``models/manifest.json``).

    A model id may carry a static shape, ``"ppocr-v5-mobile:544x960"``: the fixed-shape graph is derived
    (and cached next to the weights) by ``vision.ort_static``. Thread-safe.
    """

    settings: OrtSettings = field(default_factory=OrtSettings)
    cache_dir: Path | None = None
    threads_by_model: Mapping[str, int] = field(default_factory=dict)
    _sessions: dict[str, ort.InferenceSession] = field(default_factory=dict, init=False, repr=False)
    metadata: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @classmethod
    def from_config(cls, config: Config, cache_dir: Path | None = None) -> OrtSessionFactory:
        return cls(OrtSettings.from_config(config), cache_dir)

    def model_path(self, model_id: str) -> Path:
        from models.prepare import DEFAULT_CACHE, verified_path

        name, _, shape = model_id.partition(":")
        cache = self.cache_dir or DEFAULT_CACHE
        path, artifact = verified_path(name, cache)
        if not shape:
            return path
        try:
            height_s, width_s = shape.lower().split("x")
            height, width = int(height_s), int(width_s)
        except ValueError:
            raise ValueError(f"bad static shape in model id {model_id!r}; expected NAME:HxW") from None
        from vision.ort_static import prepare_static

        derived, _ = prepare_static(path, artifact["sha256"], height, width, cache)
        return derived

    def session(self, model_id: str) -> ort.InferenceSession:
        with self._lock:
            cached = self._sessions.get(model_id)
            if cached is not None:
                return cached
            path = self.model_path(model_id)
            threads = self.threads_by_model.get(model_id.partition(":")[0], self.settings.threads)
            settings = OrtSettings(
                self.settings.provider,
                threads=threads,
                mem_limit_mb=self.settings.mem_limit_mb,
                disable_fallback=self.settings.disable_fallback,
                device_id=self.settings.device_id,
            )
            sess, meta = build_session(path, settings)
            self._sessions[model_id] = sess
            self.metadata[model_id] = meta
            return sess

    def close(self) -> None:
        with self._lock:
            self._sessions.clear()
