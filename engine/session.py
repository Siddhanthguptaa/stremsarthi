"""``Session``: A/V rings, the tick loop and the emit path. Never blocks on the sink.

Adapted from IRL Shield ``relay/live.py`` (``RelaySession``; MIT, Copyright (c) 2026 Elan Nash;
``engine/THIRD_PARTY_LICENSES/irl-shield/LICENSE``). Kept: the 48 kHz PCM ring tagged by absolute
sample index (a slot is read only when its tag matches, so missing audio is silence), the audio
slice of one frame taken at emit time, and the hold re-check just before the sink. Changed: video
and audio arrive from a ``contracts.FrameSource`` on their own threads; masks come from a
``contracts.LaneHost`` through ``DeadlineEngine``; painting is ``engine/compositor.py``; ``MuteSpan``
and retroactive-hold ranges are applied to the audio slice; events, status and timing go out on the
bus; the egress hold follows ``FrameSink.healthy``.

Threads (``start``): ingest-video (``FrameSource.frames`` -> ``on_frame``), ingest-audio
(``audio_blocks`` -> ``on_audio``) and tick (``step`` every ``tick_s``). Everything they call is
also public and clock-injectable (``now=``), so the unit tests drive a session in virtual time
without threads.

Per emitted frame (docs/specs/engine.md 5.2 to 5.4): ``DeadlineEngine.poll`` decides LIVE, PROTECT
or HOLD; the hold is re-checked; the compositor paints; the audio slice (1600 samples at 30 fps,
exactly the frame's span on the 48 kHz timeline, silent in HOLD, mutes applied) is cut; the sink
gets ``write(pts, image, audio)``. No frame is emitted before its deadline.
"""

from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
from scipy.signal import firwin, lfilter

from contracts import (
    AllowlistUpdate,
    AudioChunk,
    AudioHost,
    CaptionEvent,
    Category,
    Config,
    ConfigChange,
    Frame,
    FrameSink,
    FrameSource,
    HoldCommand,
    HoldSource,
    LaneHost,
    MaskKind,
    MaskRequest,
    MaskStyle,
    MuteSpan,
    SessionCommand,
    StageTiming,
    Topic,
)

from .audio_mute import RATE, MuteBook
from .audio_tee import AudioTee
from .captions import CaptionRenderer
from .compositor import Composed, Compositor
from .control import HOLD, LIVE
from .deadline import DeadlineEngine, FrameDecision
from .publishers import SafetyPublisher, StatusPublisher
from .timing import TimingLog

log = logging.getLogger("streamsarthi.engine.session")

ANALYSIS_RATE = 16_000
RESYNC_SAMPLES = RATE // 50  # 20 ms: audio and video clocks further apart than this are re-aligned
# Audio watchdog: the audio host publishes StageTiming(stage="audio_heartbeat") on Topic.TIMING about once a
# second while it is healthy. Without one for ``audio_watchdog_s`` the audio is unchecked and goes out muted.
AUDIO_HEARTBEAT_STAGE = "audio_heartbeat"
AUDIO_WATCHDOG_S = 3.0  # default silence tolerated between heartbeats
AUDIO_GRACE_S = (
    2.5  # startup grace before the first heartbeat (matches the config default; clamped to delay - 0.5 s)
)
# The heartbeat's pts is the newest audio pts the host has seen. A beat whose pts lags the newest ingested
# audio pts by more than this is a stuck loop (or a spoof) and counts as no beat at all.
AUDIO_HEARTBEAT_LAG_S = 2.0
# A beat vouches for audio only up to ``beat pts - margin``: the beat is the newest chunk handed to the host, and
# speech inside the last second or so has no verdict yet (STT is still working on it). Clamped to delay - 1.5 s.
AUDIO_VOUCH_MARGIN_S = 1.0


