"""Run the loaded detectors over the dataset (CPU, no stream) and collect scoring records and lane timings.

Stills: every active lane runs on the image; the FAST lane runs ``fast_still_runs`` times (a static scene seen on a
few consecutive fast-lane runs, so detectors that need two sightings or an identity vote get them); the
planner (``vision.planner.MaskPlanner`` with the IoU hold tracker, the production planner) turns detections into
mask requests exactly as in ``vision.host``.

Clips: the lanes follow the production schedule on the clip's PTS (FAST every ``detect.stride``-th frame, ACCURATE
at most every ``1 / detect.accurate_hz`` s), one fresh ``MaskPlanner`` per lane (ByteTrack with hysteresis), and
``timeline.masks_for_frame`` paints each frame from the nearest results as the deadline engine does.

A lane run in which a detector raises is an ``ok=False`` run (the engine would slate the frame); the number of
such runs is reported and gated. The exception message is never kept.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from contracts import Config, DetectContext, Frame, Lane

from . import dataset
from .detectors import ERROR, DetectorSet
from .frames import Mask, masks_from_requests, score_frame
from .timeline import LaneRun, masks_for_frame

LANE_ORDER = (Lane.FAST, Lane.ACCURATE, Lane.SLOW)


@dataclass
class Timings:
    """Milliseconds per lane run and per detector run."""

    lane_ms: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    detector_ms: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))


@dataclass
class RunStats:
    lane_runs: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    detect_errors: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    detect_calls: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    failed_lane_runs: int = 0
    protect_frames: int = 0
    clip_frames: int = 0
    seconds: float = 0.0


@dataclass
class RunOptions:
    fast_still_runs: int = 4
    only_buckets: set[str] | None = None
    max_per_bucket: int | None = None
    progress: Callable[[str], None] | None = None


class Runner:
    def __init__(self, dset: DetectorSet, config: Config, options: RunOptions | None = None) -> None:
        self.dset = dset
        self.cfg = config
        self.opt = options or RunOptions()
        self.timings = Timings()
        self.stats = RunStats()
        self.records: list[dict[str, Any]] = []

    # -------------------------------------------------------------------- one lane run
    def _planner(self, lane: Lane, tracker: str | None) -> Any:
        from vision.planner import MaskPlanner

        return MaskPlanner(lane, self.cfg, tracker=tracker)  # type: ignore[arg-type]

    def _lane_run(
        self,
        lane: Lane,
        dets: list[Any],
        planner: Any,
        frame: Frame,
        prev: tuple[Any, ...],
        stride: int,
    ) -> tuple[LaneRun, tuple[Any, ...]]:
        ctx = DetectContext(
            lane=lane,
            categories=self.cfg.categories,
            stride=stride,
            previous=prev,
            deadline=None,
            allowlist=getattr(self.dset.resources, "allowlist", None),
        )
        t_lane = time.perf_counter()
        detections: list[Any] = []
        ok = True
        for det in dets:
            t0 = time.perf_counter()
            self.stats.detect_calls[det.id] += 1
            try:
                detections.extend(det.detect(frame, ctx))
            except Exception as exc:  # noqa: BLE001 - counted, type only
                self.stats.detect_errors[det.id] += 1
                ok = False
                self.dset.status[det.id].detail = type(exc).__name__
            finally:
                self.timings.detector_ms[det.id].append((time.perf_counter() - t0) * 1000.0)
        masks: tuple[Mask, ...] = ()
        valid: tuple[tuple[float, float], ...] = ()
        if ok:
            try:
                requests, _aux = planner.plan(frame.pts, tuple(detections))
                ms: list[Mask] = []
                va: list[tuple[float, float]] = []
                for r in requests:
                    new = masks_from_requests([r])
                    ms.extend(new)
                    va.extend([(r.pts_start, r.pts_end)] * len(new))
                masks, valid = tuple(ms), tuple(va)
            except Exception:  # noqa: BLE001 - a planner failure is a failed lane run too
                ok = False
        self.timings.lane_ms[lane.value].append((time.perf_counter() - t_lane) * 1000.0)
        self.stats.lane_runs[lane.value] += 1
        if not ok:
            self.stats.failed_lane_runs += 1
        return LaneRun(frame.seq, frame.pts, ok, masks, valid), tuple(detections) if ok else ()

    # -------------------------------------------------------------------- stills
    def run_image(self, item: dataset.ImageItem) -> list[dict[str, Any]]:
        img = dataset.read_image_bgr(item.path)
        masks: list[Mask] = []
        protect = False
        stride = self.cfg.detect.stride
        for lane, dets in sorted(self.dset.by_lane().items(), key=lambda kv: LANE_ORDER.index(kv[0])):
            planner = self._planner(lane, "iou")
            runs = self.opt.fast_still_runs if lane is Lane.FAST else 1
            prev: tuple[Any, ...] = ()
            last: LaneRun | None = None
            for r in range(runs):
                frame = Frame(r * stride, r * stride / float(self.cfg.fps), time.monotonic(), img)
                last, prev = self._lane_run(lane, dets, planner, frame, prev, stride)
            assert last is not None
            if not last.ok:
                protect = True
            else:
                masks.extend(last.masks)
        if protect:
            self.stats.protect_frames += 1
        ft = dataset.frame_truth_from_image(item.truth)
        return score_frame(
            ft, masks, src="img", item=item.id, bucket=item.bucket, level=item.level, protect=protect
        )

    # -------------------------------------------------------------------- clips
    def _reset_fast(self) -> None:
        """Stateful FAST detectors (face tracking, identity votes) start each clip clean."""
        from contracts import DetectorConfig, Device

        for ident, det in self.dset.detectors.items():
            if type(det).lane is Lane.FAST and self.dset.status[ident].runs:
                try:
                    det.configure(
                        DetectorConfig(
                            categories=self.cfg.categories,
                            device=Device.CPU,
                            params=self.dset.params.get(ident, {}),
                        ),
                        self.dset.resources,
                    )
                except Exception:  # noqa: BLE001 - keep the previous state rather than abort the run
                    pass

    def run_clip(self, clip: dataset.ClipItem) -> list[dict[str, Any]]:
        fps = float(self.cfg.fps)
        stride = self.cfg.detect.stride
        period = 1.0 / float(self.cfg.detect.accurate_hz)
        by_lane = sorted(self.dset.by_lane().items(), key=lambda kv: LANE_ORDER.index(kv[0]))
        self._reset_fast()
        planners = {lane: self._planner(lane, None) for lane, _ in by_lane}
        runs: dict[str, list[LaneRun]] = {lane.value: [] for lane, _ in by_lane}
        prev: dict[Lane, tuple[Any, ...]] = {lane: () for lane, _ in by_lane}
        next_acc = {lane: 0.0 for lane, _ in by_lane}
        n = 0
        for k, img in enumerate(dataset.iter_clip_frames(clip.path)):
            if k >= len(clip.frames):
                break
            n = k + 1
            pts = k / fps
            frame = Frame(k, pts, time.monotonic(), img)
            for lane, dets in by_lane:
                if lane is Lane.FAST:
                    if k % stride:
                        continue
                else:
                    if pts + 1e-9 < next_acc[lane]:
                        continue
                    next_acc[lane] = pts + (period if lane is Lane.ACCURATE else 1.0)
                run, prev[lane] = self._lane_run(lane, dets, planners[lane], frame, prev[lane], stride)
                runs[lane.value].append(run)
        max_age = {
            Lane.FAST.value: self.cfg.engine.lane_max_age.fast,
            Lane.ACCURATE.value: self.cfg.engine.lane_max_age.accurate,
            Lane.SLOW.value: self.cfg.engine.lane_max_age.slow,
        }
        win = self.cfg.engine.window
        out: list[dict[str, Any]] = []
        for k in range(n):
            ft = clip.frames[k]
            pts = k / fps
            masks, protect = masks_for_frame(runs, k, pts, max_age, win.before, win.after)
            if protect:
                self.stats.protect_frames += 1
            self.stats.clip_frames += 1
            out.extend(
                score_frame(
                    ft,
                    masks,
                    src="clip",
                    item=clip.id,
                    bucket=clip.bucket,
                    level=clip.level,
                    frame=k,
                    t=pts,
                    protect=protect,
                    motion=clip.motion,
                )  # fmt: skip
            )
        return out

    # -------------------------------------------------------------------- everything
    def run(self, ds: dataset.Dataset) -> list[dict[str, Any]]:
        t0 = time.monotonic()
        say = self.opt.progress or (lambda s: None)
        for i, item in enumerate(ds.images):
            self.records.extend(self.run_image(item))
            if (i + 1) % 50 == 0:
                say(f"  stills {i + 1}/{len(ds.images)}")
        for i, clip in enumerate(ds.clips):
            self.records.extend(self.run_clip(clip))
            say(f"  clips {i + 1}/{len(ds.clips)} ({clip.id})")
        self.stats.seconds = time.monotonic() - t0
        for ident, calls in self.stats.detect_calls.items():
            if calls and self.stats.detect_errors.get(ident, 0) == calls:
                self.dset.mark_error(ident, self.dset.status[ident].detail or "every detect call raised")
        return self.records


def errored_detectors(dset: DetectorSet) -> list[str]:
    return sorted(i for i, s in dset.status.items() if s.state == ERROR)
