"""``LaneHost``: ``vision.host:build(config, bus)`` (T0.5 core, T1.3 planner wiring).

Discovers detector plugins, checks the VRAM budget, builds one ``LaneWorker`` per active lane and hands
the engine one ``LaneResult`` per lane run through ``poll()``.

Active lanes: the lanes that have at least one enabled detector. With no detector at all (as in Phase 0)
both FAST and ACCURATE run empty and still emit ``ok=True`` heartbeats, because the engine's fail-safe
needs "ran and found nothing" to tell it from "lane is late".

The mask planner and tracker plug in through ``assembler_factory`` (called once per lane). ``build`` wires
``vision.planner.planner_factory`` (T1.3: ``MaskPlanner`` with the IoU-hold tracker; T2.3 swaps in ByteTrack
through the planner's ``Tracker`` hook); a bare ``VisionHost`` without a factory still emits no requests.
Detections are published on ``Topic.DETECTION`` (codes and quads only). The shared OCR service is created by
``VisionResources`` (CPU by default, CUDA when ``Config.gpu`` enables it) and warmed by the detectors.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping

from contracts import (
    Config,
    Detection,
    Detector,
    DetectorConfig,
    Device,
    EventBus,
    Frame,
    Lane,
    LaneResult,
    MaskRequest,
    Topic,
)

from .lanes import Assembler, LaneSchedule, LaneWorker, _no_assembly
from .planner import planner_factory
from .registry import (
    check_vram_budget,
    discover,
    enabled_detectors,
    uses_gpu,
    vram_required_mb,
)
from .resources import VisionResources

log = logging.getLogger("vision.host")

DEFAULT_LANES = frozenset({Lane.FAST, Lane.ACCURATE})
# Accurate-lane deadline hint passed to detectors: stop refining after this many seconds.
ACCURATE_DEADLINE_S = 0.45

AssemblerFactory = Callable[[Lane, Config], Assembler]


class VisionHost:
    """Implements ``contracts.LaneHost``."""

    def __init__(
        self,
        config: Config,
        bus: EventBus | None = None,
        *,
        registry: Mapping[str, type[Detector]] | None = None,
        resources: VisionResources | None = None,
        assembler_factory: AssemblerFactory | None = None,
        vram_budget_mb: int | None = None,
    ) -> None:
        self.config = config
        self._bus = bus
        self._assembler_factory = assembler_factory
        self.resources = resources or VisionResources(config)
        reg = discover() if registry is None else dict(registry)
        self.classes = enabled_detectors(reg, config)
        self.declared_vram_mb = check_vram_budget(self.classes.values(), config, budget_mb=vram_budget_mb)
        self.detectors: dict[str, Detector] = {i: cls() for i, cls in self.classes.items()}
        by_lane: dict[Lane, list[Detector]] = {}
        for det in self.detectors.values():
            by_lane.setdefault(type(det).lane, []).append(det)
        self.active_lanes: frozenset[Lane] = frozenset(by_lane) or DEFAULT_LANES
        self._workers: dict[Lane, LaneWorker] = {}
        self.assemblers: dict[Lane, Assembler] = {}
        for lane in sorted(self.active_lanes, key=lambda x: x.value):
            self._workers[lane] = LaneWorker(
                lane,
                by_lane.get(lane, ()),
                LaneSchedule.from_config(lane, config.detect),
                categories=config.categories,
                allowlist=self.resources.allowlist,
                assembler=self._wrap(lane),
                deadline_s=ACCURATE_DEADLINE_S if lane is Lane.ACCURATE else None,
            )
        self._lock = threading.Lock()
        self._started = False
        self._closed = False

    # ------------------------------------------------------------------ assembler
    def _wrap(self, lane: Lane) -> Assembler:
        inner = self._assembler_factory(lane, self.config) if self._assembler_factory else _no_assembly
        self.assemblers[lane] = inner

        def assemble(
            frame: Frame, lane_: Lane, detections: tuple[Detection, ...]
        ) -> tuple[tuple[MaskRequest, ...], tuple[Detection, ...]]:
            out = inner(frame, lane_, detections)
            if self._bus is not None:
                for det in detections:
                    try:
                        self._bus.publish(Topic.DETECTION, det)
                    except Exception:  # noqa: BLE001 - telemetry must never fail a lane run
                        log.debug("bus publish failed", exc_info=True)
            return out

        return assemble

    # ------------------------------------------------------------------ LaneHost
    def _detector_config(self, det: Detector) -> DetectorConfig:
        gpu = uses_gpu(self.config)
        return DetectorConfig(
            categories=self.config.categories,
            device=type(det).device if gpu else Device.CPU,
            ort_provider="cuda" if gpu else "cpu",
            gpu_mem_limit_mb=self.config.gpu.mem_limit_mb if gpu else 0,
        )

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            if self._closed:
                raise RuntimeError("VisionHost is closed")
            for det in self.detectors.values():
                det.configure(self._detector_config(det), self.resources)
            for det in self.detectors.values():
                det.warmup()
            for worker in self._workers.values():
                worker.start()
            self._started = True
        log.info(
            "vision started: lanes=%s detectors=%s declared_vram_mb=%d",
            sorted(lane.value for lane in self.active_lanes),
            sorted(self.detectors),
            self.declared_vram_mb,
        )

    def submit(self, frame: Frame) -> None:
        if not self._started:
            raise RuntimeError("VisionHost.submit before start()")
        if self._closed:
            return
        for worker in self._workers.values():
            try:
                worker.offer(frame)
            except RuntimeError:
                if not self._closed:
                    raise

    def poll(self) -> list[LaneResult]:
        out: list[LaneResult] = []
        for worker in self._workers.values():
            out.extend(worker.drain())
        out.sort(key=lambda r: (r.completed_at, r.frame_seq))
        return out

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        errors: list[str] = []
        for worker in self._workers.values():
            try:
                worker.close()
            except RuntimeError as exc:
                errors.append(str(exc))
        self.resources.close()
        if errors:
            raise RuntimeError("; ".join(errors))

    @property
    def stats(self) -> dict[str, dict[str, object]]:
        out: dict[str, dict[str, object]] = {}
        for lane, worker in self._workers.items():
            stats: dict[str, object] = dict(worker.stats)
            planner = self.assemblers.get(lane)
            if planner is not None and hasattr(planner, "live_tracks"):
                stats["tracks"] = planner.live_tracks
                stats["tracker_fell_back"] = bool(getattr(planner, "fell_back", False))
            out[lane.value] = stats
        return out

    @property
    def vram_required_mb(self) -> int:
        return vram_required_mb(self.classes.values(), self.config)


def build(config: Config, bus: EventBus) -> VisionHost:
    """Entry point loaded by the engine from ``Config.runtime.modules['vision']``."""
    return VisionHost(config, bus, assembler_factory=planner_factory)
