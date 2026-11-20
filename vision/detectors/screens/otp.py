"""OTP / notification screens from OCR text (spec 5.10, first bullet).

A one-time code is a 4-8 digit run next to a cue (``OTP``, ``one time password``, ``verification code``, ``do not
share``, ``<code> is your``, the Hindi ``ओटीपी`` / ``वन टाइम``) in the same text paragraph, or in a code-only box just
below the cue paragraph (OTP entry screens). Text lives for one frame only: nothing here logs or stores it.

Geometry: the paragraph that holds the cue and the code, grown to its banner (a flat-colour region around the
text), then to the whole phone screen when a 9:19.5 rectangle around the banner contains enough text boxes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np

from vision.ocr.layout import order_quad, poly_area, side_lengths
from vision.ocr.normalize import digit_ratio

# a strong cue names a one-time code by itself; a weak one ("do not share") only counts beside a code
STRONG_CUE_RE = re.compile(
    r"\b[o0]tp\b|one[\s-]?time[\s-]?(?:pass(?:word|code)|pin|code)|verification\s+code|security\s+code"
    r"|login\s+code|pass\s?code|वन\s*टाइम|ओटीपी|ओ\.\s?टी\.\s?पी|सत्यापन\s*कोड|वेरिफिकेशन\s*कोड|पासकोड",
    re.IGNORECASE,
)
CUE_RE = re.compile(
    STRONG_CUE_RE.pattern + r"|do\s+not\s+share|don'?t\s+share|do\s+not\s+disclose|साझा\s*न\s*करें",
    re.IGNORECASE,
)
# "<code> is your ..." counts as a cue only when the code leads it (a bare "is your" is far too common)
LEADING_CODE_RE = re.compile(
    r"(?<![\d#/.])\d{4,8}(?!\d)\s+(?:is|hai|है)\s+(?:your|aapka|आपका)\b", re.IGNORECASE
)
CODE_RE = re.compile(r"(?<![\d#/.])\d{4,8}(?![\d%/]|[.,]\d)")

H_PAD, V_PAD = 1.0, 0.9  # paragraph grouping: boxes whose rects, grown by this many text heights, touch
NEAR_CUE_H = 4.5  # a code-only box up to this many text heights below the cue paragraph (OTP entry screens)
BANNER_TOL = 12  # max per-channel difference from the banner colour
SCREEN_ASPECT = (1.7, 2.7)  # long/short of a phone screen: 19.5:9 is 2.17, margin for perspective
SCREEN_RECT_FILL = 0.88  # hull area / rotated-rectangle area
CONF_SAME_BOX, CONF_PARAGRAPH, CONF_NEAR, CONF_CUE_ONLY = 0.85, 0.8, 0.7, 0.55


@dataclass(slots=True)
class OtpHit:
    cue_idx: tuple[int, ...]  # boxes of the cue paragraph (or the one box)
    code_idx: int | None  # None: a strong cue without a readable code
    conf: float
    reason: str  # otp_same_box | otp_paragraph | otp_near_code | otp_cue_only


def rect_of(b: np.ndarray) -> tuple[float, float, float, float]:
    return float(b[:, 0].min()), float(b[:, 1].min()), float(b[:, 0].max()), float(b[:, 1].max())


def has_code(text: str) -> bool:
    return CODE_RE.search(text) is not None


def is_code_only_box(text: str) -> bool:
    """A box that is (almost) only the code: 4-8 digit-like characters (``BOG000`` is an OCR-damaged 800000)."""
    s = re.sub(r"[\s-]", "", text)
    if not 4 <= len(s) <= 8:
        return False
    return digit_ratio(s) >= 0.8 and sum(c.isdigit() for c in s) >= 3


def paragraphs(boxes: list[np.ndarray], heights: list[float]) -> list[list[int]]:
    """Group boxes that touch once grown by a fraction of a text height (adjacent lines of one block)."""
    n = len(boxes)
    rects = [rect_of(b) for b in boxes]
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            hi, hj = max(heights[i], 1.0), max(heights[j], 1.0)
            if not 0.55 <= hi / hj <= 1.8:
                continue
            h = (hi + hj) / 2.0
            a, b = rects[i], rects[j]
            if (
                a[0] - H_PAD * h < b[2]
                and b[0] - H_PAD * h < a[2]
                and a[1] - V_PAD * h < b[3]
                and b[1] - V_PAD * h < a[3]
            ):
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = []
    for g in groups.values():
        g.sort(key=lambda k: (round(rects[k][1] / max(heights[k], 1.0)), rects[k][0]))
        out.append(g)
    return out


def find_otps(
    boxes: list[np.ndarray],
    texts: list[str],
    heights: list[float],
    confs: list[float],
    min_conf: float = 0.0,
    *,
    cue_only: bool = False,
) -> list[OtpHit]:
    """Cue plus code pairs. ``texts`` are normalised (ASCII digits); boxes with empty text are ignored.
    With ``cue_only`` (``mask_when_unsure``) a paragraph with a strong cue and no readable code is a hit too
    (the code is on the screen but the OCR could not read it)."""
    paras = paragraphs(boxes, heights)
    rects = [rect_of(b) for b in boxes]
    cue_paras: list[list[int]] = []
    hits: list[OtpHit] = []
    used_codes: set[int] = set()
    for para in paras:
        joined = " ".join(texts[i] for i in para if texts[i])
        if not joined:
            continue
        cue_box = [
            i for i in para if texts[i] and (CUE_RE.search(texts[i]) or LEADING_CODE_RE.search(texts[i]))
        ]
        cue_joined = bool(CUE_RE.search(joined) or LEADING_CODE_RE.search(joined))
        if not cue_box and not cue_joined:
            continue
        cue_paras.append(para)
        code_boxes = [i for i in para if texts[i] and has_code(texts[i]) and confs[i] >= min_conf]
        if code_boxes:
            for c in code_boxes:
                same = c in cue_box
                hits.append(
                    OtpHit(
                        tuple(para),
                        c,
                        CONF_SAME_BOX if same else CONF_PARAGRAPH,
                        "otp_same_box" if same else "otp_paragraph",
                    )
                )
                used_codes.add(c)
    # OTP entry screens: the cue is a heading, the code a box of its own below it
    for para in cue_paras:
        if any(h.cue_idx == tuple(para) for h in hits):
            continue
        px1, _py1, px2, py2 = (
            min(rects[i][0] for i in para),
            min(rects[i][1] for i in para),
            max(rects[i][2] for i in para),
            max(rects[i][3] for i in para),
        )
        h = max(heights[i] for i in para)
        best: tuple[float, int] | None = None
        for j in range(len(boxes)):
            if j in para or not texts[j] or not is_code_only_box(texts[j]):
                continue
            x1, y1, x2, y2 = rects[j]
            if y1 < py2 - 0.5 * h or y1 - py2 > NEAR_CUE_H * h:
                continue
            if x2 < px1 - 2 * h or x1 > px2 + 2 * h:
                continue
            d = y1 - py2
            if best is None or d < best[0]:
                best = (d, j)
        if best is not None:
            hits.append(OtpHit(tuple(para), best[1], CONF_NEAR, "otp_near_code"))
        elif cue_only and any(STRONG_CUE_RE.search(texts[i]) for i in para if texts[i]):
            hits.append(OtpHit(tuple(para), None, CONF_CUE_ONLY, "otp_cue_only"))
    return hits


# ------------------------------------------------------------------------------------------------ geometry
def _pad_rect(r: tuple[float, float, float, float], p: float) -> np.ndarray:
    x1, y1, x2, y2 = r
    return np.array([[x1 - p, y1 - p], [x2 + p, y1 - p], [x2 + p, y2 + p], [x1 - p, y2 + p]], np.float32)


def text_rect(boxes: list[np.ndarray], idx: tuple[int, ...]) -> tuple[float, float, float, float]:
    rs = [rect_of(boxes[i]) for i in idx]
    return min(r[0] for r in rs), min(r[1] for r in rs), max(r[2] for r in rs), max(r[3] for r in rs)


def banner_quad(image: np.ndarray, rect: tuple[float, float, float, float], h: float) -> np.ndarray:
    """The flat-colour banner around a text rectangle, else the rectangle padded by 0.6 text heights."""
    fallback = _pad_rect(rect, 0.6 * h)
    ih, iw = image.shape[:2]
    sx1, sy1, sx2, sy2 = rect
    sw = sx2 - sx1
    rx1, ry1 = int(max(0, sx1 - max(1.0 * sw, 6 * h))), int(max(0, sy1 - max(4.0 * h, 1.2 * (sy2 - sy1))))
    rx2, ry2 = int(min(iw, sx2 + max(1.0 * sw, 6 * h))), int(min(ih, sy2 + max(4.0 * h, 1.2 * (sy2 - sy1))))
    if rx2 - rx1 < 20 or ry2 - ry1 < 20:
        return fallback
    roi = image[ry1:ry2, rx1:rx2]
    local = np.array([sx1 - rx1, sy1 - ry1, sx2 - rx1, sy2 - ry1])
    pad = max(3, int(0.3 * h))

    def box_mask(p: int) -> np.ndarray:
        m = np.zeros(roi.shape[:2], np.uint8)
        x1, y1 = max(0, int(local[0]) - p), max(0, int(local[1]) - p)
        x2, y2 = min(roi.shape[1], int(local[2]) + p), min(roi.shape[0], int(local[3]) + p)
        m[y1:y2, x1:x2] = 255
        return m

    ring = (box_mask(2 * pad) > 0) & (box_mask(pad) == 0)
    if int(ring.sum()) < 20:
        return fallback
    bg = np.median(roi[ring], axis=0)
    same = (np.abs(roi.astype(np.int16) - bg.astype(np.int16)).max(axis=2) <= BANNER_TOL).astype(
        np.uint8
    ) * 255
    same[box_mask(pad) > 0] = 255
    k = max(3, int(0.45 * h) | 1)
    same = cv2.morphologyEx(same, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    n, labels = cv2.connectedComponents(same)
    cx, cy = int((local[0] + local[2]) / 2), int((local[1] + local[3]) / 2)
    comp = labels[min(max(cy, 0), roi.shape[0] - 1), min(max(cx, 0), roi.shape[1] - 1)]
    if comp == 0:
        return fallback
    mask = (labels == comp).astype(np.uint8)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return fallback
    cnt = max(cnts, key=cv2.contourArea)
    x, y, w, hh = cv2.boundingRect(cnt)
    touching = int(x <= 0) + int(y <= 0) + int(x + w >= roi.shape[1]) + int(y + hh >= roi.shape[0])
    if touching >= 3 or w * hh >= 0.9 * roi.shape[0] * roi.shape[1]:
        return fallback  # the colour leaks into the rest of the screen: no distinct banner
    box = cv2.boxPoints(cv2.minAreaRect(cnt)).astype(np.float32) + np.array([rx1, ry1], np.float32)
    out = order_quad(box)
    return out if poly_area(out) >= poly_area(fallback) * 0.9 else fallback


def phone_screen_quad(
    image: np.ndarray,
    rect: tuple[float, float, float, float],
    boxes: list[np.ndarray],
    min_boxes: int,
    work_width: int = 640,
) -> np.ndarray | None:
    """A phone-screen rectangle (aspect near 19.5:9, either orientation) around ``rect`` with at least
    ``min_boxes`` OCR text boxes inside. The tightest such rectangle; None when there is none."""
    ih, iw = image.shape[:2]
    s = min(1.0, work_width / float(iw))
    small = cv2.resize(image, (int(iw * s), int(ih * s)), interpolation=cv2.INTER_AREA) if s < 1.0 else image
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    edges = cv2.Canny(gray, 30, 90)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)  # straight edges stay long
    corners = _pad_rect(rect, 0.0) * s
    centres = np.array([b.mean(axis=0) for b in boxes], np.float32) * s if boxes else np.zeros((0, 2))
    text_area = max(poly_area(corners), 1.0)
    frame_area = float(small.shape[0] * small.shape[1])
    best: tuple[float, np.ndarray] | None = None
    for cnt in cnts:
        if len(cnt) < 20:
            continue
        hull = cv2.convexHull(cnt)
        rr = cv2.minAreaRect(hull)
        area = float(rr[1][0] * rr[1][1])
        if not 3.0 * text_area <= area <= 0.85 * frame_area:
            continue
        lo, sh = max(rr[1]), min(rr[1])
        if sh <= 0 or not SCREEN_ASPECT[0] <= lo / sh <= SCREEN_ASPECT[1]:
            continue
        if float(cv2.contourArea(hull)) / area < SCREEN_RECT_FILL:
            continue
        box = cv2.boxPoints(rr).astype(np.float32)
        poly = box.reshape(-1, 1, 2)
        if any(cv2.pointPolygonTest(poly, (float(x), float(y)), True) < -2.0 for x, y in corners):
            continue
        inside = sum(cv2.pointPolygonTest(poly, (float(x), float(y)), False) >= 0 for x, y in centres)
        if inside < min_boxes:
            continue
        if best is None or area < best[0]:
            best = (area, box)
    if best is None:
        return None
    return order_quad(best[1] / s)


def side_ratio(q: np.ndarray) -> float:
    lo, sh = side_lengths(q)
    return lo / sh if sh > 0 else 0.0
