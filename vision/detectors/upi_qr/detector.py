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

Benign memory. A code that decoded as a non-payment on one run and fails to decode on the next (compression
noise, glare) is the same code: an undecodable candidate that is nearly the same box (IoU >= ``BENIGN_MATCH_IOU``,
area change <= ``BENIGN_AREA_CHANGE``) as a non-payment code decoded within the last ``BENIGN_TTL_S`` is not masked,
for at most ``BENIGN_MAX_PASSES`` runs in a row. Only the geometry and the kind of the sighting (non-payment or
payment) are remembered, never the payload, and only a decoded code ever creates or refreshes a sighting: an
undecodable run never does.
The most recent sighting at a place decides: a payment decoded there after (or instead of) the non-payment code
keeps an undecodable candidate masked, and so does a candidate whose box moved or resized beyond the limits (a
different code, e.g. a payment QR put over the website one). A code that never decodes, or that decodes as a
payment, is masked exactly as before; so is an undecodable code anywhere else.
Without this a single undecodable run on a readable website QR masked it for the 2 s hold of the category; the
short TTL and the strict match keep a payment QR that replaces the website code from slipping through unmasked.

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

from .scan import Found, _bbox, bbox_iou, expand_quad, scan

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
BENIGN_TTL_S = 0.35  # an undecodable candidate on a code that decoded as a non-payment this recently passes
BENIGN_IOU = 0.5  # "same place": the newest sighting overlapping this much decides (payment there: always masked)
BENIGN_MATCH_IOU = 0.7  # to be trusted as the same website code the box must be nearly the same ...
BENIGN_AREA_CHANGE = 0.2  # ... and about the same size (relative area change)
BENIGN_MAX_PASSES = 1  # undecodable runs let through per decoded sighting; the next one is masked


def _area_change(a: np.ndarray, b: np.ndarray) -> float:
    """Relative change of the bounding-box area of ``a`` against ``b``."""
    ax1, ay1, ax2, ay2 = _bbox(a)
    bx1, by1, bx2, by2 = _bbox(b)
    area_b = max((bx2 - bx1) * (by2 - by1), 1e-6)
    return abs((ax2 - ax1) * (ay2 - ay1) - area_b) / area_b


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
        # geometry and kind of decoded codes only, no payload is kept
        # [quad 4x2, PTS, is_payment, undecodable passes used] of decoded codes
        self._seen: list[list] = []

    # ------------------------------------------------------------------ plugin interface
    def configure(self, cfg: DetectorConfig, res: Resources) -> None:
        self._cfg, self._res = cfg, res
        self._aruco = cv2.QRCodeDetectorAruco()
        self._seen = []

    def warmup(self) -> None:
        if self._aruco is None:
            self._aruco = cv2.QRCodeDetectorAruco()
        scan(np.full((720, 1280, 3), 127, dtype=np.uint8), self._aruco)

    def close(self) -> None:
        self._aruco = None
        self._res = None
        self._seen = []

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
        found = self._apply_benign_memory(found, frame.pts)
        out: list[Detection] = []
        for f in found:
            det = self._detection(frame, f, mask_all=mask_all)
            if det is not None:
                out.append(det)
        return out

    def _apply_benign_memory(self, found: list[Found], pts: float) -> list[Found]:
        """Drop undecodable candidates that are the same box as the newest overlapping sighting (within
        ``BENIGN_TTL_S``) when that was a non-payment code; then remember the decoded codes of this run."""
        if self._seen and pts < self._seen[-1][1] - 1.0:  # the source clock restarted
            self._seen = []
        self._seen = [e for e in self._seen if 0.0 <= pts - e[1] <= BENIGN_TTL_S]

        def same_code_as_non_payment(f: Found) -> bool:
            for entry in reversed(self._seen):  # newest first; the first sighting at this place decides
                quad, _t, payment, passes = entry
                if bbox_iou(f.quad, quad) < BENIGN_IOU:
                    continue
                if payment or passes >= BENIGN_MAX_PASSES:
                    return False
                if bbox_iou(f.quad, quad) < BENIGN_MATCH_IOU or _area_change(f.quad, quad) > BENIGN_AREA_CHANGE:
                    return False  # moved or resized: not the code we saw
                entry[3] = passes + 1
                return True
            return False

        kept = [f for f in found if f.decoded or not same_code_as_non_payment(f)]
        for f in found:
            if f.decoded:
                self._seen.append([f.quad, pts, f.reason is not None, 0])
        if len(self._seen) > 16:
            self._seen = self._seen[-16:]
        return kept

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
