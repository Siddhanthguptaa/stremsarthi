"""A QR code cut by a frame side (entering or leaving the picture): mask the whole square.

A half-visible code cannot be decoded and its finder patterns may be partly outside the frame, so the
finder-pattern search misses it. What stays visible is a dense block of black/white modules glued to one frame
side. The search looks for exactly that on a half-size copy: strong local contrast in both directions, closed
into blobs; a blob that touches exactly one left or right frame side, is about as tall as a QR code, is dense
and roughly binary (dark and light pixels, little mid-grey) is taken for the visible part of a code. The hidden
part is inferred from squareness (the code is as wide as it is tall) and the quad is returned grown beyond the
frame side. Pure OpenCV, about 1-2 ms at 720p, no text is read.
"""

from __future__ import annotations

import cv2
import numpy as np

SCALE = 0.5
GRAD_THR = 70  # morphological gradient above this counts as a module edge
CLOSE = 9  # half-size px: joins module edges into one blob but not a code and the text below it
MIN_H_FRAC, MAX_H_FRAC = 0.07, 0.8  # blob height as a share of the frame height
MIN_DEPTH_FRAC = 0.2  # visible width as a share of the blob height
MIN_DENSITY = 0.22  # share of edge pixels inside the blob box
MIN_BINARY = 0.4  # share of pixels that are clearly dark or clearly light (motion blur lowers it)
EDGE_TOL = 3.0


def find_edge_qr(gray: np.ndarray) -> list[np.ndarray]:
    """Quads (4x2, full-frame px, may run beyond the frame) of QR codes cut by a left or right frame side."""
    h, w = gray.shape[:2]
    small = cv2.resize(gray, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    # only a band along each side can hold the visible part: keeps the cost small
    band = int(0.5 * sw)
    out: list[np.ndarray] = []
    for side in ("left", "right"):
        x_lo = 0 if side == "left" else sw - band
        roi = small[:, x_lo : x_lo + band]
        k = np.ones((3, 3), np.uint8)
        grad = cv2.morphologyEx(roi, cv2.MORPH_GRADIENT, k)
        edges = (grad > GRAD_THR).astype(np.uint8)
        blobs = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((CLOSE, CLOSE), np.uint8))
        n, _lab, stats, _ = cv2.connectedComponentsWithStats(blobs, connectivity=8)
        for i in range(1, n):
            x, y, bw, bh, _area = stats[i]
            gx = x + x_lo  # half-size frame coordinates
            touches = gx <= EDGE_TOL if side == "left" else gx + bw >= sw - EDGE_TOL
            if not touches:
                continue
            if not (MIN_H_FRAC * sh <= bh <= MAX_H_FRAC * sh) or bw < MIN_DEPTH_FRAC * bh:
                continue
            box_edges = edges[y : y + bh, x : x + bw]
            if float(box_edges.mean()) < MIN_DENSITY:
                continue
            patch = roi[y : y + bh, x : x + bw]
            binary = float(((patch < 70) | (patch > 185)).mean())
            if binary < MIN_BINARY:
                continue
            side_px = float(bh) / SCALE  # the code is as wide as it is tall
            vis = float(bw) / SCALE
            y0, y1 = y / SCALE, (y + bh) / SCALE
            if side == "right":
                x0, x1 = gx / SCALE, gx / SCALE + max(side_px, vis)
            else:
                x1 = (gx + bw) / SCALE
                x0 = x1 - max(side_px, vis)
            pad = 0.08 * side_px
            out.append(
                np.array(
                    [[x0 - pad, y0 - pad], [x1 + pad, y0 - pad], [x1 + pad, y1 + pad], [x0 - pad, y1 + pad]],
                    np.float32,
                )
            )
    return _dedupe(out)


def _dedupe(quads: list[np.ndarray]) -> list[np.ndarray]:
    """One quad per code: a smaller box mostly inside a bigger one is the same code seen at another blob size."""
    kept: list[np.ndarray] = []
    for q in sorted(quads, key=lambda a: -float(np.ptp(a[:, 0]) * np.ptp(a[:, 1]))):
        x0, y0, x1, y1 = q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max()
        inside = False
        for k in kept:
            iw = min(x1, k[:, 0].max()) - max(x0, k[:, 0].min())
            ih = min(y1, k[:, 1].max()) - max(y0, k[:, 1].min())
            if iw > 0 and ih > 0 and iw * ih > 0.6 * (x1 - x0) * (y1 - y0):
                inside = True
                break
        if not inside:
            kept.append(q)
    return kept
