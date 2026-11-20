"""QR finding and decoding on one frame (CPU only). Pure functions; no state; no payload ever leaves this module.

Cascade (each stage runs only if the cheaper one found nothing, so a QR-free frame pays for all of them):

1. zxing-cpp on the 1280x720 gray frame (QR only, rotated and inverted codes tried): ~1.5 ms.
2. zxing-cpp on a 2x upscale (small or soft codes): ~5 ms.
3. OpenCV ``QRCodeDetectorAruco`` finder-pattern detection (``detectMulti``) for codes zxing could not read
   (glare, partial occlusion, tilt, soft focus): each candidate is rectified and decoded again; a candidate that
   still does not decode is an *undecodable QR*.
4. zxing "errored" candidates (a symbol was located but its checksum failed) are kept only if the aruco
   detector confirms a QR on the upscaled crop (zxing alone also fires on bullseye-like logos).

WeChat QR (opencv-contrib) is not used: without its CNN models it needs 0.7 s on a QR-free 720p frame
(measured), far over the FAST budget, and the models are not in ``models/manifest.json``.

``scan`` returns ``Found`` objects holding a geometry and a reason *code* only (``payload.classify_payload``
runs inside this module and the text is dropped immediately).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
import zxingcpp

from .payload import classify_payload

_QR = zxingcpp.BarcodeFormat.QRCode
MIN_SIDE_PX = 10.0  # a located symbol smaller than this is noise
UPSCALE = 2
RECT_SIDE = 288  # rectified candidate size (px)
RECT_MARGIN = 0.12  # quiet-zone margin added around a candidate before rectifying (fraction of side)
CROP_PAD = 0.35
CROP_TARGET_PX = 240.0


@dataclass(slots=True)
class Found:
    quad: np.ndarray  # (4, 2) float32, clockwise from top-left, the symbol only (no quiet zone)
    decoded: bool
    reason: str | None  # payment code if decoded and a payment; None for a decoded non-payment or undecodable
    decoder: str  # "zxing" | "zxing_2x" | "zxing_crop" | "aruco_rect" | "aruco" | "aruco_crop"


def order_quad(pts: np.ndarray) -> np.ndarray:
    """Clockwise from the top-left corner (image coordinates, y down)."""
    p = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    c = p.mean(axis=0)
    ang = np.arctan2(p[:, 1] - c[1], p[:, 0] - c[0])
    p = p[np.argsort(ang)]  # ascending angle with y down = clockwise on screen
    start = int(np.argmin(p[:, 0] + p[:, 1]))
    return np.roll(p, -start, axis=0)


def _bbox(q: np.ndarray) -> tuple[float, float, float, float]:
    return float(q[:, 0].min()), float(q[:, 1].min()), float(q[:, 0].max()), float(q[:, 1].max())


def bbox_iou(a: np.ndarray, b: np.ndarray) -> float:
    ax1, ay1, ax2, ay2 = _bbox(a)
    bx1, by1, bx2, by2 = _bbox(b)
    iw, ih = max(0.0, min(ax2, bx2) - max(ax1, bx1)), max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


def _side(q: np.ndarray) -> float:
    return float(np.mean([np.linalg.norm(q[i] - q[(i + 1) % 4]) for i in range(4)]))


def _position_quad(pos: Any, scale: float = 1.0) -> np.ndarray:
    corners = (pos.top_left, pos.top_right, pos.bottom_right, pos.bottom_left)
    return order_quad(np.array([(c.x, c.y) for c in corners], dtype=np.float32) * scale)


def expand_quad(q: np.ndarray, frac: float) -> np.ndarray:
    """Push each corner outward along both of its edges by ``frac`` of the edge (follows tilt and perspective)."""
    out = np.empty_like(q)
    for i in range(4):
        p, prev, nxt = q[i], q[(i - 1) % 4], q[(i + 1) % 4]
        out[i] = p + frac * (p - prev) + frac * (p - nxt)
    return out


def _decode(gray: np.ndarray, *, errors: bool) -> list:
    return zxingcpp.read_barcodes(gray, formats=_QR, return_errors=errors)


def _split(results: list, scale: float, decoder: str) -> tuple[list[Found], list[np.ndarray]]:
    decoded: list[Found] = []
    errored: list[np.ndarray] = []
    for r in results:
        try:
            quad = _position_quad(r.position, scale)
        except Exception:  # noqa: BLE001 - a malformed position must not fail the frame
            continue
        if _side(quad) < MIN_SIDE_PX:
            continue
        if r.valid:
            decoded.append(Found(quad, True, classify_payload(r.text), decoder))
        else:
            errored.append(quad)
    return decoded, errored


def _rectify(gray: np.ndarray, quad: np.ndarray) -> np.ndarray | None:
    """Warp a candidate (with its quiet zone) to a square patch for a second decode attempt."""
    big = expand_quad(quad, RECT_MARGIN)
    h, w = gray.shape[:2]
    if big[:, 0].max() < 0 or big[:, 1].max() < 0 or big[:, 0].min() > w or big[:, 1].min() > h:
        return None
    dst = np.array([[0, 0], [RECT_SIDE, 0], [RECT_SIDE, RECT_SIDE], [0, RECT_SIDE]], dtype=np.float32)
    try:
        mat = cv2.getPerspectiveTransform(big.astype(np.float32), dst)
    except cv2.error:
        return None
    return cv2.warpPerspective(gray, mat, (RECT_SIDE, RECT_SIDE), flags=cv2.INTER_LINEAR, borderValue=255)


def _aruco_quads(det: cv2.QRCodeDetectorAruco, image: np.ndarray) -> list[np.ndarray]:
    try:
        ok, pts = det.detectMulti(image)
    except cv2.error:
        return []
    if not ok or pts is None:
        return []
    return [
        order_quad(p) for p in np.asarray(pts, dtype=np.float32).reshape(-1, 4, 2) if _side(p) >= MIN_SIDE_PX
    ]


def _upscaled_crop(gray: np.ndarray, quad: np.ndarray) -> tuple[np.ndarray, float, int, int] | None:
    """Crop around a located symbol (padded), upscaled so the code is about ``CROP_TARGET_PX`` wide."""
    h, w = gray.shape[:2]
    x1, y1, x2, y2 = _bbox(quad)
    pad = CROP_PAD * max(x2 - x1, y2 - y1)
    x1, y1 = max(0, int(x1 - pad)), max(0, int(y1 - pad))
    x2, y2 = min(w, int(x2 + pad)), min(h, int(y2 + pad))
    if x2 - x1 < 16 or y2 - y1 < 16:
        return None
    crop = gray[y1:y2, x1:x2]
    f = float(np.clip(CROP_TARGET_PX / max(crop.shape[:2]), 1.0, 4.0))
    up = cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC) if f > 1.0 else crop
    return up, f, x1, y1


def _recover_in_crop(det: cv2.QRCodeDetectorAruco, gray: np.ndarray, quad: np.ndarray) -> Found | None:
    """A symbol zxing located but could not decode: re-decode an upscaled crop; failing that, keep it as
    undecodable only if the aruco finder detector confirms a QR there (zxing alone also fires on logos)."""
    crop = _upscaled_crop(gray, quad)
    if crop is None:
        return None
    up, f, x1, y1 = crop
    off = np.array([x1, y1], dtype=np.float32)
    dec, _ = _split(_decode(up, errors=False), 1.0, "zxing_crop")
    if dec:
        return Found(quad, True, dec[0].reason, "zxing_crop")
    quads = _aruco_quads(det, up)
    if not quads:
        return None
    best = max(quads, key=_side)
    return Found(order_quad(best / f + off), False, None, "aruco_crop")


def scan(
    image: np.ndarray,
    aruco: cv2.QRCodeDetectorAruco,
    *,
    find_undecodable: bool = True,
    budget_s: float = 0.018,
    upscale: bool = False,
    trace: list[tuple[str, float]] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> list[Found]:
    """Find every QR on a BGR frame. ``budget_s`` is a soft cap: optional stages are skipped once it is spent.

    ``upscale`` adds a full-frame 2x zxing pass (+5 ms on every frame; the candidate-region re-decodes below
    already upscale where it matters, so it is off by default). ``trace`` collects ``(stage, ms)`` pairs.
    """
    t0 = clock()
    last = t0

    def lap(stage: str) -> None:
        nonlocal last
        now = clock()
        if trace is not None:
            trace.append((stage, (now - last) * 1000.0))
        last = now

    def spent() -> float:
        return clock() - t0

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    found, errored = _split(_decode(gray, errors=True), 1.0, "zxing")
    lap("zxing")
    if upscale and not found and spent() < budget_s:
        up = cv2.resize(gray, None, fx=UPSCALE, fy=UPSCALE, interpolation=cv2.INTER_CUBIC)
        f2, e2 = _split(_decode(up, errors=True), 1.0 / UPSCALE, "zxing_2x")
        found.extend(f2)
        errored.extend(e2)
        lap("zxing_2x")
    if not find_undecodable:
        return _dedupe(found)

    # finder-pattern candidates. Readable codes are blanked first, so a frame with one decoded code costs the
    # same as a QR-free frame and only a second, unreadable code can still show up.
    if spent() < budget_s:
        probe = image
        if found:
            probe = image.copy()
            for f in found:
                cv2.fillConvexPoly(
                    probe, np.round(expand_quad(f.quad, 0.2)).astype(np.int32), (127, 127, 127)
                )
        cands = _aruco_quads(aruco, probe)
        lap("aruco")
        for quad in cands:
            if any(bbox_iou(quad, f.quad) > 0.3 for f in found):
                continue
            patch = _rectify(gray, quad)
            hit: Found | None = None
            if patch is not None:
                dec, _ = _split(_decode(patch, errors=False), 1.0, "aruco_rect")
                if dec:
                    hit = Found(quad, True, dec[0].reason, "aruco_rect")
            found.append(hit or Found(quad, False, None, "aruco"))
        lap("rectify")
    # zxing located something it could not decode and aruco did not report: look at the two largest only
    errored.sort(key=_side, reverse=True)
    for quad in errored[:2]:
        if spent() >= budget_s:
            break
        if any(bbox_iou(quad, f.quad) > 0.3 for f in found):
            continue
        rec = _recover_in_crop(aruco, gray, quad)
        if rec is not None:
            found.append(rec)
        lap("crop")
    return _dedupe(found)


def _dedupe(found: list[Found]) -> list[Found]:
    """One result per code: overlapping boxes collapse, preferring a decoded result."""
    out: list[Found] = []
    for f in sorted(found, key=lambda x: (not x.decoded, -_side(x.quad))):
        if all(bbox_iou(f.quad, o.quad) < 0.5 for o in out):
            out.append(f)
    return out
