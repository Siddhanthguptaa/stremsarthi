"""Data model of the video red team: one ``Case`` = one adversarial clip (a list of frames with ground truth)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# A target counts as "exposed" for scoring when enough of it is in the frame to be read. This is deliberately
# stricter than the dataset rule of docs/specs/eval.md 5.2 (>= 60 % inside, >= 24 px): an attacker does not wait
# for 60 % of the card to be on screen. Both numbers are recorded in each report.
EXPOSED_MIN_IN_FRAME = 0.25
EXPOSED_MIN_SIDE_PX = 16.0
SPEC_MIN_IN_FRAME = 0.60
SPEC_MIN_SIDE_PX = 24.0


@dataclass(frozen=True)
class Expect:
    """What a reader must NOT be able to recover from the output frame inside a target's quad.

    ``kind`` is ``qr`` (a QR decoder returns one of ``values``), ``text`` (OCR text contains a long enough run of
    one of ``values`` after normalisation) or ``none`` (no read-back check, proxy only: faces).
    """

    kind: str
    values: tuple[str, ...] = ()


NO_READBACK = Expect("none")


@dataclass
class Frame:
    """One captured frame. ``image`` is BGR uint8 1280x720. ``truth`` is the phase-2 frame truth
    (``targets``: category, quad, visible, j, aux, ...). ``expect`` maps a target's ``j`` to its read-back check."""

    image: np.ndarray
    truth: dict[str, Any]
    expect: dict[int, Expect] = field(default_factory=dict)
    dropped: bool = False  # captured but never delivered to the engine (frame-drop attacks)
    tag: str = ""  # e.g. "cut+2" (2 frames after a scene cut) or "steady"


@dataclass
class Case:
    id: str
    attack: str
    group: str
    param: str  # human readable parameter label, unique within the attack
    params: dict[str, Any]
    seed: int
    build: Callable[[], list[Frame]]
    notes: str = ""

    @property
    def key(self) -> str:
        return f"{self.attack}[{self.param}]"
