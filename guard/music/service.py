"""MusicGuard: music detector plus fingerprint matcher, emitting ``RiskSignal`` objects (spec 5.1, 5.2).

``push()`` only appends to a bounded queue (microseconds), so the audio path never waits.
``poll()`` does the work (YAMNet about 3 ms per second of audio, a fingerprint scan a few ms) and
returns the signals; ``start()`` runs ``poll()`` on a daemon thread that calls a callback.
Signals carry codes only (``music``, ``fp:demo_03``): no audio, no text.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

import numpy as np

from contracts.config import GuardConfig
from contracts.enums import Category
from contracts.events import RiskSignal

from .detector import SR, MusicDetector, MusicStateMachine, WindowResult
from .fingerprint import FingerprintMatcher, Library
from .library import load_library

BUFFER_S = 8.0  # rolling buffer for fingerprint queries
MIN_QUERY_S = 5.0  # no fingerprint under 5 s of music
SCAN_EVERY_S = 2.0
VERDICT_CACHE_S = 30.0
MAX_QUEUE_S = 30.0


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class MusicGuard:
    def __init__(
        self,
        config: GuardConfig | None = None,
        library: Library | None = None,
        scorer: object | None = None,
        start_pts: float = 0.0,
        wall_clock: Callable[[], str] = _utc_now,
        backends: tuple[str, ...] = ("chromaprint", "landmark"),
    ) -> None:
        cfg = config or GuardConfig()
        self.config = cfg
        lib = library if library is not None else load_library(cfg.fingerprint_library_dir)
        self.matcher = FingerprintMatcher(
            lib, max_ber=cfg.fp_max_ber, margin=cfg.fp_margin, backends=backends
        )
        self.detector = MusicDetector(
            scorer, MusicStateMachine(cfg.music_on_threshold, cfg.music_off_threshold), start_pts=start_pts
        )
        self._wall = wall_clock
        self._queue: deque[np.ndarray] = deque()
        self._queued = 0
        self._lock = threading.Lock()
        self._ring = np.zeros(0, dtype=np.float32)
        self._pts = start_pts  # stream time at the end of the audio consumed so far
        self._on_since: float | None = None
        self._last_music_emit = -1e9
        self._last_scan = -1e9
        self._verdict_at: dict[str, float] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.dropped_s = 0.0

    @property
    def music_on(self) -> bool:
        return self.detector.state.on

    def push(self, samples: np.ndarray) -> None:
        """Non-blocking. Drops the oldest audio if the worker falls more than 30 s behind."""
        chunk = np.asarray(samples, dtype=np.float32)
        with self._lock:
            self._queue.append(chunk)
            self._queued += len(chunk)
            while self._queued > MAX_QUEUE_S * SR and len(self._queue) > 1:
                old = self._queue.popleft()
                self._queued -= len(old)
                self.dropped_s += len(old) / SR

    def poll(self) -> list[RiskSignal]:
        with self._lock:
            chunks = list(self._queue)
            self._queue.clear()
            self._queued = 0
        signals: list[RiskSignal] = []
        if not chunks:
            return signals
        audio = np.concatenate(chunks)
        self._ring = np.concatenate([self._ring, audio])[-int(BUFFER_S * SR) :]
        self._pts += len(audio) / SR
        results = self.detector.feed(audio)
        for r in results:
            signals.extend(self._on_window(r))
        return signals

    def _signal(
        self, source: str, category: Category, score: float, ttl: float, label: str, pts: float
    ) -> RiskSignal:
        return RiskSignal(
            wall_ts=self._wall(),
            pts=pts,
            source=source,
            category=category,
            score=min(1.0, max(0.0, score)),
            ttl_s=ttl,
            label=label,  # type: ignore[arg-type]
        )

    def _on_window(self, r: WindowResult) -> list[RiskSignal]:
        out: list[RiskSignal] = []
        if not r.on:
            self._on_since = None
            return out
        if self._on_since is None:
            self._on_since = r.t_end - 2.0  # music started about two windows before the gate opened
        if r.t_end - self._last_music_emit >= 1.0:
            self._last_music_emit = r.t_end
            out.append(self._signal("music", Category.MUSIC, r.smoothed, 10.0, "music", r.t_end))
        if r.t_end - self._last_scan >= SCAN_EVERY_S:
            sig = self._scan(r.t_end)
            if sig:
                out.append(sig)
        return out

    def _scan(self, t_end: float) -> RiskSignal | None:
        have_s = len(self._ring) / SR
        music_s = t_end - (self._on_since if self._on_since is not None else t_end)
        query_s = min(have_s, BUFFER_S, music_s + 2.0)
        if query_s < MIN_QUERY_S:
            return None
        self._last_scan = t_end
        m = self.matcher.match(self._ring[-int(query_s * SR) :])
        if m is None:
            return None
        last = self._verdict_at.get(m.track_id, -1e9)
        if t_end - last < VERDICT_CACHE_S:
            return None  # same verdict inside its 30 s cache: the earlier 60 s signal is still live
        self._verdict_at[m.track_id] = t_end
        return self._signal("fingerprint", Category.COPYRIGHT_MATCH, 0.95, 60.0, f"fp:{m.track_id}", t_end)

    # --- background worker -------------------------------------------------------------------
    def start(self, callback: Callable[[RiskSignal], None], interval_s: float = 0.25) -> None:
        if self._thread:
            return
        self._stop.clear()

        def run() -> None:
            while not self._stop.is_set():
                for s in self.poll():
                    callback(s)
                self._stop.wait(interval_s)

        self._thread = threading.Thread(target=run, name="guard-music", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None


def timed(fn: Callable[[], object]) -> tuple[object, float]:
    t = time.perf_counter()
    r = fn()
    return r, (time.perf_counter() - t) * 1000.0
