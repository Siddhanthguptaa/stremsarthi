"""Hysteresis on tracks, and the per-track vote buffer.

Idea from PrivaStream ``Hysteresis`` (``reference/privastream/src/models/detection/pii_blur/pii_detector.py``,
Apache-2.0, ``vision/LICENSE.privastream``): K hits to activate, K frames to hold, drop old state. What
changed: it keys on ``track_id`` from ``bytetrack.ByteTracker`` instead of greedy box IoU, counts time in
seconds of source PTS (lanes run at different rates) instead of frames, and is biased toward masking:

* a track is confirmed after ``k_confirm`` hits, but at once (one hit) when the detection confidence is at
  least ``confirm_conf`` or the category has ``mask_when_unsure`` on (cards, QR, faces and anything the
  streamer set to "mask when unsure");
* once confirmed, a track that stops being detected is still emitted (``held=True``) for the category's hold
  time, with its last detection and its last velocity, so the planner can project it forward;
* state is garbage collected after ``gc_factor`` times the hold time (3x, spec 5.4).

``TrackVotes`` is the per-track vote window the faces detector uses for the allowlist (spec 5.5: a track
needs at least ``face_vote_min`` of the last ``face_vote_window`` samples to match before it may stay
visible; a stranger beside an enrolled person never inherits the neighbour's votes because every track has
its own buffer; three non-matching samples in a row revoke the allowance). Nothing here keeps pixels, text
or embeddings.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from contracts import Category, CategoryConfig, Config, Detection

# Hold after the last sighting, seconds (spec 5.4).
HOLD_S: Mapping[Category, float] = {
    Category.FACE: 0.8,
    Category.AADHAAR: 2.0,
    Category.PAN: 2.0,
    Category.UPI_QR: 2.0,
}
DEFAULT_HOLD_S = 1.0  # text categories and everything else
CONFIRM_CONF = 0.8  # at or above this a track is confirmed on its first hit
K_CONFIRM = 2
GC_FACTOR = 3.0
_BACKJUMP_S = 1.0
_EPS = 1e-9


@dataclass(frozen=True, slots=True)
class TrackedDetection:
    """A detection with a stable ``track_id``. ``held`` means the object was not seen on this run;
    ``velocity`` is the track's image-plane velocity in px/s (0, 0 until it has two sightings)."""

    detection: Detection
    last_seen_pts: float
    held: bool
    track_id: int | None = None
    velocity: tuple[float, float] = (0.0, 0.0)


@dataclass(frozen=True, slots=True)
class Observation:
    """One track that was matched or created on this run (input of ``TrackHysteresis.update``)."""

    track_id: int
    detection: Detection
    velocity: tuple[float, float] = (0.0, 0.0)


@dataclass(slots=True)
class _Entry:
    track_id: int
    detection: Detection
    velocity: tuple[float, float]
    last_pts: float
    hits: int = 0
    confirmed: bool = False


