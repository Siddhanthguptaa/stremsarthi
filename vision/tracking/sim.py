"""Synthetic trajectories and a stand-in for the engine's mask selection, for tracking tests and the eval harness.

Everything is deterministic (seeded) and CPU only. No images: targets are boxes with a known path, detections
are the boxes plus noise, and "coverage" is geometry. Two views of what the engine would paint on frame ``i``:

* ``engine`` (``docs/specs/engine.md`` 5.2 and ``engine/deadline.py``): the requests of every lane result whose
  frame is within the lane's maximum age of frame ``i`` (before or after it, the delay buffer makes later
  results available), the request of each ``request_id`` whose ``frame_pts`` is nearest to ``pts_i`` wins.
  Global camera motion is not simulated (it is zero for these targets).
* ``causal``: only results at or before frame ``i`` and requests whose ``[pts_start, pts_end]`` covers it
  (what a lane could guarantee without the delay buffer).
"""

from __future__ import annotations

import random
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from contracts import Category, Config, Detection, Lane, MaskRequest, Quad

Box = tuple[float, float, float, float]
Path = Callable[[float], tuple[float, float]]  # pts -> (cx, cy)


@dataclass(frozen=True, slots=True)
class Target:
    """A box of ``size`` whose centre follows ``path`` for ``t_from <= pts <= t_to``."""

    path: Path
    size: tuple[float, float] = (300.0, 200.0)
    category: Category = Category.AADHAAR
    detector: str = "cards"
    confidence: float = 0.9
    t_from: float = 0.0
    t_to: float = float("inf")

    def present(self, pts: float) -> bool:
        return self.t_from - 1e-9 <= pts <= self.t_to + 1e-9

    def box(self, pts: float) -> Box:
        cx, cy = self.path(pts)
        w, h = self.size
        return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def static(cx: float, cy: float) -> Path:
    return lambda _t: (cx, cy)


def linear(x0: float, y0: float, vx: float, vy: float) -> Path:
    return lambda t: (x0 + vx * t, y0 + vy * t)


@dataclass(slots=True)
class LaneSim:
    fps: int
    max_age_frames: int
    n_frames: int
    runs: list[tuple[int, float, tuple[MaskRequest, ...]]] = field(default_factory=list)  # seq, pts, requests
    truth: list[list[Box | None]] = field(default_factory=list)  # per frame, per target
    seqs: list[int] = field(default_factory=list)  # frame seq of each run (ascending)


def run_lane(
    planner,  # MaskPlanner (typed loosely: this module must not import vision.planner)
    config: Config,
    targets: Sequence[Target],
    *,
    lane: Lane = Lane.FAST,
    duration_s: float = 10.0,
    miss_prob: float = 0.0,
    noise_px: float = 0.0,
    seed: int = 7,
    detect_hz: float | None = None,
) -> LaneSim:
    """Run ``planner`` over a synthetic stream. A lane run happens at ``detect_hz`` (default: ``fps/stride``
    for FAST, ``accurate_hz`` for ACCURATE); each target is detected with probability ``1 - miss_prob``."""
    rng = random.Random(seed)
    fps = int(config.fps)
    if detect_hz is None:
        detect_hz = fps / config.detect.stride if lane is Lane.FAST else config.detect.accurate_hz
    max_age = config.engine.lane_max_age.fast if lane is Lane.FAST else config.engine.lane_max_age.accurate
    n_frames = int(duration_s * fps)
    sim = LaneSim(fps, max_age, n_frames)
    for i in range(n_frames):
        sim.truth.append([tg.box(i / fps) if tg.present(i / fps) else None for tg in targets])
    k = 0
    while True:
        seq = round(k * fps / detect_hz)
        k += 1
        if seq >= n_frames:
            break
        pts = seq / fps
        dets: list[Detection] = []
        for tg in targets:
            if not tg.present(pts) or rng.random() < miss_prob:
                continue
            x1, y1, x2, y2 = tg.box(pts)
            if noise_px:
                x1, x2 = (v + rng.gauss(0.0, noise_px) for v in (x1, x2))
                y1, y2 = (v + rng.gauss(0.0, noise_px) for v in (y1, y2))
                x1, x2 = min(x1, x2), max(x1, x2)
                y1, y2 = min(y1, y2), max(y1, y2)
            dets.append(
                Detection(
                    tg.detector,
                    tg.category,
                    Quad.from_xyxy(x1, y1, x2, y2),
                    tg.confidence,
                    seq,
                    pts,
                    0.0,
                    extra={"reason": "sim"},
                )
            )
        requests, _aux = planner.plan(pts, dets)
        sim.runs.append((seq, pts, requests))
        sim.seqs.append(seq)
    return sim


