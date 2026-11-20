"""Single-slot egress writer and the encode-and-fan-out output it drives.

``EgressWriter`` is IRL Shield ``relay/egress.py`` (MIT, Copyright (c) 2026 Elan Nash; see
``relay/LICENSE``): one thread owns the encoder call and keeps at most one replaceable pending frame,
so the engine tick never waits for encode, mux or a slow destination. Changes: the hold re-check is
gone (the engine already does it at hand-off, ``Session.recheck``); the slot holds a private copy of
the frame (the engine may reuse its buffer) from a three-buffer pool, so steady state allocates
nothing; the audio of a frame replaced in the slot is carried to its successor, so a skipped frame
never loses audio; the "slow write" threshold is configurable and ``presented_at`` is stamped here.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from contracts.enums import Category
from relay.destinations import DestinationSet
from relay.encoder import STAGE_ENCODE, Encoder

log = logging.getLogger("relay.egress")


class EgressError(RuntimeError):
    """Only fixed codes cross this seam, never libav URLs or errors."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(slots=True)
class _Item:
    pts: float
    image: np.ndarray
    chunks: list[tuple[float, np.ndarray]]
    presented_at: float


class EgressWriter:
    """Own the output on one thread, retaining at most one pending frame.

    ``write()`` never blocks on the output. A pending frame is replaceable: its audio moves on to the
    replacing frame, its pixels are dropped and counted. A failed or slow write (``False`` or longer
    than ``slow_after_s``) discards the slot, and frames presented before that moment are dropped, so
    recovery starts from a fresh frame. ``write()`` returns the destination readiness: ``None`` until
    known, ``False`` during recovery, ``True`` when healthy.
    """

    capacity = 1

    def __init__(
        self,
        output: Any,
        *,
        shape: tuple[int, int] = (720, 1280),
        slow_after_s: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.output, self._clock, self._slow = output, clock, slow_after_s
        self._shape = (int(shape[0]), int(shape[1]), 3)
        self._pool: list[np.ndarray] = [np.empty(self._shape, np.uint8) for _ in range(3)]
        self.submitted = self.dropped = self.written = self.slow_writes = 0
        self.max_write_s = 0.0
        self._cond = threading.Condition()
        self._pending: _Item | None = None
        self._ready: bool | None = None
        self._stopped = False
        self._error: EgressError | None = None
        self._cleanup_error: EgressError | None = None
        self._fresh_after = 0.0
        self.thread = threading.Thread(target=self._run, name="relay-egress-writer", daemon=True)
        self.thread.start()

    @property
    def alive(self) -> bool:
        return self._error is None and not self._stopped and self.thread.is_alive()

    def _take(self) -> np.ndarray:
        with self._cond:
            if self._pool:
                return self._pool.pop()
        return np.empty(self._shape, np.uint8)

    def _give(self, image: np.ndarray) -> None:
        with self._cond:
            if len(self._pool) < 3:
                self._pool.append(image)

    def write(self, pts: float, image: Any, pcm: Any) -> bool | None:
        if getattr(image, "shape", None) != self._shape or getattr(image, "dtype", None) != np.uint8:
            raise EgressError("egress-frame-contract")
        presented_at = self._clock()
        with self._cond:
            if self._error is not None:
                raise self._error
            if self._stopped:
                raise EgressError("egress-closed")
            if presented_at < self._fresh_after:
                self.dropped += 1
                return self._ready
        copy = self._take()
        np.copyto(copy, image)
        samples = np.array(pcm, dtype=np.int16, copy=True).reshape(-1)
        with self._cond:
            if self._error is not None or self._stopped:
                self._pool.append(copy)
                raise self._error or EgressError("egress-closed")
            carried: list[tuple[float, np.ndarray]] = []
            if self._pending is not None:
                self.dropped += 1
                carried = self._pending.chunks
                if len(self._pool) < 3:
                    self._pool.append(self._pending.image)
            self._pending = _Item(float(pts), copy, [*carried, (float(pts), samples)], presented_at)
            self.submitted += 1
            self._cond.notify()
            return self._ready

    def raise_if_failed(self) -> None:
        with self._cond:
            if self._error is not None:
                raise self._error

    def _run(self) -> None:
        try:
            while True:
                with self._cond:
                    self._cond.wait_for(lambda: self._stopped or self._pending is not None)
                    if self._stopped:
                        return
                    item = self._pending
                    self._pending = None
                assert item is not None
                began = self._clock()
                accepted = self.output.write(item.pts, item.image, item.chunks)
                elapsed = self._clock() - began
                self._give(item.image)
                with self._cond:
                    self.max_write_s = max(self.max_write_s, elapsed)
                    if accepted is not None:
                        self._ready = accepted
                    if accepted is not False:
                        self.written += 1
                    if accepted is False or elapsed > self._slow:
                        if accepted is not False:
                            self.slow_writes += 1
                        self._fresh_after = self._clock()
                        if self._pending is not None:
                            self.dropped += 1
                            self._pending = None
        except Exception:  # noqa: BLE001 - surface a fixed code on the next write, never upstream text
            log.warning("egress writer failed", exc_info=log.isEnabledFor(logging.DEBUG))
            with self._cond:
                self._error = EgressError("egress-writer-failed")
                self._ready = False
        finally:
            try:
                self.output.close()
            except Exception:  # noqa: BLE001
                with self._cond:
                    self._cleanup_error = EgressError("egress-writer-cleanup")

    def close(self) -> None:
        """Join the writer; report only cleanup failures (write errors stay separate)."""
        with self._cond:
            self._stopped = True
            self._pending = None
            self._cond.notify()
        self.thread.join(timeout=25)
        if self.thread.is_alive():
            raise EgressError("egress-writer-survived")
        if self._cleanup_error is not None:
            raise self._cleanup_error


class Egress:
    """What the writer drives: encode a frame once, then hand the packets to every destination."""

    def __init__(self, encoder: Encoder, destinations: DestinationSet):
        self.encoder, self.destinations = encoder, destinations
        self.frames = 0
        self.dropped_encoder_down = 0
        self.last_encode_s = 0.0

    @property
    def healthy(self) -> bool:
        return self.encoder.healthy

    def write(self, pts: float, image: np.ndarray, chunks: list[tuple[float, np.ndarray]]) -> bool:
        began = time.monotonic()
        result = self.encoder.encode(pts, image, chunks)
        if result is None:
            self.dropped_encoder_down += 1
            return False  # encoder down or backing off: the writer drops the slot and waits for a fresh frame
        done = time.monotonic()
        self.frames += 1
        self.last_encode_s = done - began
        self.destinations.publish_timing(STAGE_ENCODE, result.video_index, pts, began, done)
        self.destinations.offer(result, self.encoder.stream_info, done)
        return True

    def encoder_event(self, code: str) -> None:
        self.destinations.publish_safety("relay.encoder", Category.SYSTEM_STATUS, "warned", code)

    def stats(self) -> dict[str, Any]:
        return {
            "frames": self.frames, "encoder_down_drops": self.dropped_encoder_down,
            "codec": self.encoder.codec, "generation": self.encoder.generation,
            "restarts": self.encoder.restarts, "last_error": self.encoder.last_error,
            "destinations": self.destinations.stats(),
        }  # fmt: skip

    def close(self) -> None:
        try:
            self.destinations.close()
        finally:
            self.encoder.close()
