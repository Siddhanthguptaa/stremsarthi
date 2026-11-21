"""A QR code cut by a left/right frame side is masked whole (CPU, no models). SPECIMEN payload only."""

from __future__ import annotations

import cv2
import numpy as np
import qrcode

from vision.detectors.upi_qr.edge import find_edge_qr

W, H = 1280, 720


def _qr(side: int) -> np.ndarray:
    q = qrcode.QRCode(border=0, box_size=10)
    q.add_data("upi://pay?pa=zz-specimen@ybl&pn=SPECIMEN")
    q.make(fit=True)
    img = np.array(q.make_image().convert("L"), np.uint8)
    return cv2.resize(img, (side, side), interpolation=cv2.INTER_NEAREST)


def _frame(x: int, y: int = 200, side: int = 280) -> np.ndarray:
    img = np.full((H, W), 140, np.uint8)
    code = _qr(side)
    pad = 24
    x0, x1 = max(x - pad, 0), min(x + side + pad, W)
    img[y - pad : y + side + pad, x0:x1] = 255  # quiet zone
    cx0, cx1 = max(x, 0), min(x + side, W)
    img[y : y + side, cx0:cx1] = code[:, cx0 - x : cx1 - x]
    return img


def test_half_visible_code_at_the_right_side_is_masked_beyond_the_frame() -> None:
    quads = find_edge_qr(_frame(1140))  # 140 of 280 px visible
    assert len(quads) == 1
    q = quads[0]
    assert q[:, 0].max() > W + 100, "the hidden half is inferred"
    assert q[:, 1].min() <= 200 and q[:, 1].max() >= 480, "the code's full height is covered"


def test_half_visible_code_at_the_left_side() -> None:
    quads = find_edge_qr(_frame(-140))
    assert len(quads) == 1 and quads[0][:, 0].min() < -100


def test_whole_code_in_the_middle_and_an_empty_frame_give_nothing() -> None:
    assert find_edge_qr(_frame(500)) == []
    assert find_edge_qr(np.full((H, W), 140, np.uint8)) == []
