"""Push one red-team case through a mask source and score every delivered frame.

A *mask source* turns the frames of a case into (masks, protect) per frame:

* ``DetectorSource``  the real detectors and planner, on the production lane schedule (same code path as
  ``eval.phase2.runner.Runner.run_clip``, with in-memory frames so dropped frames can be skipped).
* ``NullSource`` / ``OracleSource`` / ``ShiftedSource`` / ``PartialSource``  test doubles: a seeded broken detector
  (reports nothing), a perfect one, one whose masks are displaced, one that covers only part of each target. The unit
  tests prove the scoring flags the broken ones.

Scoring uses ``eval.phase2.frames.score_frame`` (the Gate 2 coverage proxy: share of the target quad under any mask,
``COVER_MIN`` = 0.90) and adds the output read-back of ``readback.py``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import numpy as np

from ...phase2.frames import TRUTH_MASK_CATS, Mask, score_frame
from ...phase2.rows import COVER_MIN
from . import readback
from .model import Case, Frame

READBACK_STRIDE = 3


class MaskSource(Protocol):
    def run(self, frames: Sequence[Frame]) -> list[tuple[list[Mask], bool]]: ...


# ---------------------------------------------------------------------------------------------- sources
class DetectorSource:
    """The production chain: lanes on the clip schedule, one planner per lane, nearest-result painting."""

    def __init__(self, dset: Any, cfg: Any) -> None:
        from ...phase2.runner import Runner, RunOptions

        self.runner = Runner(dset, cfg, RunOptions())
        self.cfg = cfg

    def run(self, frames: Sequence[Frame]) -> list[tuple[list[Mask], bool]]:
        from contracts import Frame as CFrame
        from contracts import Lane

        from ...phase2.runner import LANE_ORDER
        from ...phase2.timeline import LaneRun, masks_for_frame

        r, cfg = self.runner, self.cfg
        fps, stride = float(cfg.fps), cfg.detect.stride
        period = 1.0 / float(cfg.detect.accurate_hz)
        by_lane = sorted(r.dset.by_lane().items(), key=lambda kv: LANE_ORDER.index(kv[0]))
        r._reset_fast()
        planners = {lane: r._planner(lane, None) for lane, _ in by_lane}
        runs: dict[str, list[LaneRun]] = {lane.value: [] for lane, _ in by_lane}
        prev: dict[Any, tuple[Any, ...]] = {lane: () for lane, _ in by_lane}
        next_acc = {lane: 0.0 for lane, _ in by_lane}
        for k, f in enumerate(frames):
            if f.dropped:
                continue  # never delivered: no lane can run on it
            pts = k / fps
            cf = CFrame(k, pts, time.monotonic(), f.image)
            for lane, dets in by_lane:
                if lane is Lane.FAST:
                    if k % stride:
                        continue
                else:
                    if pts + 1e-9 < next_acc[lane]:
                        continue
                    next_acc[lane] = pts + (period if lane is Lane.ACCURATE else 1.0)
                run, prev[lane] = r._lane_run(lane, dets, planners[lane], cf, prev[lane], stride)
                runs[lane.value].append(run)
        max_age = {
            Lane.FAST.value: cfg.engine.lane_max_age.fast,
            Lane.ACCURATE.value: cfg.engine.lane_max_age.accurate,
            Lane.SLOW.value: cfg.engine.lane_max_age.slow,
        }
        win = cfg.engine.window
        out: list[tuple[list[Mask], bool]] = []
        for k, _ in enumerate(frames):
            masks, protect = masks_for_frame(runs, k, k / fps, max_age, win.before, win.after)
            out.append((masks, protect))
        return out


def _truth_mask_category(cat: str) -> str | None:
    cats = TRUTH_MASK_CATS.get(cat)
    return sorted(cats)[0] if cats else None


def _target_quads(f: Frame) -> list[tuple[str, list[list[float]]]]:
    out = []
    for t in f.truth["targets"]:
        mc = _truth_mask_category(t["category"])
        if mc and not t.get("aux") and t.get("visible"):
            out.append((mc, t["quad"]))
    return out


class NullSource:
    """Seeded defect: a detector that loads, runs and never reports anything."""

    def run(self, frames: Sequence[Frame]) -> list[tuple[list[Mask], bool]]:
        return [([], False) for _ in frames]


class OracleSource:
    """A perfect detector: masks exactly the exposed targets (grown by ``pad`` px)."""

    def __init__(self, pad: float = 6.0) -> None:
        self.pad = pad

    def _grow(self, quad: list[list[float]]) -> tuple[tuple[float, float], ...]:
        a = np.asarray(quad, np.float64)
        c = a.mean(axis=0)
        d = a - c
        n = np.linalg.norm(d, axis=1, keepdims=True)
        g = c + d * (n + self.pad) / np.maximum(n, 1e-6)
        return tuple((float(x), float(y)) for x, y in g)

    def run(self, frames: Sequence[Frame]) -> list[tuple[list[Mask], bool]]:
        return [([Mask(c, self._grow(q), "oracle") for c, q in _target_quads(f)], False) for f in frames]


class ShiftedSource(OracleSource):
    """Seeded defect: the right masks, displaced by a fraction of the target width (a stale or mis-projected mask)."""

    def __init__(self, shift: float = 0.8) -> None:
        super().__init__(0.0)
        self.shift = shift

    def run(self, frames: Sequence[Frame]) -> list[tuple[list[Mask], bool]]:
        out = []
        for f in frames:
            ms = []
            for c, q in _target_quads(f):
                a = np.asarray(q, np.float64)
                dx = self.shift * (a[:, 0].max() - a[:, 0].min())
                ms.append(Mask(c, tuple((float(x + dx), float(y)) for x, y in a), "shifted"))
            out.append((ms, False))
        return out


class PartialSource(OracleSource):
    """Seeded defect: masks only the left ``frac`` of each target (a clipped box)."""

    def __init__(self, frac: float = 0.5) -> None:
        super().__init__(0.0)
        self.frac = frac

    def run(self, frames: Sequence[Frame]) -> list[tuple[list[Mask], bool]]:
        out = []
        for f in frames:
            ms = []
            for c, q in _target_quads(f):
                a = np.asarray(q, np.float64)
                x1, x2 = a[:, 0].min(), a[:, 0].max()
                y1, y2 = a[:, 1].min(), a[:, 1].max()
                xm = x1 + self.frac * (x2 - x1)
                ms.append(Mask(c, ((x1, y1), (xm, y1), (xm, y2), (x1, y2)), "partial"))
            out.append((ms, False))
        return out


# ---------------------------------------------------------------------------------------------- scoring
def run_case(
    case: Case,
    source: MaskSource,
    reader: readback.Reader | None,
    styles: dict[str, Any] | None = None,
    *,
    cover_min: float = COVER_MIN,
    stride: int = READBACK_STRIDE,
    frames: list[Frame] | None = None,
    on_frame: Callable[[int, Frame, list[Mask], bool, list[dict[str, Any]]], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Per exposed-target-frame records plus case info (frames delivered/dropped, protect frames, seconds).

    Record fields: ``case attack group param frame j cat tag cov cov_cat proxy`` (proxy leak: coverage below
    ``cover_min``) and, where read-back ran, ``ro`` in {``confirmed``, ``clean``, ``src_unreadable``,
    ``src_readable``}: ``confirmed`` = the target is recoverable from the OUTPUT frame; ``src_unreadable`` = proxy leak
    but the target is not recoverable even from the unmasked input (the attack destroyed the legibility);
    ``src_readable`` = proxy leak, readable in the input, not recoverable from the output.
    """
    t0 = time.perf_counter()
    frames = frames if frames is not None else case.build()
    results = source.run(frames)
    recs: list[dict[str, Any]] = []
    info = {"frames": len(frames), "dropped": 0, "protect": 0, "scored_frames": 0}
    for k, (f, (masks, protect)) in enumerate(zip(frames, results, strict=True)):
        if f.dropped:
            info["dropped"] += 1
            continue
        info["protect"] += 1 if protect else 0
        rows = [
            r
            for r in score_frame(
                f.truth,
                masks,
                src="clip",
                item=case.id,
                bucket=case.attack,
                level=case.group,
                frame=k,
                t=k / 30.0,
                protect=protect,
            )
            if r["k"] == "t"
        ]
        if rows:
            info["scored_frames"] += 1
        painted: np.ndarray | None = None
        for r in rows:
            r.update(case=case.id, attack=case.attack, group=case.group, param=case.param, tag=f.tag)
            r["proxy"] = bool(r["cov"] < cover_min)
            exp = f.expect.get(r["j"])
            recs.append(r)
            if reader is None or exp is None or exp.kind == "none" or k % stride:
                continue
            quad = next(t["quad"] for t in f.truth["targets"] if t["j"] == r["j"])
            if protect:
                r["ro"] = "clean"
                continue
            if painted is None:
                painted = readback.paint_output(f.image, masks, False, styles)
            got = reader.recovers(painted, quad, exp)
            if got:
                r["ro"] = "confirmed"
            elif not r["proxy"]:
                r["ro"] = "clean"
            else:
                r["ro"] = "src_readable" if reader.recovers(f.image, quad, exp) else "src_unreadable"
        if on_frame is not None and rows:
            on_frame(k, f, list(masks), protect, rows)
    info["seconds"] = round(time.perf_counter() - t0, 2)
    return recs, info
