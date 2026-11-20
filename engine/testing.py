"""Synthetic builders for engine tests and the eval harness (CPU only, no real media)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np

from contracts import Category, Frame, Lane, LaneResult, MaskKind, MaskRequest, MaskStyle, Quad

from .deadline import DeadlineEngine
from .motion import IdentityMotion

FPS = 30.0
T0 = 10.0  # arrival time of frame 0 in the tests


def make_frame(
    seq: int,
    *,
    fps: float = FPS,
    t0: float = T0,
    size: int = 32,
    value: int | None = None,
    pts: float | None = None,
    arrival: float | None = None,
) -> Frame:
    """A tiny BGR frame; ``value`` defaults to ``seq`` so frames are distinguishable."""
    image = np.full((size, size, 3), seq % 256 if value is None else value, np.uint8)
    p = seq / fps if pts is None else pts
    return Frame(seq=seq, pts=p, arrival=t0 + p if arrival is None else arrival, image=image)


def region_request(
    request_id: str,
    xyxy: tuple[float, float, float, float],
    *,
    seq: int = 0,
    pts_start: float | None = None,
    pts_end: float | None = None,
    fps: float = FPS,
    kind: MaskKind = MaskKind.REGION,
    category: Category = Category.AADHAAR,
    style: MaskStyle = MaskStyle.BLUR,
    frame_pts: float | None = None,
) -> MaskRequest:
    pts = seq / fps if frame_pts is None else frame_pts
    quads: tuple[Quad, ...] = (Quad.from_xyxy(*xyxy),) if kind is MaskKind.REGION else ()
    return MaskRequest(
        request_id=request_id,
        source="fake",
        category=category,
        kind=kind,
        quads=quads,
        frame_pts=pts,
        pts_start=pts if pts_start is None else pts_start,
        pts_end=pts if pts_end is None else pts_end,
        style=style,
        confidence=0.9,
        reason="synthetic",
        completed_at=0.0,
    )


def lane_result(
    lane: Lane,
    seq: int,
    completed_at: float,
    *,
    fps: float = FPS,
    ok: bool = True,
    requests: Sequence[MaskRequest] = (),
) -> LaneResult:
    return LaneResult(
        lane=lane,
        frame_seq=seq,
        pts=seq / fps,
        completed_at=completed_at,
        ok=ok,
        requests=tuple(requests) if ok else (),
    )


def make_engine(
    lanes: Iterable[Lane] = (Lane.FAST, Lane.ACCURATE),
    *,
    motion: Any = None,
    delay: float = 1.0,
    **kwargs: Any,
) -> DeadlineEngine:
    """An engine with identity motion and a short delay (deadline of frame i = 10 + i/30 + 1)."""
    return DeadlineEngine(
        lanes,
        fps=FPS,
        delay_seconds=delay,
        motion=IdentityMotion() if motion is None else motion,
        **kwargs,
    )


def feed(
    engine: DeadlineEngine,
    seqs: Iterable[int],
    *,
    lanes: Iterable[Lane] | None = None,
    complete_after: float = 0.05,
    t0: float = T0,
    fps: float = FPS,
) -> None:
    """Submit frames ``seqs`` on time and an empty ok result per lane for each."""
    for seq in seqs:
        arrival = t0 + seq / fps
        engine.submit(make_frame(seq, t0=t0, fps=fps), now=arrival)
        for lane in engine.lanes if lanes is None else lanes:
            engine.submit_result(lane_result(lane, seq, arrival + complete_after, fps=fps))
