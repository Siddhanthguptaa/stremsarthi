"""Shared face plumbing for the detector and the enroller: YuNet, SFace, geometry, low-light handling.

Both ``detector.py`` (matching) and ``enroll.py`` (enrolment) build their embeddings with ``FaceModels``
below, so enrolment and matching always use the same model and the same alignment. (PrivaStream enrolled
with buffalo_s and matched with buffalo_l, which made its allowlist useless; spec 5.5.)

Ideas adapted from PrivaStream ``face_detector.py`` (Apache-2.0, ``vision/LICENSE.privastream``): CLAHE on the
luma channel when the frame is dark (mean below 60), and dilating the face box before masking. What changed:
YuNet + SFace through OpenCV instead of InsightFace, the CLAHE runs on the 640x360 detection copy only, and
the dilation is a fraction of the box instead of a fixed pixel count.

Nothing here stores or logs pixels or embeddings.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np

from contracts import CANVAS_H, CANVAS_W, Quad

YUNET_ID = "yunet-2023mar"
SFACE_ID = "sface-2021dec"
EMBEDDING_DIM = 128

DET_W, DET_H = 640, 360  # the FAST lane detects on this copy of the 1280x720 frame
NMS_THRESHOLD = 0.3
TOP_K = 50
LOW_LIGHT_LUMA = 60.0  # mean luma below this triggers CLAHE (PrivaStream lowlight_trigger)

# YuNet row layout: x, y, w, h, then five landmarks (x, y) each, then the score.
# Landmark order: right eye, left eye, nose tip, right mouth corner, left mouth corner
# ("right" is the subject's right, which is the left side of the image for a frontal face).
LM_NOSE = 2
LM_MOUTH_R = 3
LM_MOUTH_L = 4


@dataclass(frozen=True, slots=True)
class Face:
    """One detected face in the coordinates of the image it was found in. ``row`` is the 15-float YuNet row
    (what ``FaceRecognizerSF.alignCrop`` wants); ``landmarks`` is a (5, 2) view of it."""

    row: np.ndarray

    @property
    def x1(self) -> float:
        return float(self.row[0])

    @property
    def y1(self) -> float:
        return float(self.row[1])

    @property
    def x2(self) -> float:
        return float(self.row[0] + self.row[2])

    @property
    def y2(self) -> float:
        return float(self.row[1] + self.row[3])

    @property
    def w(self) -> float:
        return float(self.row[2])

    @property
    def h(self) -> float:
        return float(self.row[3])

    @property
    def score(self) -> float:
        return float(self.row[14])

    @property
    def landmarks(self) -> np.ndarray:
        return self.row[4:14].reshape(5, 2)

    def scaled(self, sx: float, sy: float) -> Face:
        return Face(_scale_row(self.row, sx, sy))


def _scale_row(row: np.ndarray, sx: float, sy: float) -> np.ndarray:
    """Scale a YuNet row: x, w, landmark x by ``sx``; y, h, landmark y by ``sy``; the score stays."""
    out = np.asarray(row, dtype=np.float32).copy()
    out[[0, 2, 4, 6, 8, 10, 12]] *= sx
    out[[1, 3, 5, 7, 9, 11, 13]] *= sy
    return out


def faces_from_rows(rows: np.ndarray | None, sx: float = 1.0, sy: float = 1.0) -> list[Face]:
    if rows is None or len(rows) == 0:
        return []
    return [Face(_scale_row(r, sx, sy)) for r in np.asarray(rows, dtype=np.float32).reshape(-1, 15)]


# --------------------------------------------------------------------------- backend
class FaceBackend(Protocol):
    """What the detector and the enroller need from the models (tests inject a fake)."""

    def detect(self, image: np.ndarray, score_threshold: float) -> np.ndarray | None:
        """(N, 15) YuNet rows in the coordinates of ``image`` (any size), or None."""
        ...

    def align(self, image: np.ndarray, row: np.ndarray) -> np.ndarray:
        """The 112x112 aligned crop of the face ``row`` (cheap: a warp, about 0.1 ms)."""
        ...

    def embed_aligned(self, aligned: np.ndarray) -> np.ndarray:
        """L2-normalised 128-d SFace embedding of an aligned crop (the expensive step, about 15 ms)."""
        ...

    def embed(self, image: np.ndarray, row: np.ndarray) -> np.ndarray:
        """``embed_aligned(align(image, row))``."""
        ...

    def warmup(self) -> None: ...


def l2_normalise(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32).reshape(-1)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


class FaceModels:
    """YuNet 2023mar (``cv2.FaceDetectorYN``) and SFace 2021dec (``cv2.FaceRecognizerSF``) on the CPU.

    The OpenCV objects keep state, so each has its own lock: the detector thread (YuNet) and an embedding
    worker (SFace) run in parallel, and the API enroller may be called from request threads.
    """

    def __init__(self, yunet_path: Path, sface_path: Path) -> None:
        self._det_lock = threading.Lock()
        self._rec_lock = threading.Lock()
        self._det = cv2.FaceDetectorYN.create(str(yunet_path), "", (DET_W, DET_H), 0.6, NMS_THRESHOLD, TOP_K)
        self._rec = cv2.FaceRecognizerSF.create(str(sface_path), "")
        self._size = (DET_W, DET_H)

    def detect(self, image: np.ndarray, score_threshold: float) -> np.ndarray | None:
        h, w = image.shape[:2]
        with self._det_lock:
            if (w, h) != self._size:
                self._det.setInputSize((w, h))
                self._size = (w, h)
            self._det.setScoreThreshold(float(score_threshold))
            _, rows = self._det.detect(image)
        return rows

    def align(self, image: np.ndarray, row: np.ndarray) -> np.ndarray:
        with self._rec_lock:
            return np.asarray(self._rec.alignCrop(image, np.asarray(row, dtype=np.float32).reshape(1, 15)))

    def embed_aligned(self, aligned: np.ndarray) -> np.ndarray:
        with self._rec_lock:
            feat = self._rec.feature(aligned)
        return l2_normalise(feat)

    def embed(self, image: np.ndarray, row: np.ndarray) -> np.ndarray:
        return self.embed_aligned(self.align(image, row))

    def warmup(self) -> None:
        self.detect(np.zeros((DET_H, DET_W, 3), np.uint8), 0.6)
        self.embed_aligned(np.zeros((112, 112, 3), np.uint8))  # SFace aligned-crop size


def load_models(model_path: Callable[[str], Path] | None = None) -> FaceModels:
    """Build ``FaceModels``. ``model_path`` is ``Resources.model_path``; without it the hash-pinned cache
    under ``models/weights`` is used (``python models/prepare.py --profile dev`` downloads it)."""
    if model_path is None:
        from models.prepare import DEFAULT_CACHE, verified_path

        def model_path(model_id: str) -> Path:
            return verified_path(model_id, DEFAULT_CACHE)[0]

    return FaceModels(Path(model_path(YUNET_ID)), Path(model_path(SFACE_ID)))


# --------------------------------------------------------------------------- image helpers
def mean_luma(image: np.ndarray) -> float:
    return float(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).mean())


def enhance_lowlight(image: np.ndarray) -> np.ndarray:
    """CLAHE on the luma channel (PrivaStream idea): clip 2.0, 8x8 tiles."""
    ycrcb = cv2.cvtColor(image, cv2.COLOR_BGR2YCrCb)
    y, cr, cb = cv2.split(ycrcb)
    y = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(y)
    return cv2.cvtColor(cv2.merge((y, cr, cb)), cv2.COLOR_YCrCb2BGR)


def dilate_box(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    total: float,
    width: int = CANVAS_W,
    height: int = CANVAS_H,
) -> Quad:
    """Grow the box by ``total`` (0.20 = 20 % bigger: ``total / 2`` of the width and height per side) and clamp."""
    dx, dy = (x2 - x1) * total / 2.0, (y2 - y1) * total / 2.0
    return Quad.from_xyxy(
        min(max(x1 - dx, 0.0), float(width)),
        min(max(y1 - dy, 0.0), float(height)),
        min(max(x2 + dx, 0.0), float(width)),
        min(max(y2 + dy, 0.0), float(height)),
    )


MOUTH_WIDTH = 1.4  # of the mouth-corner distance (spec 5.5.5)
MOUTH_HEIGHT = 1.2


def mouth_quad(landmarks: np.ndarray, width: int = CANVAS_W, height: int = CANVAS_H) -> Quad | None:
    """The mouth region from YuNet's two mouth corners: centred on their midpoint, ``1.4 d`` wide along the
    line through the corners and ``1.2 d`` high, where ``d`` is the corner distance. Rotated with the head
    (clockwise from the top-left, like every ``Quad``), clamped to the canvas. None for degenerate landmarks."""
    a = np.asarray(landmarks[LM_MOUTH_R], dtype=np.float64)
    b = np.asarray(landmarks[LM_MOUTH_L], dtype=np.float64)
    d = float(np.hypot(*(b - a)))
    if not np.isfinite(d) or d < 2.0:
        return None
    u = (b - a) / d
    if u[0] < 0:  # always point to the right of the image so the quad starts at the top-left
        u = -u
    v = np.array([-u[1], u[0]])  # 90 degrees clockwise on screen: points down for an upright face
    c = (a + b) / 2.0
    hw, hh = MOUTH_WIDTH * d / 2.0, MOUTH_HEIGHT * d / 2.0
    pts = [c - u * hw - v * hh, c + u * hw - v * hh, c + u * hw + v * hh, c - u * hw + v * hh]
    clamped = tuple(
        (min(max(float(p[0]), 0.0), float(width)), min(max(float(p[1]), 0.0), float(height))) for p in pts
    )
    return Quad(clamped)  # type: ignore[arg-type]


def as_bgr(image: Any) -> np.ndarray | None:
    """A usable BGR uint8 image, or None. Accepts arrays and encoded bytes (JPEG/PNG)."""
    if isinstance(image, (bytes, bytearray, memoryview)):
        image = cv2.imdecode(np.frombuffer(bytes(image), np.uint8), cv2.IMREAD_COLOR)
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
        return None
    if image.shape[0] < 16 or image.shape[1] < 16:
        return None
    return image
