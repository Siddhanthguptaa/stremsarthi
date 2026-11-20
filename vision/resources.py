"""``Resources`` implementation handed to every detector (``contracts.Resources``).

Carries the ORT session factory, the model-path resolver, the in-RAM face allowlist, the shared OCR
provider and the clock. The OCR provider is the memoised ``vision.ocr.OcrService`` (T1.3), built lazily
(sessions load on the first call or ``warm_ocr``). If its models are unavailable it RAISES
``OcrUnavailableError`` instead of returning "no text", because an empty OCR result would silently mean
"nothing to mask": the detector that called it fails its lane run (ok=False) and the engine PROTECTs,
which is the safe direction. ``set_ocr`` swaps in another provider (tests, alternative engines).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from contracts import Config, Frame, OcrProvider, OcrResult

from .ocr.service import OcrService, OcrUnavailableError
from .ort import OrtSessionFactory

__all__ = ["InMemoryAllowlist", "OcrUnavailableError", "VisionResources"]


class InMemoryAllowlist:
    """Enrolled-face embeddings (L2-normalised SFace vectors), RAM only. Biometric: never logged.

    ``update`` replaces the whole set and bumps ``version`` so detectors can refresh cached copies.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: tuple[tuple[str, Any], ...] = ()
        self.version = 0

    def update(self, entries: Sequence[tuple[str, Any]]) -> None:
        with self._lock:
            self._entries = tuple((str(i), v) for i, v in entries)
            self.version += 1

    def clear(self) -> None:
        self.update(())

    def embeddings(self) -> Sequence[tuple[str, Any]]:
        with self._lock:
            return self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:  # never show embedding values
        return f"InMemoryAllowlist(n={len(self._entries)}, version={self.version})"


class VisionResources:
    def __init__(
        self,
        config: Config,
        *,
        factory: OrtSessionFactory | None = None,
        allowlist: InMemoryAllowlist | None = None,
        ocr: OcrProvider | Callable[[Frame], OcrResult] | None = None,
        clock: Callable[[], float] = time.monotonic,
        cache_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.factory = factory or OrtSessionFactory.from_config(config, cache_dir)
        self.clock = clock
        self.allowlist: InMemoryAllowlist = allowlist or InMemoryAllowlist()
        self._own_ocr: OcrService | None = None
        if ocr is None:
            self._own_ocr = OcrService(config, self.factory)
        self.ocr: Callable[[Frame], OcrResult] = ocr or self._own_ocr  # type: ignore[assignment]

    @property
    def ocr_installed(self) -> bool:
        return self.ocr is not None

    def warm_ocr(self) -> None:
        """Load the OCR sessions now (a no-op for an injected provider without ``warmup``)."""
        warm = getattr(self.ocr, "warmup", None)
        if warm is not None:
            warm()

    def set_ocr(self, provider: OcrProvider | Callable[[Frame], OcrResult]) -> None:
        self.ocr = provider

    def ort_session(self, model_id: str) -> Any:
        return self.factory.session(model_id)

    def model_path(self, model_id: str) -> Path:
        return self.factory.model_path(model_id)

    def close(self) -> None:
        if self._own_ocr is not None:
            self._own_ocr.close()
        self.factory.close()
