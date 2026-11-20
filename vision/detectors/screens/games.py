"""BGMI / Free Fire player IDs and custom-room credentials from OCR labels (spec 5.10, second bullet).

The label route does not depend on fixed positions: a label (``ID:``, ``UID:``, ``ROOM ID``, ``PASSWORD``) in a
frame with game context (``CUSTOM ROOM``, ``SQUAD``, ``LOBBY``, ``SLOT 1``, ``BGMI``, ``FREE FIRE``, ...) marks the box
that holds its value (in the label's own box, to its right or just below it). The value is validated by shape:
player ID 8-12 digits, room ID and password 4-10 letters or digits (lengths UNVERIFIED, spec 5.10). A label whose
value cannot be read is still masked when the category's ``mask_when_unsure`` is on (the box next to a credential
label is a credential). Text lives for one frame only: nothing here logs or stores it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from contracts import Category
from vision.ocr.normalize import digit_ratio, repair_digit_field

CONTEXT_RE = re.compile(
    r"CUSTOM\s*ROOM|ROOM\s*(?:ID|NO|NUMBER|CODE)\b|\bBGMI\b|BATTLEGROUNDS|FREE\s*FIRE|GARENA|\bSQUAD\b|\bLOBBY\b"
    r"|\bSLOT\s*\d\b|\bTEAM\s*UP\b"
)
ROOM_CONTEXT_RE = re.compile(r"CUSTOM\s*ROOM|ROOM\s*(?:ID|NO|NUMBER|CODE)\b|\bROOM\s*(?:SETTINGS|INFO)\b")
SEP = r"\s*[:：.#-]?\s*"
ID_LABEL = re.compile(rf"^(?:PLAYER\s*|USER\s*|CHARACTER\s*)?(?:UID|ID)\b{SEP}(?P<rest>.*)$")
ROOM_LABEL = re.compile(rf"^ROOM\s*(?:ID|NO\.?|NUMBER|CODE)\b{SEP}(?P<rest>.*)$")
PASS_LABEL = re.compile(rf"^(?:ROOM\s*)?(?:PASSWORD|PASSCODE|PASS|PWD)\b{SEP}(?P<rest>.*)$")
PLAYER_ID_RE = re.compile(r"\d{8,12}")
CREDENTIAL_RE = re.compile(r"[A-Z0-9]{4,10}")
CONF_VALID, CONF_UNSURE = 0.9, 0.55
RIGHT_GAP_H, BELOW_GAP_H = 3.0, 2.2  # value box: up to this many text heights right of / below its label


@dataclass(slots=True)
class GameHit:
    category: Category
    idx: int  # the box that holds the value
    conf: float
    reason: str  # game_id | room_id | room_password (valid), *_unsure (unreadable value)
    label_idx: int | None = None


def has_game_context(texts_upper: list[str]) -> bool:
    return any(CONTEXT_RE.search(t) for t in texts_upper if t)


def has_room_context(texts_upper: list[str]) -> bool:
    return any(ROOM_CONTEXT_RE.search(t) for t in texts_upper if t)


def _compact(text: str) -> str:
    return re.sub(r"[\s-]", "", text)


def valid_player_id(value: str) -> bool:
    v = _compact(value)
    if digit_ratio(v) >= 0.85 and not v.isdigit():
        v = repair_digit_field(v)
    return PLAYER_ID_RE.fullmatch(v) is not None


def valid_credential(value: str) -> bool:
    return CREDENTIAL_RE.fullmatch(_compact(value).upper()) is not None


def _label(text: str) -> tuple[str, re.Match[str]] | None:
    # ROOM labels first: "ROOM ID" must not read as a bare "ID"
    for kind, rx in (("room", ROOM_LABEL), ("pass", PASS_LABEL), ("id", ID_LABEL)):
        m = rx.match(text)
        if m:
            return kind, m
    return None


def _value_box(
    rects: list[tuple[float, float, float, float]], heights: list[float], i: int, skip: set[int]
) -> int | None:
    """The box to the right of, or just below, label box ``i`` (nearest first); None when there is none."""
    x1, y1, x2, y2 = rects[i]
    h = max(heights[i], 8.0)
    cy = (y1 + y2) / 2.0
    best: tuple[float, int] | None = None
    for j, (a1, b1, a2, b2) in enumerate(rects):
        if j == i or j in skip:
            continue
        hj = max(heights[j], 8.0)
        if abs((b1 + b2) / 2.0 - cy) <= 0.7 * max(h, hj) and -0.3 * h <= a1 - x2 <= RIGHT_GAP_H * h:
            d = a1 - x2 + 0.1 * abs((b1 + b2) / 2.0 - cy)
        elif (
            0.45 * h <= (b1 + b2) / 2.0 - cy
            and b1 - y2 <= BELOW_GAP_H * h
            and (abs(a1 - x1) <= 2.0 * h or (a1 < x2 and a2 > x1))
        ):
            d = (
                (b1 - y2) + 0.25 * abs(a1 - x1) + 0.5 * h
            )  # a right-hand value wins over one below at equal distance
        else:
            continue
        if best is None or d < best[0]:
            best = (d, j)
    return best[1] if best else None


def find_game_fields(
    boxes: list[np.ndarray],
    texts: list[str],
    heights: list[float],
    confs: list[float],
    *,
    unsure: dict[Category, bool],
    template_context: bool = False,
) -> list[GameHit]:
    """Player IDs, room IDs and passwords found through their labels. ``texts`` are normalised."""
    upper = [t.upper() for t in texts]
    ctx = template_context or has_game_context(upper)
    room_ctx = template_context or has_room_context(upper)
    if not ctx:
        return []
    rects = [
        (float(b[:, 0].min()), float(b[:, 1].min()), float(b[:, 0].max()), float(b[:, 1].max()))
        for b in boxes
    ]
    labels = {i: _label(u) for i, u in enumerate(upper) if u}
    label_idx = {i for i, lab in labels.items() if lab is not None}
    hits: list[GameHit] = []
    used: set[int] = set()
    for i, lab in labels.items():
        if lab is None:
            continue
        kind, m = lab
        category = Category.GAME_ID if kind == "id" else Category.ROOM_CREDENTIAL
        if kind != "id" and not room_ctx:
            continue  # a PASSWORD label outside a custom-room screen is a login form, not a game credential
        rest = m.group("rest").strip()
        ok = valid_player_id if kind == "id" else valid_credential
        reason = {"id": "game_id", "room": "room_id", "pass": "room_password"}[kind]
        if rest:  # the value sits in the label's own box
            if ok(rest) and confs[i] >= 0.5:
                hits.append(GameHit(category, i, CONF_VALID, reason, i))
                used.add(i)
            elif (
                unsure.get(category, False)
                and len(_compact(rest)) >= 5
                and digit_ratio(_compact(rest)) >= 0.5
            ):
                hits.append(GameHit(category, i, CONF_UNSURE, reason + "_unsure", i))
                used.add(i)
            continue
        j = _value_box(rects, heights, i, label_idx | used)
        if j is None:
            continue
        value = upper[j]
        if value and ok(value) and confs[j] >= 0.5:
            hits.append(GameHit(category, j, CONF_VALID, reason, i))
            used.add(j)
        elif unsure.get(category, False) and (not value or len(_compact(value)) <= 14):
            hits.append(GameHit(category, j, CONF_UNSURE, reason + "_unsure", i))
            used.add(j)
    return hits
