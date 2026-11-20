"""``upi_qr`` detector: payment QR codes on the FAST lane (spec 5.7).

Per frame (``scan.scan``): zxing-cpp on the full frame, a 2x upscale, then OpenCV aruco finder patterns with a
rectified re-decode for codes zxing could not read. Then, by what each QR turned out to be:

- decoded **payment** payload (``upi://``, ``upi:``, ``pay?pa=``, ``bhim://`` / ``tez://`` / ``phonepe://`` links,
  payment-link hosts, a bare UPI ID): masked (confidence 0.95; reason ``upi_scheme`` / ``upi_app_link`` /
  ``pay_link`` / ``upi_id``);
- QR found but **not decodable** (glare, partial cover, soft focus, tilt): masked as ``qr_undecodable`` (0.6) when
  ``categories.upi_qr.mask_when_unsure`` is on;
- decoded **non-payment** code (a website, Wi-Fi sign-in): passes, unless ``categories.upi_qr.sensitivity`` is at
  least 0.8, which masks every QR (``qr_any``, 0.8).

The quad is the decoder's symbol position grown by 16 % of each edge (quiet zone plus about a module of margin),
following tilt and perspective. The planner then pads by ``padding_px`` and styles it SOLID. The payload is read,
classified into a reason code and dropped inside ``scan``; ``Detection.extra`` carries codes only.
"""

from __future__ import annotations

import logging
import time
from typing import ClassVar

import cv2
import numpy as np

from contracts import (
    Category,
    DetectContext,
    Detection,
    DetectorConfig,
    Device,
    Frame,
    Lane,
    Quad,
    Resources,
)

from .scan import Found, expand_quad, scan

log = logging.getLogger("vision.upi_qr")

CONF_PAYMENT = 0.95
CONF_ALL_QR = 0.8
CONF_UNDECODABLE = 0.6
ALL_QR_SENSITIVITY = 0.8  # at or above this every QR is masked, not only payments
EXPAND_FRAC = 0.16  # per edge: ~4 modules of quiet zone at 25 modules, still >= 4 at 33
EXPAND_MIN_PX = 6.0
REASON_UNDECODABLE = "qr_undecodable"
REASON_ANY = "qr_any"
SOFT_BUDGET_S = 0.018  # optional scan stages stop here (FAST lane budget is 25 ms)


class UpiQrDetector:
    id: ClassVar[str] = "upi_qr"
    lane: ClassVar[Lane] = Lane.FAST
    categories: ClassVar[frozenset[Category]] = frozenset({Category.UPI_QR})
    device: ClassVar[Device] = Device.CPU
    vram_mb: ClassVar[int] = 0

    def __init__(self) -> None:
        self._cfg: DetectorConfig | None = None
        self._res: Resources | None = None
        self._aruco: cv2.QRCodeDetectorAruco | None = None
        self.last_ms: float = 0.0  # scan time of the latest frame (telemetry/tests, no content)

    # ------------------------------------------------------------------ plugin interface
    def configure(self, cfg: DetectorConfig, res: Resources) -> None:
        self._cfg, self._res = cfg, res
        self._aruco = cv2.QRCodeDetectorAruco()

    def warmup(self) -> None:
        if self._aruco is None:
            self._aruco = cv2.QRCodeDetectorAruco()
        scan(np.full((720, 1280, 3), 127, dtype=np.uint8), self._aruco)

    def close(self) -> None:
        self._aruco = None
        self._res = None

    # ------------------------------------------------------------------ detection
    def detect(self, frame: Frame, ctx: DetectContext) -> list[Detection]:
        if self._cfg is None or self._res is None or self._aruco is None:
            raise RuntimeError("upi_qr detector used before configure()")
        conf = self._cfg.categories.get(Category.UPI_QR)
        if conf is None or not conf.enabled:
            return []
        t0 = time.perf_counter()
        found = scan(
            frame.image,
            self._aruco,
            find_undecodable=conf.mask_when_unsure,
            budget_s=SOFT_BUDGET_S,
        )
        self.last_ms = (time.perf_counter() - t0) * 1000.0
        mask_all = conf.sensitivity >= ALL_QR_SENSITIVITY
        out: list[Detection] = []
        for f in found:
            det = self._detection(frame, f, mask_all=mask_all)
            if det is not None:
                out.append(det)
        return out

    def _detection(self, frame: Frame, f: Found, *, mask_all: bool) -> Detection | None:
        assert self._res is not None
        if f.decoded and f.reason is not None:
            reason, confidence = f.reason, CONF_PAYMENT
        elif f.decoded:
            if not mask_all:
                return None
            reason, confidence = REASON_ANY, CONF_ALL_QR
        else:
            reason, confidence = REASON_UNDECODABLE, CONF_UNDECODABLE
        side = float(np.mean([np.linalg.norm(f.quad[i] - f.quad[(i + 1) % 4]) for i in range(4)]))
        grown = expand_quad(f.quad, max(EXPAND_FRAC, EXPAND_MIN_PX / max(side, 1.0)))
        quad = Quad(tuple((float(x), float(y)) for x, y in grown)).clamped()  # type: ignore[arg-type]
        return Detection(
            detector=self.id,
            category=Category.UPI_QR,
            quad=quad,
            confidence=confidence,
            frame_seq=frame.seq,
            pts=frame.pts,
            completed_at=self._res.clock(),
            extra={"reason": reason, "decoder": f.decoder, "side_px": round(side, 1)},
        )
