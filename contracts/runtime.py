"""Runtime wiring protocols.

The engine never imports ``relay``, ``vision``, ``audio``, ``guard`` or ``chat``. ``engine/main.py``
loads each module from the dotted ``module:build`` path in ``Config.runtime.modules`` and talks to
what it returns through these protocols. Every ``build`` has the signature
``build(config: Config, bus: EventBus) -> <object below>``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .config import Config
from .detection import LaneResult
from .enums import Lane
from .media import AudioChunk, Frame
from .protocols import EventBus

Builder = Callable[[Config, EventBus], Any]


@runtime_checkable
class FrameSource(Protocol):
    """Decoded input from ingest (relay)."""

    def frames(self) -> Iterator[Frame]:
        """1280x720 BGR frames in order, PTS rebased to 0."""
        ...

    def audio_blocks(self) -> Iterator[tuple[int, Any]]:
        """``(absolute_sample_index_of_first_sample, int16 mono 48 kHz ndarray)`` blocks."""
        ...

    def close(self) -> None: ...


@runtime_checkable
class FrameSink(Protocol):
    """Encoder and egress (relay). ``write`` must not block the engine tick (single replaceable slot)."""

    def write(self, pts: float, image: Any, audio_pcm48k: Any) -> None:
        """One composited frame plus its audio slice (1600 samples per frame at 30 fps)."""
        ...

    @property
    def healthy(self) -> bool: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RelayEndpoints:
    """What ``relay.runtime:build`` returns."""

    source: FrameSource
    sink: FrameSink


@runtime_checkable
class LaneHost(Protocol):
    """What ``vision.host:build`` returns: runs the lanes, tracker and mask planner."""

    active_lanes: frozenset[Lane]

    def start(self) -> None: ...

    def submit(self, frame: Frame) -> None:
        """Non-blocking. Each lane takes the newest frame at its own rate; a slow lane drops work, never frames."""
        ...

    def poll(self) -> list[LaneResult]:
        """Drain finished lane results."""
        ...

    def close(self) -> None: ...


@runtime_checkable
class AudioHost(Protocol):
    """What ``audio.orchestrator:build`` returns. Publishes ``MuteSpan``, ``MaskRequest``
    (MOUTH_OF_SPEAKER), ``CaptionEvent`` and ``Transcript`` on the bus."""

    def start(self) -> None: ...

    def feed(self, chunk: AudioChunk) -> None:
        """Non-blocking 16 kHz analysis chunk."""
        ...

    def close(self) -> None: ...
