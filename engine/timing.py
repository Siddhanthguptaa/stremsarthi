"""``StageTiming`` JSONL writer for the eval harness (docs/specs/engine.md 4, 10).

New code. One JSON object per line (plus ``{"type":"event","name","t"}`` point events): ``{"stage", "seq", "pts", "t_start", "t_end", "ms"}``. The
times are ``time.monotonic()`` seconds, the stage is a code such as ``compose`` or ``lane_fast``.
Nothing else is written: no images, no text, no detections. Attach it to the ``LocalBus`` and it
records every ``Topic.TIMING`` event, the engine's own stages and the ingest stages the relay
publishes, so the harness reads one file.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import IO

from contracts import StageTiming, Topic

from .bus import LocalBus

log = logging.getLogger("streamsarthi.engine.timing")


class TimingLog:
    def __init__(self, path: str | Path | None, *, flush_interval_s: float = 0.5) -> None:
        self.path = Path(path) if path else None
        self._flush_interval = flush_interval_s
        self._lock = threading.Lock()
        self._fh: IO[str] | None = None
        self._last_flush = time.monotonic()
        self._remove: object | None = None
        self.written = 0
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = self.path.open("a", encoding="utf-8")
            except OSError as exc:
                log.warning("timing log disabled: cannot open file (%s)", type(exc).__name__)

    @property
    def enabled(self) -> bool:
        return self._fh is not None

    def attach(self, bus: LocalBus) -> None:
        """Record every ``Topic.TIMING`` event published on ``bus``."""
        self._remove = bus.on(Topic.TIMING, self.write)

    def write(self, timing: StageTiming) -> None:
        if self._fh is None:
            return
        line = json.dumps(
            {
                "stage": timing.stage,
                "seq": timing.seq,
                "pts": None if timing.pts is None else round(timing.pts, 6),
                "t_start": round(timing.t_start, 6),
                "t_end": round(timing.t_end, 6),
                "ms": round((timing.t_end - timing.t_start) * 1000.0, 3),
            },
            separators=(",", ":"),
        )
        with self._lock:
            if self._fh is None:
                return
            self._fh.write(line + "\n")
            self.written += 1
            now = time.monotonic()
            if now - self._last_flush >= self._flush_interval:
                self._fh.flush()
                self._last_flush = now

    def event(self, name: str, t: float, detail: str | None = None) -> None:
        """A point event ``{"type":"event","name","t"}`` (``t`` is ``time.monotonic()``), e.g. the engine
        receiving a hold command: the eval harness reads ``hold_received`` for its slate-latency check."""
        if self._fh is None:
            return
        obj: dict[str, object] = {"type": "event", "name": name, "t": round(t, 6)}
        if detail is not None:
            obj["detail"] = detail
        line = json.dumps(obj, separators=(",", ":"))
        with self._lock:
            if self._fh is None:
                return
            self._fh.write(line + "\n")
            self.written += 1
            self._fh.flush()  # rare and latency-critical for the reader: do not wait for the interval
            self._last_flush = time.monotonic()

    def flush(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.flush()

    def close(self) -> None:
        if callable(self._remove):
            self._remove()
            self._remove = None
        with self._lock:
            if self._fh is not None:
                self._fh.flush()
                self._fh.close()
                self._fh = None