class AudioRing:
    """48 kHz int16 ring indexed by absolute sample number. A slot is valid only when its tag matches."""

    def __init__(self, seconds: float, rate: int = RATE) -> None:
        self.capacity = int(math.ceil(seconds * rate))
        self._samples = np.zeros(self.capacity, np.int16)
        self._tags = np.full(self.capacity, -1, np.int64)
        self._lock = threading.Lock()
        self.latest = -1  # highest sample index written
        self.rejected = 0

    def write(self, index: int, pcm: np.ndarray) -> None:
        n = len(pcm)
        if n == 0:
            return
        idx = np.arange(index, index + n, dtype=np.int64)
        with self._lock:
            floor = self.latest - self.capacity + 1  # older samples would overwrite newer ones
            if index < floor:
                keep = idx >= floor
                self.rejected += int((~keep).sum())
                idx, pcm = idx[keep], pcm[keep]
                if len(idx) == 0:
                    return
            slots = idx % self.capacity
            self._samples[slots] = pcm
            self._tags[slots] = idx
            self.latest = max(self.latest, int(idx[-1]))

    def read(self, first: int, n: int) -> np.ndarray:
        """``n`` samples from ``first``; anything not written (or overwritten) reads as silence."""
        idx = np.arange(first, first + n, dtype=np.int64)
        slots = idx % self.capacity
        with self._lock:
            valid = self._tags[slots] == idx
            return np.where(valid, self._samples[slots], 0).astype(np.int16)

    def zero(self, start: int, end: int) -> None:
        """Silence ``[start, end)`` in place (retroactive hold)."""
        if end <= start:
            return
        start = max(start, end - self.capacity)
        idx = np.arange(start, end, dtype=np.int64)
        slots = idx % self.capacity
        with self._lock:
            hit = self._tags[slots] == idx
            self._samples[slots[hit]] = 0

    def clear(self) -> None:
        with self._lock:
            self._tags.fill(-1)
            self.latest = -1

    @property
    def nbytes(self) -> int:
        return int(self._samples.nbytes + self._tags.nbytes)


class Downsampler:
    """Streaming 48 kHz -> 16 kHz (low-pass FIR with carried state, then every third sample)."""

    def __init__(self) -> None:
        self._taps = firwin(63, 7000.0, fs=RATE).astype(np.float64)
        self._zi = np.zeros(len(self._taps) - 1)
        self._phase = 0

    def reset(self) -> None:
        self._zi[:] = 0.0
        self._phase = 0

    def process(self, pcm48: np.ndarray) -> np.ndarray:
        filtered, self._zi = lfilter(self._taps, 1.0, pcm48.astype(np.float64), zi=self._zi)
        out = filtered[self._phase :: 3]
        self._phase = (self._phase - len(pcm48)) % 3
        return np.clip(np.rint(out), -32768, 32767).astype(np.int16)