def masks_at(sim: LaneSim, i: int, model: str = "engine") -> list[Quad]:
    """The mask quads painted on frame ``i`` (see the module docstring for the two models)."""
    pts = i / sim.fps
    best: dict[str, tuple[float, Quad]] = {}
    if model == "engine":
        lo, hi = bisect_left(sim.seqs, i - sim.max_age_frames), bisect_right(sim.seqs, i + sim.max_age_frames)
    else:  # causal: a request window is at most 12 frames long, a held anchor is older than its run
        lo, hi = bisect_left(sim.seqs, i - 60), bisect_right(sim.seqs, i)
    for _seq, _rpts, reqs in sim.runs[lo:hi]:
        for r in reqs:
            if model == "causal" and not (r.pts_start - 1e-9 <= pts <= r.pts_end + 1e-9):
                continue
            d = abs(r.frame_pts - pts)
            held = best.get(r.request_id)
            if held is None or d < held[0]:
                best[r.request_id] = (d, r.quads[0])
    return [q for _, q in best.values()]


def covered_fraction(box: Box, masks: Sequence[Quad]) -> float:
    """Largest share of ``box`` area that one mask bounding box covers."""
    x1, y1, x2, y2 = box
    area = (x2 - x1) * (y2 - y1)
    if area <= 0:
        return 1.0
    best = 0.0
    for q in masks:
        mx1, my1, mx2, my2 = q.bbox()
        iw = max(0.0, min(x2, mx2) - max(x1, mx1))
        ih = max(0.0, min(y2, my2) - max(y1, my1))
        best = max(best, iw * ih / area)
    return best


def coverage_ratio(
    sim: LaneSim,
    target_index: int = 0,
    model: str = "engine",
    threshold: float = 0.98,
    *,
    sightings: int = 1,
) -> float:
    """Share of the frames where the target is present and at least ``threshold`` of it is under a mask.

    Frames before the ``sightings``-th detected lane run are not scored: nothing can mask an object the
    detector has not found yet (1), and a track needs two sightings before it has a velocity (2). A run counts
    as a sighting when it has a request that is not a held one."""
    seen_runs = 0
    start = sim.n_frames
    for seq, _pts, reqs in sim.runs:
        if any(not r.reason.endswith(":held") for r in reqs):
            seen_runs += 1
            if seen_runs >= sightings:
                start = seq
                break
    seen = ok = 0
    for i in range(start, sim.n_frames):
        box = sim.truth[i][target_index]
        if box is None:
            continue
        seen += 1
        if covered_fraction(box, masks_at(sim, i, model)) >= threshold:
            ok += 1
    return ok / max(1, seen)


def on_series(sim: LaneSim, model: str = "engine") -> list[bool]:
    """Per frame: is any mask painted?"""
    return [bool(masks_at(sim, i, model)) for i in range(sim.n_frames)]


def flicker_events(on: Sequence[bool], fps: int, window_s: float = 1.0) -> int:
    """Number of on -> off -> on transitions where the off run is at most ``window_s`` long."""
    events = 0
    i, n = 0, len(on)
    while i < n:
        if on[i]:
            i += 1
            continue
        j = i
        while j < n and not on[j]:
            j += 1
        if i > 0 and j < n and (j - i) / fps <= window_s + 1e-9:
            events += 1
        i = j
    return events
