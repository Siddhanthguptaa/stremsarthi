"""Scriptable detector double implementing the Detector protocol."""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Mapping, Sequence
from typing import ClassVar

from ..detection import Detection
from ..enums import Category, Device, Lane
from ..media import Frame
from ..protocols import DetectContext, DetectorConfig, Resources


class FakeDetector:
    id: ClassVar[str] = "fake"
    lane: ClassVar[Lane] = Lane.FAST
    categories: ClassVar[frozenset[Category]] = frozenset({Category.AADHAAR})
    device: ClassVar[Device] = Device.CPU
    vram_mb: ClassVar[int] = 0

    def __init__(
        self,
        script: Mapping[int, Sequence[Detection]] | None = None,
        latency_s: float = 0.0,
        raise_on_seq: int | None = None,
    ) -> None:
        self.script = dict(script or {})
        self.latency_s = latency_s
        self.raise_on_seq = raise_on_seq
        self.configured = False
        self.warmed = False
        self.closed = False
        self.calls: list[int] = []

    def configure(self, cfg: DetectorConfig, res: Resources) -> None:
        self.configured = True

    def warmup(self) -> None:
        self.warmed = True

    def detect(self, frame: Frame, ctx: DetectContext) -> Sequence[Detection]:
        self.calls.append(frame.seq)
        if self.latency_s:
            time.sleep(self.latency_s)
        if self.raise_on_seq is not None and frame.seq == self.raise_on_seq:
            raise RuntimeError("scripted detector failure")
        now = time.monotonic()
        return [
            dataclasses.replace(d, frame_seq=frame.seq, pts=frame.pts, completed_at=now)
            for d in self.script.get(frame.seq, ())
        ]

    def close(self) -> None:
        self.closed = True


def make_fake_detector(
    detector_id: str,
    lane: Lane = Lane.FAST,
    categories: frozenset[Category] = frozenset({Category.AADHAAR}),
) -> type[FakeDetector]:
    """A FakeDetector subclass with its own id/lane/categories (for registry tests)."""
    return type(
        f"Fake_{detector_id}",
        (FakeDetector,),
        {"id": detector_id, "lane": lane, "categories": categories},
    )