class TrackHysteresis:
    """K-confirm, hold and garbage collection over track ids."""

    def __init__(
        self,
        categories: Mapping[Category, CategoryConfig],
        *,
        hold_s: Mapping[Category, float] | None = None,
        default_hold_s: float = DEFAULT_HOLD_S,
        k_confirm: int = K_CONFIRM,
        confirm_conf: float = CONFIRM_CONF,
        gc_factor: float = GC_FACTOR,
    ) -> None:
        if k_confirm < 1:
            raise ValueError("k_confirm must be >= 1")
        if gc_factor < 1.0:
            raise ValueError("gc_factor must be >= 1 (state must outlive the hold)")
        self._categories = categories
        self._hold = dict(HOLD_S if hold_s is None else hold_s)
        self._default_hold = default_hold_s
        self._k = k_confirm
        self._confirm_conf = confirm_conf
        self._gc = gc_factor
        self._entries: dict[int, _Entry] = {}
        self._last_pts = -math.inf

    # ------------------------------------------------------------------ config
    def hold_for(self, category: Category) -> float:
        return self._hold.get(category, self._default_hold)

    def hold_frames(self, category: Category, frame_rate: float) -> int:
        """The hold expressed in runs of a lane at ``frame_rate`` Hz (rounded up)."""
        return math.ceil(self.hold_for(category) * frame_rate - _EPS)

    @property
    def max_hold_s(self) -> float:
        return max([self._default_hold, *self._hold.values()])

    def _confirm_now(self, det: Detection) -> bool:
        cfg = self._categories.get(det.category)
        return det.confidence >= self._confirm_conf or bool(cfg is not None and cfg.mask_when_unsure)

    # ------------------------------------------------------------------ state
    def reset(self) -> None:
        self._entries.clear()
        self._last_pts = -math.inf

    def forget_category(self, category: Category) -> None:
        """Drop every track (held ones included) of ``category``: a switched-off category stops masking at once."""
        for tid in [t for t, e in self._entries.items() if e.detection.category is category]:
            del self._entries[tid]

    def __len__(self) -> int:
        return len(self._entries)

    def update(self, observations: Sequence[Observation], pts: float) -> list[TrackedDetection]:
        """Feed the tracks seen on this run; get every confirmed track that must still be masked."""
        if pts < self._last_pts - _BACKJUMP_S:  # the source clock restarted: old tracks are meaningless
            self._entries.clear()
        self._last_pts = max(self._last_pts, pts)
        for ob in observations:
            e = self._entries.get(ob.track_id)
            if e is None:
                e = self._entries[ob.track_id] = _Entry(ob.track_id, ob.detection, ob.velocity, pts)
            e.hits += 1
            e.last_pts = pts
            e.detection = ob.detection
            e.velocity = ob.velocity
            if not e.confirmed and (e.hits >= self._k or self._confirm_now(ob.detection)):
                e.confirmed = True
        out: list[TrackedDetection] = []
        for tid, e in list(self._entries.items()):
            age = pts - e.last_pts
            hold = self.hold_for(e.detection.category)
            if age > self._gc * hold + _EPS:
                del self._entries[tid]
                continue
            if e.confirmed and age <= hold + _EPS:
                out.append(TrackedDetection(e.detection, e.last_pts, age > _EPS, tid, e.velocity))
        return out


class TrackVotes:
    """Per-track vote window. ``add(track_id, matched)`` after each identity check; ``allowed(track_id)``
    is True only while at least ``min_votes`` of the last ``window`` samples matched, the track has at least
    ``min_samples`` samples, and the last ``flip_run`` samples were not all misses. Unknown tracks are not
    allowed (a new or unconfirmed track is masked)."""

    def __init__(
        self, window: int = 7, min_votes: int = 5, *, min_samples: int = 3, flip_run: int = 3
    ) -> None:
        if window < 1 or min_votes < 1 or min_samples < 1 or flip_run < 1:
            raise ValueError("window, min_votes, min_samples and flip_run must be >= 1")
        self.window = window
        self.min_votes = min(min_votes, window)  # a window shorter than the minimum could never allow
        self.min_samples = min_samples
        self.flip_run = flip_run
        self._votes: dict[int, deque[bool]] = {}

    @classmethod
    def from_config(cls, config: Config) -> TrackVotes:
        return cls(config.vision.face_vote_window, config.vision.face_vote_min)

    def add(self, track_id: int, matched: bool) -> None:
        self._votes.setdefault(track_id, deque(maxlen=self.window)).append(bool(matched))

    def samples(self, track_id: int) -> int:
        return len(self._votes.get(track_id, ()))

    def matches(self, track_id: int) -> int:
        return sum(self._votes.get(track_id, ()))

    def allowed(self, track_id: int) -> bool:
        v = self._votes.get(track_id)
        if not v or len(v) < self.min_samples:
            return False
        if len(v) >= self.flip_run and not any(list(v)[-self.flip_run :]):
            return False
        return sum(v) >= self.min_votes

    def forget(self, track_id: int) -> None:
        self._votes.pop(track_id, None)

    def prune(self, keep: Iterable[int]) -> None:
        """Drop the buffers of every track not in ``keep`` (call with the live track ids)."""
        alive = set(keep)
        for tid in [t for t in self._votes if t not in alive]:
            del self._votes[tid]

    def reset(self) -> None:
        self._votes.clear()

    def __len__(self) -> int:
        return len(self._votes)
