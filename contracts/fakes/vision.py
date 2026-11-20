"""Doubles for the vision-side protocols: allowlist, resources, enroller."""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ..media import Frame
from ..protocols import OcrResult


class FakeAllowlist:
    def __init__(self, entries: Sequence[tuple[str, Any]] = (), version: int = 0) -> None:
        self._entries = tuple(entries)
        self.version = version

    def embeddings(self) -> Sequence[tuple[str, Any]]:
        return self._entries


class FakeResources:
    """No onnxruntime import. ``ort_session`` raises unless a stub was registered for the model id."""

    def __init__(
        self,
        allowlist: FakeAllowlist | None = None,
        ocr: Callable[[Frame], OcrResult] | None = None,
        models_dir: str | Path = "models/weights",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.clock = clock
        self.allowlist = allowlist or FakeAllowlist()
        self.ocr = ocr or (lambda frame: OcrResult((), (), (), ()))
        self._models_dir = Path(models_dir)
        self._sessions: dict[str, Any] = {}

    def register_session(self, model_id: str, session: Any) -> None:
        self._sessions[model_id] = session

    def ort_session(self, model_id: str) -> Any:
        try:
            return self._sessions[model_id]
        except KeyError:
            raise RuntimeError(f"no stub ORT session registered for {model_id!r}") from None

    def model_path(self, model_id: str) -> Path:
        return self._models_dir / model_id


class FakeEnroller:
    """Deterministic fake: the embedding is a hash of the label, L2-normalised. Needs >= 5 images."""

    def __init__(self, dim: int = 16) -> None:
        self.dim = dim

    def enrol(self, images: Sequence[Any], label: str) -> tuple[str, tuple[float, ...]]:
        if len(images) < 5:
            raise ValueError("too_few_faces")
        digest = hashlib.sha256(label.encode("utf-8")).digest()
        raw = [(digest[i % len(digest)] - 127.5) for i in range(self.dim)]
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return f"f_{digest[:4].hex()}", tuple(x / norm for x in raw)
