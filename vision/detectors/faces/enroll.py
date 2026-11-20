"""``Enroller``: turns a handful of webcam frames into one allowlist embedding (``contracts.FaceEnroller``).

The API loads this class through ``Config.enrol.impl`` and calls ``enrol(images, label)`` (spec app-api 5.5,
D-41). Per frame: YuNet, the LARGEST face, SFace embedding (the same ``FaceModels`` the detector matches with,
so enrolment and matching can never disagree about the model). Frames without a usable face are skipped.
With at least ``min_frames`` usable frames the embeddings are averaged and L2-normalised; fewer raises
``ValueError("too_few_faces")``.

Two safeguards beyond the spec, both on the safe side:
* a face smaller than ``MIN_FACE_PX`` or scored under ``MIN_SCORE`` is "not usable" (its embedding is poor);
* a frame whose embedding is far from the rest (cosine to the mean of the OTHER frames under
  ``OUTLIER_COS``; somebody else walked into the shot) is dropped. If too few frames remain: ``too_few_faces``.

Nothing is persisted or logged here: no image, crop, embedding or path. The API owns the allowlist store
(D-41), so this class never reads ``vision.allowlist_dir``. ``face_id`` is a hash of the label and the
(rounded) embedding, so the same frames always give the same id.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

import cv2
import numpy as np

from contracts import EnrolConfig

from .core import FaceBackend, as_bgr, faces_from_rows, l2_normalise, load_models

MIN_SCORE = 0.7  # YuNet score of the face used for enrolment (the live detector accepts 0.5 to 0.6)
MIN_FACE_PX = 48  # shorter side of the face box, in the frame's own pixels
OUTLIER_COS = 0.30  # genuine same-person pairs sit well above this (SFace, 0.40 is the match threshold)
DETECT_MAX_SIDE = 640  # frames are detected at no more than this size (the browser sends <= 640 px)
MAX_FRAMES = 30  # frames beyond this are ignored (the API caps well below)


class Enroller:
    def __init__(self, *, min_frames: int | None = None, backend: FaceBackend | None = None) -> None:
        self.min_frames = EnrolConfig().min_frames if min_frames is None else int(min_frames)
        if self.min_frames < 1:
            raise ValueError("min_frames must be >= 1")
        self._backend: FaceBackend = backend or load_models()  # a missing weight fails here (API: 503)

    # ------------------------------------------------------------------ FaceEnroller
    def enrol(self, images: Sequence[Any], label: str) -> tuple[str, tuple[float, ...]]:
        embeddings: list[np.ndarray] = []
        for raw in list(images)[:MAX_FRAMES]:
            emb = self._embed_largest(as_bgr(raw))
            if emb is not None:
                embeddings.append(emb)
        if len(embeddings) < self.min_frames:
            raise ValueError("too_few_faces")
        kept = self._drop_outliers(embeddings)
        if len(kept) < self.min_frames:
            raise ValueError("too_few_faces")
        mean = l2_normalise(np.mean(np.stack(kept), axis=0))
        values = tuple(float(x) for x in mean)
        return self._face_id(label, mean), values

    # ------------------------------------------------------------------ steps
    def _embed_largest(self, image: np.ndarray | None) -> np.ndarray | None:
        if image is None:
            return None
        h, w = image.shape[:2]
        scale = min(1.0, DETECT_MAX_SIDE / max(h, w))
        small = (
            image if scale == 1.0 else cv2.resize(image, (round(w * scale), round(h * scale)), cv2.INTER_AREA)
        )
        rows = self._backend.detect(small, MIN_SCORE)
        faces = faces_from_rows(rows, w / small.shape[1], h / small.shape[0])  # back to the frame's pixels
        faces = [f for f in faces if min(f.w, f.h) >= MIN_FACE_PX]
        if not faces:
            return None
        biggest = max(faces, key=lambda f: f.w * f.h)
        return self._backend.embed(image, biggest.row)

    @staticmethod
    def _drop_outliers(embeddings: list[np.ndarray]) -> list[np.ndarray]:
        if len(embeddings) < 3:
            return embeddings
        stack = np.stack(embeddings)
        total = stack.sum(axis=0)
        kept = []
        for e in embeddings:
            rest = l2_normalise(total - e)  # mean of the other frames
            if float(e @ rest) >= OUTLIER_COS:
                kept.append(e)
        return kept

    @staticmethod
    def _face_id(label: str, embedding: np.ndarray) -> str:
        digest = hashlib.sha256(
            label.encode("utf-8") + b"\x00" + np.round(embedding, 4).astype("<f4").tobytes()
        )
        return "f_" + digest.hexdigest()[:16]