class Session:
    def __init__(
        self,
        config: Config,
        source: FrameSource,
        sink: FrameSink,
        *,
        lane_host: LaneHost | None = None,
        audio_host: AudioHost | None = None,
        bus: Any = None,
        engine: DeadlineEngine | None = None,
        compositor: Compositor | None = None,
        captions: CaptionRenderer | None = None,
        clock: Callable[[], float] = time.monotonic,
        session_id: str | None = None,
        tick_s: float = 0.004,
        audio_watchdog_s: float | None = None,
        audio_grace_s: float | None = None,
        audio_heartbeat_lag_s: float | None = None,
        audio_vouch_margin_s: float | None = None,
    ) -> None:
        self.config = config
        self.source, self.sink = source, sink
        self.lane_host, self.audio_host = lane_host, audio_host
        self.bus = bus
        self.clock = clock
        self.tick_s = tick_s
        self.session_id = session_id or uuid.uuid4().hex
        self.fps = float(config.fps)
        lanes = lane_host.active_lanes if lane_host is not None else ()
        self.engine = engine or DeadlineEngine.from_config(config, lanes)
        self.compositor = compositor or Compositor(config)
        self.captions = captions or CaptionRenderer(config, self.compositor.width, self.compositor.height)
        self.mutes = MuteBook()
        self.mute_ms: deque[float] = deque(maxlen=4096)  # ring read + mute application, per emitted frame
        self._mute_window: tuple[float, float] | None = None
        delay = self.engine.delay_seconds
        self.ring = AudioRing(delay + 3.0)
        self._down = Downsampler()
        self._audio_seq = 0
        self._taps: tuple[AudioTee, ...] = ()
        self._acursor: float | None = None  # next output sample (float: fps need not divide 48 kHz)
        self._cursor_reset = False  # set by ingest threads, applied by the tick thread
        self.safety = SafetyPublisher(bus, self.session_id, fps=self.fps) if bus is not None else None
        self.status = (
            StatusPublisher(bus, self.engine, applied_allowlist_version=self._applied_allowlist_version)
            if bus is not None
            else None
        )
        self.timing_log = TimingLog(config.engine.timing_log_path)
        if bus is not None and self.timing_log.enabled:
            self.timing_log.attach(bus)
        self._highwater = -1  # highest capture sequence submitted: the fence's high-water mark
        # audio watchdog. Timeout: kwarg, else ``engine.audio_watchdog_s`` when the config has it, else 3 s;
        # at most delay - 0.5 s so the unchecked speech is still in the delay buffer when it fires; 0 disables.
        timeout = audio_watchdog_s
        if timeout is None:
            timeout = getattr(config.engine, "audio_watchdog_s", AUDIO_WATCHDOG_S)
        self.audio_watchdog_s = 0.0 if not timeout or timeout <= 0 else max(0.5, min(timeout, delay - 0.5))
        grace = audio_grace_s
        if grace is None:
            grace = getattr(config.engine, "audio_grace_s", AUDIO_GRACE_S)
        # The startup grace is clamped like the timeout: audio ingested at t is emitted at t + delay, so a
        # grace longer than delay - 0.5 s would let unchecked audio out before the watchdog could mute it.
        self.audio_grace_s = max(0.0, min(float(grace), delay - 0.5))
        lag = audio_heartbeat_lag_s
        if lag is None:
            lag = getattr(config.engine, "audio_heartbeat_lag_s", AUDIO_HEARTBEAT_LAG_S)
        self.audio_heartbeat_lag_s = max(0.0, float(lag))
        margin = audio_vouch_margin_s
        if margin is None:
            margin = getattr(config.engine, "audio_vouch_margin_s", AUDIO_VOUCH_MARGIN_S)
        self.audio_vouch_margin_s = max(0.0, min(float(margin), delay - 1.5))
        self._inflight_samples = round(
            (delay - 0.5) * RATE
        )  # speech that may be in flight when a beat stalls
        if self.audio_watchdog_s and abs(self.audio_grace_s - float(grace)) > 1e-9:
            log.warning(
                "audio startup grace %.1f s is longer than delay - 0.5 s: using %.1f s",
                float(grace),
                self.audio_grace_s,
            )
        log.info(
            "audio watchdog: heartbeat timeout %.1f s, startup grace %.1f s, heartbeat lag margin %.1f s",
            self.audio_watchdog_s,
            self.audio_grace_s,
            self.audio_heartbeat_lag_s,
        )
        self._wd_lock = threading.Lock()
        self._wd_start: float | None = None  # when the audio path started (first block or first tick)
        self._wd_last_beat: float | None = None  # engine clock at the last heartbeat
        self._wd_beat_index = -1  # newest ring sample when that heartbeat arrived
        self._wd_first_index: int | None = None  # first sample ingested (nothing was ever checked before it)
        self._wd_stale_from = 0
        # Audio is emitted muted unless a heartbeat has vouched (by its pts) for the samples: _checked_until is
        # the newest accepted beat pts. No beat yet, or a stuck host whose pts stopped, means muted output.
        self._checked_until = float("-inf")
        self._newest_ingest_pts: float | None = None
        self._first_ingest_pts: float | None = None
        self.audio_beats_rejected = 0
        self.audio_heartbeats = 0
        self._audio_stale = False
        self._audio_stale_since: float | None = None
        self._arrivals: dict[int, float] = {}
        self._arrival_order: deque[int] = deque()
        self._generation: Any = None
        self._resets_seen = 0
        self._state = "ready"
        self._last_emit_at: float | None = None
        self._egress_held = False
        self._last_pts: float | None = None
        self._lanes_checked = 0.0
        self.counts: dict[str, int] = {
            "emitted": 0, "LIVE": 0, "PROTECT": 0, "HOLD": 0, "fenced": 0, "bad_frames": 0,
            "sink_errors": 0, "lane_errors": 0, "audio_errors": 0, "audio_resyncs": 0,
            "rejected_requests": 0, "compose_errors": 0, "config_ignored": 0,
            "late_spans": 0, "tardy_spans": 0, "audio_watchdog": 0,
            "caption_frames": 0, "caption_errors": 0, "audio_unvouched": 0,
        }  # fmt: skip
        self.emit_lag: deque[float] = deque(maxlen=4096)
        self.compose_ms: deque[float] = deque(maxlen=4096)
        self.caption_ms: deque[float] = deque(maxlen=4096)  # burn-in time, only for frames that drew one
        self.finished = threading.Event()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._unsubscribe: list[Callable[[], None]] = []
        if bus is not None:
            self._wire_bus(bus)

    # ------------------------------------------------------------------ bus wiring

    def _wire_bus(self, bus: Any) -> None:
        on = getattr(bus, "on", None)
        if on is None:
            return
        self._unsubscribe = [
            on(Topic.HOLD, self.apply_hold),
            on(Topic.CONFIG, self.apply_config),
            on(Topic.MUTE_SPAN, self.on_mute),
            on(Topic.TIMING, self._on_timing_event),
            on(Topic.MASK_REQUEST, self.on_mask_request),
            on(Topic.CAPTION, self.on_caption),
            on(Topic.ALLOWLIST, self._on_allowlist),
            on(Topic.SESSION, self._on_session_command),
        ]

    def _applied_allowlist_version(self) -> int | None:
        """The version the vision host actually applied, when it reports one (``VisionHost.applied_allowlist_version``)."""
        return getattr(self.lane_host, "applied_allowlist_version", None)

    def _on_allowlist(self, update: AllowlistUpdate) -> None:
        # The received version is only a fallback for hosts that do not report what they applied: reporting it
        # as soon as it arrives would hide an update that vision dropped, and the API would never resync.
        if self.status is not None and self._applied_allowlist_version() is None:
            self.status.allowlist_version = update.version

    # ------------------------------------------------------------------ audio watchdog

    def _on_timing_event(self, timing: StageTiming) -> None:
        if timing.stage == AUDIO_HEARTBEAT_STAGE:
            self.audio_heartbeat(pts=timing.pts)

    def audio_heartbeat(self, now: float | None = None, pts: float | None = None) -> bool:
        """The audio host says it is alive and has seen audio up to ``pts``. Arrival is stamped on the engine
        clock. A beat whose pts lags the newest ingested audio by more than ``audio_heartbeat_lag_s`` (no pts
        counts as the start of the audio) proves nothing: it is ignored, as if it had not come. True if accepted."""
        now = self.clock() if now is None else now
        with self._wd_lock:
            newest = self._newest_ingest_pts
            seen = pts if pts is not None else self._first_ingest_pts
            if newest is not None and seen is not None:
                lag = newest - seen
                # behind (stuck loop) or ahead (a clock from before a PTS reset, or a spoof): proves nothing
                if abs(lag) > self.audio_heartbeat_lag_s:
                    self.audio_beats_rejected += 1
                    return False
            self._wd_last_beat = now
            if pts is not None and newest is not None:
                seen_to = min(float(pts), newest)  # never vouch for audio that has not been ingested
                self._wd_beat_index = min(self.ring.latest, round(seen_to * RATE))
                self._checked_until = max(self._checked_until, seen_to)
            elif pts is not None:  # no audio ingested yet: nothing to vouch for
                self._wd_beat_index = self.ring.latest
            else:  # nothing the host has seen yet: none of the ingested samples is vouched for
                first = self._wd_first_index
                self._wd_beat_index = (first - 1) if first is not None else self.ring.latest
            self.audio_heartbeats += 1
        return True

    def _check_audio_watchdog(self, now: float) -> None:
        """Fail-safe for a dead or silent audio module. With no heartbeat for ``audio_watchdog_s`` (or none
        within ``audio_grace_s`` of the start) the audio is unchecked: the samples ingested since the last
        heartbeat are hard-muted (they are still in the delay buffer, which is why the timeout is below the
        delay) and everything emitted until the heartbeats resume is muted. A failsafe safety event and an
        ``audio`` entry in ``EngineStatus.lanes_stale`` follow."""
        if self.audio_host is None or not self.audio_watchdog_s:
            return
        since: float | None = None
        resumed_at = -1
        with self._wd_lock:
            if self._wd_start is None:
                self._wd_start = now
            beat = self._wd_last_beat
            limit = self._wd_start + self.audio_grace_s if beat is None else beat + self.audio_watchdog_s
            dead = now > limit
            trigger = dead and not self._audio_stale
            recover = (
                self._audio_stale and not dead and beat is not None and beat > (self._audio_stale_since or 0)
            )
            from_index = self._wd_beat_index if beat is not None else (self._wd_first_index or 0)
            if trigger:
                self._audio_stale, self._audio_stale_since = True, now
                self._wd_stale_from = max(0, from_index - self._inflight_samples)
            elif recover:
                self._audio_stale = False
                since, self._audio_stale_since = self._audio_stale_since, None
                resumed_at = self._wd_beat_index
        if trigger:
            # Speech ingested up to delay - 0.5 s before the last beat may still be in flight (no verdict yet):
            # silence that too (samples already emitted are a no-op), then everything up to the newest sample.
            start = max(0, from_index - self._inflight_samples)
            if self.ring.latest >= start:
                self.mutes.add_silence(start, self.ring.latest + 1)
            self.counts["audio_watchdog"] += 1
            log.warning("audio watchdog: no heartbeat from the audio module, audio is muted")
            if self.safety is not None:
                self.safety.audio_unchecked(True, self._last_pts)
            if self.status is not None:
                self.status.audio_stale = True
        elif recover:
            # what was ingested while the module was silent is still unchecked when it is emitted later
            self.mutes.add_silence(
                max(0, min(self._wd_stale_from, resumed_at - self._inflight_samples)), resumed_at + 1
            )
            log.warning("audio watchdog: the audio module is back, audio is checked again")
            if self.safety is not None:
                self.safety.audio_unchecked(False, self._last_pts, round((now - (since or now)) * 1000.0))
            if self.status is not None:
                self.status.audio_stale = False

    @property
    def audio_unchecked(self) -> bool:
        return self._audio_stale

    def _on_session_command(self, command: SessionCommand) -> None:
        # Go-live orchestration belongs to the API (T6.1); the engine is always on and ignores it.
        log.info("session command %s ignored by the engine", command.action)

    def _publish_timing(self, stage: str, seq: int, pts: float | None, t0: float, t1: float) -> None:
        if self.bus is None:
            return
        try:
            self.bus.publish(Topic.TIMING, StageTiming(stage, seq, pts, t0, max(t0, t1)))
        except Exception as exc:
            log.debug("timing publish failed: %s", type(exc).__name__)

    # ------------------------------------------------------------------ ingest

    def on_frame(self, frame: Frame, now: float | None = None) -> bool:
        """Feed one decoded frame to the engine and the vision host. False if it was discarded."""
        now = self.clock() if now is None else now
        try:
            frame.validate_canvas()
        except ValueError:
            self.counts["bad_frames"] += 1
            return False
        self._highwater = max(self._highwater, frame.seq)
        try:
            accepted = self.engine.submit(frame, now)
        except (ValueError, RuntimeError):
            self.counts["bad_frames"] += 1
            return False
        if self.engine.resets != self._resets_seen:
            self._resets_seen = self.engine.resets
            self._on_reset(frame.seq)
        if not accepted:
            return False
        self._arrivals[frame.seq] = frame.arrival
        self._arrival_order.append(frame.seq)
        while len(self._arrival_order) > 512:
            self._arrivals.pop(self._arrival_order.popleft(), None)
        if self.lane_host is not None:
            try:
                self.lane_host.submit(frame)
            except Exception as exc:
                self.counts["lane_errors"] += 1
                log.warning("lane host submit failed: %s", type(exc).__name__)
        return True

    def _reset_audio_clock(self) -> None:
        with self._wd_lock:
            self._checked_until = float("-inf")
            self._newest_ingest_pts = self._first_ingest_pts = None
            self._wd_beat_index = -1

    def _on_reset(self, seq: int) -> None:
        """The PTS clock restarted: sequence-keyed and sample-keyed state from the old clock is void."""
        self._highwater = seq
        self._arrivals.clear()
        self._arrival_order.clear()
        self.compositor.presenter.reset()
        self.captions.clear()
        if self._generation is None:  # the source does not label audio generations: clear here
            self.ring.clear()
            self._down.reset()
        self.mutes.clear()
        self._cursor_reset = True
        self._reset_audio_clock()

    def on_audio(self, index: int, pcm: Any, generation: Any = None) -> None:
        """Feed one 48 kHz block tagged with its absolute sample index; tap 16 kHz to the audio host."""
        samples = np.asarray(pcm, np.int16)
        if generation is not None and generation != self._generation:
            if self._generation is not None:
                self.ring.clear()
                self._down.reset()
                self.mutes.clear()
                self._cursor_reset = True
            self._generation = generation
            self._reset_audio_clock()
        self.ring.write(int(index), samples)
        if self.audio_host is not None:
            with self._wd_lock:
                if self._wd_start is None:
                    self._wd_start = self.clock()  # the grace runs from the first audio, not the first tick
                if self._first_ingest_pts is None:
                    self._first_ingest_pts = int(index) / RATE
                self._newest_ingest_pts = max(
                    self._newest_ingest_pts or 0.0, (int(index) + len(samples)) / RATE
                )
        host, taps = self.audio_host, self._taps
        if host is None and not taps:
            return
        if host is not None and self._wd_first_index is None:
            self._wd_first_index = int(index)
        try:
            chunk = AudioChunk(
                seq=self._audio_seq,
                pts_start=index / RATE,
                sample_rate=ANALYSIS_RATE,
                pcm=self._down.process(samples),
                arrival=self.clock(),
            )
            self._audio_seq += 1
            if host is not None:
                host.feed(chunk)
        except Exception as exc:  # the audio path must never stall ingest
            self.counts["audio_errors"] += 1
            log.warning("audio host feed failed: %s", type(exc).__name__)
            # this block was never (fully) handed to the checker: it goes out as hard silence, exactly this block
            self.mutes.add_silence(int(index), int(index) + len(samples))
            return
        if taps and not self._audio_stale:  # unchecked audio is not handed to optional services
            for tap in taps:  # a bounded queue put each: never blocks, never raises
                tap.offer(chunk)

    def add_audio_tap(self, name: str, feed: Callable[[Any], Any]) -> AudioTee:
        """Also hand every 16 kHz ``AudioChunk`` to ``feed`` (an optional service, e.g. the music guard)
        on its own thread. Closed by ``stop``; nothing is fed after that."""
        tap = AudioTee(name, feed)
        self._taps = (*self._taps, tap)
        return tap

    # ------------------------------------------------------------------ commands from the bus

    def hold(self, source: HoldSource | str, active: bool, now: float | None = None) -> str:
        now = self.clock() if now is None else now
        src = HoldSource(source)
        state = self.engine.hold(src, active, now=now)
        if self.safety is not None:
            self.safety.hold(src, active, now, self._last_pts)
        self._apply_retro_audio()
        return state

    def apply_hold(self, command: HoldCommand) -> None:
        received = self.clock()
        # the eval harness reads this point event for its "slate within 1 frame of the command" check
        self.timing_log.event("hold_received", received, "on" if command.active else "off")
        self.hold(command.source, command.active, received)

    def apply_config(self, change: ConfigChange) -> None:
        """Runtime toggles the engine owns; keys for other modules are not an error."""
        for key, value in change.diff.items():
            try:
                self._apply_config_key(key, value)
            except (TypeError, ValueError):
                self.counts["config_ignored"] += 1

    def _apply_config_key(self, key: str, value: Any) -> None:
        if key == "hold.retro_seconds":
            retro = max(0.0, min(float(value), self.engine.delay_seconds - 0.5))
            self.engine.control.retro_seconds = retro
        elif key == "engine.max_mask_area_ratio":
            ratio = float(value)
            if not 0.0 < ratio <= 1.0:
                raise ValueError("ratio out of range")
            self.compositor.max_area_ratio = ratio
        elif key.startswith("categories.") and key.endswith(".style"):
            category = Category(key.split(".")[1])
            self.compositor.style_overrides[category] = MaskStyle(value)
        elif key == "audio.mute_mode":
            if value not in ("silence", "bleep"):
                raise ValueError("unknown mute mode")
            self.mutes.mode_override = str(value)  # takes effect for audio not yet emitted
        elif self.captions.apply_config_key(key, value):
            pass  # captions.enabled / captions.max_chars_per_line
        # other keys belong to other modules

    def on_caption(self, event: CaptionEvent) -> None:
        """Queue a caption for burn-in at its PTS (the text is never logged, published or counted by content)."""
        try:
            self.captions.add(event)
        except Exception as exc:  # captions are cosmetic: they must never disturb ingest or the clock
            self.counts["caption_errors"] += 1
            log.warning("caption add failed: %s", type(exc).__name__)

    def on_mute(self, span: MuteSpan, now: float | None = None) -> None:
        """Accept a ``MuteSpan`` (docs/specs/engine.md 5.6) and report it (codes only, never audio or text)."""
        now = self.clock() if now is None else now
        outcome = self.mutes.add(span)
        origin = self.engine.origin
        if (
            outcome.accepted
            and origin is not None
            and now > origin + span.pts_start + self.engine.delay_seconds - 0.1
        ):
            self.counts["tardy_spans"] += 1  # after span_start_arrival + D - 0.1, but its audio had not left
        late = outcome.late or outcome.leaked_samples > 0
        if late:
            self.counts["late_spans"] += 1
            log.warning("late-span: mute span arrived after (part of) its audio was emitted")
            self._publish_timing(
                "late_span",
                round(span.pts_start * self.fps),
                span.pts_start,
                min(span.completed_at, now),
                now,
            )
        if self.safety is None or not (outcome.accepted or outcome.late):
            return
        if late:
            self.safety.mute(span, late=True, partial_ms=round(outcome.leaked_samples * 1000 / RATE))
        if outcome.accepted:
            self.safety.mute(span)  # a revised span republishes the same event_id with its new duration

    def on_mask_request(self, request: MaskRequest) -> None:
        """Audio-originated requests (FULL_FRAME, MOUTH_OF_SPEAKER). REGION masks come from lanes only."""
        if request.kind is MaskKind.REGION:
            self.counts["rejected_requests"] += 1
            return
        self.engine.submit_request(request)

    def _apply_retro_audio(self) -> None:
        """Retroactive hold (D-23): zero the held PTS range in the ring now, and keep it as a hard-silence
        range in the mute book so audio that reaches the ring late for that time is silenced too."""
        for window in self.engine.pop_retro_ranges():
            start, end = round(window.pts_start * RATE), round(window.pts_end * RATE)
            self.ring.zero(start, end)
            self.mutes.add_silence(start, end)

    # ------------------------------------------------------------------ the tick

    def step(self, now: float | None = None) -> int:
        """One tick: drain lane results, decide due frames, emit them. Returns how many were emitted."""
        now = self.clock() if now is None else now
        self._check_sink(now)
        self._drain_lanes(now)
        self._sync_lanes(now)
        self._apply_retro_audio()
        self._check_audio_watchdog(now)
        emitted = 0
        for decision in self.engine.poll(now):
            self._emit(decision, now)
            emitted += 1
        if self.status is not None:
            self.status.tick(now, self._status_state(now))
        if self.engine.drained:
            self.finished.set()
        return emitted

    def _check_sink(self, now: float) -> None:
        healthy = bool(getattr(self.sink, "healthy", True))
        if not healthy and not self._egress_held:
            self._egress_held = True
            self.hold(HoldSource.AUTO_EGRESS, True, now)
        elif healthy and self._egress_held:
            self._egress_held = False
            self.hold(HoldSource.AUTO_EGRESS, False, now)

    def _drain_lanes(self, now: float) -> None:
        if self.lane_host is None:
            return
        try:
            results = self.lane_host.poll()
        except Exception as exc:
            self.counts["lane_errors"] += 1
            log.warning("lane host poll failed: %s", type(exc).__name__)
            return
        for result in results:
            self.engine.submit_result(result)
            started = self._arrivals.get(result.frame_seq, result.completed_at)
            self._publish_timing(
                f"lane_{result.lane.value}", result.frame_seq, result.pts, started, result.completed_at
            )

    def _sync_lanes(self, now: float) -> None:
        """Follow the host's active lanes (a category toggle can add or remove a lane)."""
        if self.lane_host is None or now - self._lanes_checked < 1.0:
            return
        self._lanes_checked = now
        lanes = tuple(sorted(self.lane_host.active_lanes, key=lambda lane: lane.value))
        if set(lanes) != set(self.engine.lanes):
            self.engine.set_lanes(lanes)

    def _status_state(self, now: float) -> str:
        if self._stop.is_set():
            return "stopping"
        if self._state == "error":
            return "error"
        if self._last_emit_at is not None and now - self._last_emit_at < 1.5:
            return "live"
        return "waiting_ingest" if self._threads or self._last_emit_at is not None else self._state

    # ------------------------------------------------------------------ emit

    def _emit(self, decision: FrameDecision, now: float) -> None:
        began = time.monotonic()
        self._apply_retro_audio()
        decision = self.engine.recheck(decision)
        composed = self._compose(decision)
        if self.engine.control.state == HOLD and composed.state != HOLD:
            # a hold arrived while painting: slate this frame instead (hold re-check, docs 5.3)
            decision = self.engine.recheck(decision)
            composed = self._compose(decision)
        composed = self._burn_captions(decision, composed)
        composed_at = time.monotonic()
        audio = self._audio_slice(decision, composed)
        written_from = time.monotonic()
        try:
            self.sink.write(decision.pts, composed.image, audio)
        except Exception as exc:  # a failing sink must not stop the clock
            self.counts["sink_errors"] += 1
            log.warning("sink write failed: %s", type(exc).__name__)
        written = time.monotonic()
        self._after_emit(decision, composed, now, began, composed_at, written_from, written)

    def _compose(self, decision: FrameDecision) -> Composed:
        try:
            return self.compositor.compose(decision, self._highwater)
        except Exception as exc:  # never emit unpainted pixels: a failure slate is the safe output
            self.counts["compose_errors"] += 1
            log.error("compose failed: %s", type(exc).__name__)
            return Composed(
                self.compositor.presenter.store.get("FAILURE").copy(), "slate", HOLD, "FAILURE", 0, 0.0, False
            )

    def _burn_captions(self, decision: FrameDecision, composed: Composed) -> Composed:
        """Last compositing step, after every mask. Only a frame that really goes out as video (painted
        LIVE output, never a slate, a PROTECT fill or a hold) gets a caption, and none while the audio is
        unchecked (the text comes from audio that has not been vetted)."""
        if (
            not self.captions.enabled
            or composed.state != LIVE
            or composed.kind not in ("frame", "solid")
            or self._audio_stale
        ):
            return composed
        began = time.monotonic()
        try:
            image = composed.image if composed.image.flags.writeable else composed.image.copy()
            drawn = self.captions.render(image, decision.pts)
        except Exception as exc:  # never let a caption bug stop or unmask a frame
            self.counts["caption_errors"] += 1
            log.warning("caption render failed: %s", type(exc).__name__)
            return composed
        if drawn:
            composed.image = image
            ended = time.monotonic()
            self.counts["caption_frames"] += 1
            self.caption_ms.append((ended - began) * 1000.0)
            self._publish_timing("caption", decision.seq, decision.pts, began, ended)
        return composed

    def _audio_slice(self, decision: FrameDecision, composed: Composed) -> np.ndarray:
        per_frame = RATE / self.fps
        target = decision.pts * RATE
        if self._cursor_reset:
            self._cursor_reset = False
            self._acursor = None
            self.mutes.reset_cursor()
        if self._acursor is None or abs(self._acursor - target) > RESYNC_SAMPLES:
            if self._acursor is not None:
                self.counts["audio_resyncs"] += 1
            self._acursor = target
        first = round(self._acursor)
        self._acursor += per_frame
        n = round(self._acursor) - first
        if composed.audio_silent:  # HOLD: fully silent, whatever the book holds
            self.mutes.advance(first + n)
            return np.zeros(n, np.int16)
        began = time.monotonic()
        pcm = self.ring.read(first, n)
        self.mutes.apply(pcm, first)  # also marks [first, first + n) as emitted (late-span test)
        if self._audio_stale:  # the audio module is silent: nothing it has not checked goes out
            pcm[:] = 0
        elif (
            self.audio_host is not None
            and self.audio_watchdog_s
            and (first + n) / RATE > self._checked_until - self.audio_vouch_margin_s
        ):
            # no accepted heartbeat has vouched for these samples yet (host not started, or its pts is stuck)
            pcm[:] = 0
            self.counts["audio_unvouched"] += 1
        self._mute_window = (began, time.monotonic())
        self.mute_ms.append((self._mute_window[1] - began) * 1000.0)
        return pcm

    def _after_emit(
        self,
        decision: FrameDecision,
        composed: Composed,
        now: float,
        began: float,
        composed_at: float,
        written_from: float,
        written: float,
    ) -> None:
        state = composed.state
        self.counts["emitted"] += 1
        self.counts[state] += 1
        if composed.kind == "slate" and state == "HOLD" and decision.state != HOLD:
            self.counts["fenced"] += 1
        self.emit_lag.append(decision.emit_lag)
        self.compose_ms.append((composed_at - began) * 1000.0)
        self._last_emit_at = now
        self._last_pts = decision.pts
        if self.safety is not None:
            self.safety.frame(decision.pts, decision.masks if state == "LIVE" else ())
            self.safety.state(state, decision.reason, decision.pts)
        if self.status is not None:
            self.status.observe(now, decision.arrival, decision.protect_age)
        if self.bus is not None:
            seq, pts = decision.seq, decision.pts
            self._publish_timing("compose", seq, pts, began, composed_at)
            self._publish_timing("sink_write", seq, pts, written_from, written)
            if self._mute_window is not None:
                self._publish_timing("audio_mute", seq, pts, *self._mute_window)
                self._mute_window = None
            if decision.arrival is not None:
                # keyed by round(pts * fps), the key eval uses to align the tap and the relay's egress_write
                # (the relay's own frame counter drifts from it after a dropped or late frame)
                self._publish_timing(
                    "engine_delay", round(pts * self.fps), pts, decision.arrival, self.clock()
                )
            self._publish_timing(
                "emit_lag", seq, pts, decision.deadline, max(decision.deadline, decision.emitted_at)
            )

    # ------------------------------------------------------------------ threads

    def start(self) -> None:
        if self._threads:
            return
        self._state = "waiting_ingest"  # the caller (engine.main) starts the lane and audio hosts first
        for name, target in (
            ("engine-ingest-video", self._video_loop),
            ("engine-ingest-audio", self._audio_loop),
            ("engine-tick", self._tick_loop),
        ):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def _video_loop(self) -> None:
        try:
            for frame in self.source.frames():
                if self._stop.is_set():
                    return
                self.on_frame(frame)
        except Exception as exc:
            self._state = "error"
            log.error("video ingest failed: %s", type(exc).__name__)
            return
        if not self._stop.is_set():
            self.engine.end()  # the source ended: drain what is buffered, then finish

    def _audio_loop(self) -> None:
        try:
            for block in self.source.audio_blocks():
                if self._stop.is_set():
                    return
                index, pcm = block
                self.on_audio(index, pcm, getattr(block, "generation", None))
        except Exception as exc:
            self._state = "error"
            log.error("audio ingest failed: %s", type(exc).__name__)

    def _tick_loop(self) -> None:
        while not self._stop.is_set():
            try:
                emitted = self.step()
            except Exception as exc:  # a bug in one tick must not end the stream clock
                self.counts["compose_errors"] += 1
                log.error("tick failed: %s", type(exc).__name__)
                emitted = 0
            if self.finished.is_set():
                return
            if emitted < 4:
                self._stop.wait(self.tick_s)

    def stop(self, join_timeout: float = 3.0) -> None:
        """Stop the threads, close the source (to unblock ingest) and flush open events."""
        self._stop.set()
        try:
            self.source.close()
        except Exception as exc:
            log.warning("source close failed: %s", type(exc).__name__)
        for thread in self._threads:
            thread.join(join_timeout)
        self._threads.clear()
        if self.safety is not None:
            self.safety.close(self.clock())
        if self.status is not None:
            self.status.tick(self.clock() + 10.0, "stopping")
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe = []
        taps, self._taps = self._taps, ()
        for tap in taps:
            tap.close()
        self.timing_log.close()
        self.engine.close()

    @property
    def stats(self) -> Mapping[str, Any]:
        out: dict[str, Any] = dict(self.counts)
        out["audio_ring_bytes"] = self.ring.nbytes
        out["audio_rejected_samples"] = self.ring.rejected
        out["mute_ranges"] = len(self.mutes)
        out["mute_replaced"] = self.mutes.replaced
        return out
