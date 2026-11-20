"""Synthetic frame source. Needs numpy (imported lazily so `contracts` imports without it)."""

from __future__ import annotations

import time
from collections.abc import Iterator

from ..geometry import CANVAS_H, CANVAS_W, Quad
from ..media import Frame


class FakeFrameSource:
    """Yields 1280x720 BGR frames with a SPECIMEN-style card rectangle at a known quad.

    The card is a light rectangle with dark stripes (a stand-in for printed text). It is NOT an
    ID card and contains no personal data. ``card_quad`` is the ground truth for tests.
    """

    def __init__(
        self,
        n_frames: int | None = 90,
        fps: int = 30,
        card_quad: Quad | None = None,
        start_monotonic: float | None = None,
    ) -> None:
        self.n_frames = n_frames
        self.fps = fps
        self.card_quad = card_quad or Quad.from_xyxy(400, 200, 880, 480)
        self._t0 = time.monotonic() if start_monotonic is None else start_monotonic

    def _base(self):  # type: ignore[no-untyped-def]
        try:
            import numpy as np
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("FakeFrameSource requires numpy") from exc
        img = np.zeros((CANVAS_H, CANVAS_W, 3), dtype=np.uint8)
        img[:, :, 0] = np.linspace(40, 90, CANVAS_W, dtype=np.uint8)[None, :]
        img[:, :, 1] = 60
        img[:, :, 2] = 50
        x1, y1, x2, y2 = (int(v) for v in self.card_quad.bbox())
        img[y1:y2, x1:x2] = 235
        for y in range(y1 + 20, y2 - 20, 24):
            img[y : y + 8, x1 + 20 : x2 - 20] = 30
        img.setflags(write=False)
        return img

    def __iter__(self) -> Iterator[Frame]:
        img = self._base()
        seq = 0
        while self.n_frames is None or seq < self.n_frames:
            pts = seq / self.fps
            yield Frame(seq=seq, pts=pts, arrival=self._t0 + pts, image=img)
            seq += 1
