"""Audio orchestrator pipeline (docs/specs/audio.md 5.2-5.4, 5.8; task T3.2).

16 kHz chunks -> VAD segmenter -> STT race -> transcript window -> PII detector -> padded ``MuteSpan`` (+ mouth
``MaskRequest``). The spans go out on the bus as soon as they are ready, which has to be before
``arrival + D - 0.1`` of the audio they cover (the engine's hard acceptance time).

Fail-safe: a voiced segment that has no verdict by ``arrival + D - 0.3`` (or whose speech-to-text failed on every
provider) is muted whole as ``SPOKEN_UNVERIFIED``/``failsafe``. Music and non-voiced audio pass. Transcript
text lives only in memory and on the local-only ``Topic.TRANSCRIPT``; logs carry counts, ids and timings.

Everything is driven by ``clock`` (default ``time.monotonic``); tests run it on a virtual-time loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import numpy as np

from audio.orchestrator.failsafe import ArrivalMap, Coverage, chunk_failsafe, hard_deadline, soft_deadline
from audio.orchestrator.live_config import LiveAudioSettings
from audio.orchestrator.mute import Carry, PlannedSpan, SpanPlanner
from audio.orchestrator.race import SttAllFailed, SttRace
from audio.orchestrator.window import TranscriptWindowBuilder
from audio.segmenter import Segment, Segmenter
from contracts.config import Config
from contracts.detection import MaskRequest, MuteSpan
from contracts.enums import Category, MaskKind, MaskStyle, Topic
from contracts.events import StageTiming
from contracts.media import AudioChunk
from contracts.protocols import EventBus, PiiSpanDetector

log = logging.getLogger("audio.orchestrator")

WINDOW_S = 8.0  # transcript window kept for the rules (5.4)
RING_S = 8.0  # recent audio kept for the Groq context (>= 3 s requests)
MIN_PAD_S = 0.3  # lead and trail padding around a spoken PII span: ASR word times are only good to ~0.2 s
FAILSAFE_PAD_S = 0.25  # VAD end-of-speech trims soft word tails: pad generously (the fail-safe is final)
MAX_BOUNDS = 64


class ConfigFeedEnded(RuntimeError):
    """The ``Topic.CONFIG`` subscription stopped delivering (closed or exhausted)."""


class ThinTranscript(RuntimeError):
    """A voiced segment came back with no words, or far fewer than its speech can hold."""


MIN_WORDS_PER_S = 1.0  # a voiced second holds at least this many words, else the transcript is not trusted


def too_thin(seg: Segment, tr: Any) -> bool:
    """Voiced speech (VAD) with an empty or near-empty transcript is unverified, not "no speech": a provider that
    answers 200 with nothing (rate-limit page, silence heard, hallucination filter) must not let speech through."""
    from audio.orchestrator.window import norm  # noqa: PLC0415

    n = sum(1 for w in tr.words if norm(w.text))
    if n == 0 and any(norm(t) for t in str(tr.text).split()):
        n = 1  # text without word records still counts as heard
    return n < max(1, int(seg.speech_ms / 1000.0 * MIN_WORDS_PER_S))


class PcmRing:
    """The last few seconds of 16 kHz audio on the source clock (for requests that need preceding audio)."""

    def __init__(self, seconds: float = RING_S, rate: int = 16_000) -> None:
        self.rate = rate
        self.max_samples = int(seconds * rate)
        self._chunks: deque[tuple[float, np.ndarray]] = deque()
        self._n = 0

    def add(self, pts_start: float, pcm: Any) -> None:
        a = np.asarray(pcm, dtype=np.int16)
        self._chunks.append((pts_start, a))
        self._n += len(a)
        while self._n > self.max_samples and len(self._chunks) > 1:
            _, old = self._chunks.popleft()
            self._n -= len(old)

    def slice(self, lo: float, hi: float) -> tuple[np.ndarray, float]:
        """Audio of ``[lo, hi]`` (zero-filled where the ring has a gap); returns ``(pcm, start_pts)``."""
        if not self._chunks:
            return np.zeros(0, dtype=np.int16), lo
        lo = max(lo, self._chunks[0][0])
        n = max(0, int(round((hi - lo) * self.rate)))
        out = np.zeros(n, dtype=np.int16)
        for pts, a in self._chunks:
            i0 = int(round((pts - lo) * self.rate))
            i1 = i0 + len(a)
            if i1 <= 0 or i0 >= n:
                continue
            s0, s1 = max(i0, 0), min(i1, n)
            out[s0:s1] = a[s0 - i0 : s1 - i0]
        return out, lo


class AudioPipeline:
    """Event-loop side of the audio module. ``on_chunk`` must be called on the loop thread, in stream order."""

    def __init__(
        self,
        config: Config,
        bus: EventBus,
        segmenter: Segmenter,
        race: SttRace,
        detector: PiiSpanDetector,
        *,
        clock: Callable[[], float] = time.monotonic,
        planner: SpanPlanner | None = None,
    ) -> None:
        a = config.audio
        self.cfg = config
        self.bus = bus
        self.segmenter = segmenter
        self.race = race
        self.detector = detector
        self.clock = clock
        self.delay = float(config.delay_seconds)
        self.planner = planner or SpanPlanner(pad_s=max(a.pad_s, MIN_PAD_S))
        self.window = TranscriptWindowBuilder(span_s=max(WINDOW_S, a.watch_window_s + 2.0))
        self.ring = PcmRing()
        self.arrivals = ArrivalMap()
        self.coverage = Coverage()
        self.live = LiveAudioSettings(a.languages, a.mute_mode)
        self.live.load_categories(getattr(config, "categories", None) or {})
        self._bounds: dict[int, tuple[float, float]] = {}
        self._carry: Carry | None = None
        self._prev_end: float | None = None
        self._last_gate: asyncio.Future[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._n_span = 0
        self.stats: dict[str, int] = dict.fromkeys(
            (
                "segments",
                "voiced",
                "verdicts",
                "failsafe",
                "spans",
                "late_spans",
                "stt_failed",
                "dropped_chunks",
                "chunk_failsafe",
                "thin_transcript",
                "config_drops",
                "config_restarts",
            ),
            0,
        )
        self.latencies: deque[float] = deque(maxlen=4096)  # first sample of the segment -> span ready
        self.margins: deque[float] = deque(
            maxlen=4096
        )  # engine hard deadline minus span ready (negative: late)
        self.leaked_s = 0.0

    # ------------------------------------------------------------------ input
    def on_chunk(self, chunk: AudioChunk) -> None:
        if chunk.sample_rate != 16_000:
            self.stats["dropped_chunks"] += 1
            self.failsafe_chunk(chunk, "bad_rate")  # cannot be analysed: never let it through unmuted
            return
        self.ring.add(chunk.pts_start, chunk.pcm)
        for seg in self.segmenter.feed_chunk(chunk):
            try:
                self._start(seg)
            except Exception as exc:  # a segment we cannot track must not pass unmuted
                log.warning(
                    "audio segment %d start failed (%s): fail-safe mute", seg.seg_id, type(exc).__name__
                )
                if seg.voiced:
                    self._failsafe(seg, "error")

    async def watch_config(self, sub: Any, poll_s: float = 1.0) -> None:
        """Feed ``Topic.CONFIG`` changes from one bus subscription into the live settings (until cancelled).

        A subscription that dropped an event (``sub.dropped`` grew) may have missed a toggle: the settings fall back
        to fail-closed defaults at once (checked every ``poll_s`` and before each event). Raises ``ConfigFeedEnded``
        if the subscription ends (the caller restarts it, see ``supervise_config``)."""
        it = sub.__aiter__()
        seen = int(getattr(sub, "dropped", 0) or 0)
        nxt: asyncio.Future[Any] | None = None
        try:
            while True:
                if nxt is None:
                    nxt = asyncio.ensure_future(it.__anext__())
                done, _ = await asyncio.wait({nxt}, timeout=poll_s)
                now = int(getattr(sub, "dropped", 0) or 0)
                if now != seen:
                    seen = now
                    self.stats["config_drops"] += 1
                    log.error("audio config feed dropped an event: spoken categories reset to fail-closed")
                    self.live.fail_closed()
                if not done:
                    continue
                fut, nxt = nxt, None
                try:
                    _topic, ev = fut.result()
                except StopAsyncIteration:
                    raise ConfigFeedEnded from None
                try:
                    self.live.apply(ev.diff)
                except Exception as exc:  # never let a bad event stop the feed
                    log.warning("audio config event ignored (%s)", type(exc).__name__)
        finally:
            if nxt is not None and not nxt.done():
                nxt.cancel()
                await asyncio.gather(nxt, return_exceptions=True)

    async def supervise_config(
        self,
        first_sub: Any,
        resubscribe: Callable[[], Any],
        *,
        backoff_s: tuple[float, ...] = (0.5, 1.0, 2.0, 5.0),
        poll_s: float = 1.0,
    ) -> None:
        """Keep ``watch_config`` alive: when it dies, log at error level, fall back to fail-closed settings, wait
        (backoff) and subscribe again. Runs until cancelled."""
        sub: Any = first_sub
        attempt = 0
        while True:
            try:
                if sub is None:
                    sub = resubscribe()
                await self.watch_config(sub, poll_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("audio config feed died (%s): fail-closed, restarting", type(exc).__name__)
            self.stats["config_restarts"] += 1
            self.live.fail_closed()  # events may have been missed while the feed was down
            closer = getattr(sub, "close", None)
            sub = None
            if closer is not None:
                try:
                    closer()
                except Exception:  # noqa: BLE001 - already failing
                    pass
            await asyncio.sleep(backoff_s[min(attempt, len(backoff_s) - 1)])
            attempt += 1

    def failsafe_chunk(self, chunk: AudioChunk, why: str) -> None:
        """Mute the whole chunk (loop thread). Never raises, never logs text or audio."""
        try:
            self.stats["chunk_failsafe"] += 1
            now = self.clock()
            probe, _ = chunk_failsafe(chunk, "probe", now, self.live.mute_mode, why)
            for lo, hi in self.coverage.uncovered(probe.pts_start, probe.pts_end, min_len=0.0):
                self._n_span += 1
                span, mask = chunk_failsafe(chunk, f"au-{self._n_span}", now, self.live.mute_mode, why)
                span = replace(span, pts_start=lo, pts_end=hi)
                mask = replace(mask, pts_start=lo, pts_end=hi, frame_pts=lo)
                self.coverage.add(lo, hi)
                self.stats["spans"] += 1
                self._account(span, now)
                self.bus.publish(Topic.MUTE_SPAN, span)
                self.bus.publish(Topic.MASK_REQUEST, mask)
        except Exception as exc:
            log.error("chunk fail-safe could not be published (%s)", type(exc).__name__)

    def flush(self) -> None:
        """End of stream: close the open segment."""
        for seg in self.segmenter.flush():
            self._start(seg)

    async def drain(self) -> None:
        """Wait for every segment in flight (each finishes by its soft deadline at the latest)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
        for stt in self.race.providers.values():
            closer = getattr(stt, "aclose", None)
            if closer is not None:
                try:
                    await closer()
                except Exception as exc:
                    log.debug("closing an stt adapter failed: %s", type(exc).__name__)

    # ------------------------------------------------------------------ segments
    def _start(self, seg: Segment) -> None:
        self.stats["segments"] += 1
        self.arrivals.add(seg.pts_start, seg.arrival)
        self._bounds[seg.seg_id] = (seg.pts_start, seg.pts_end)
        while len(self._bounds) > MAX_BOUNDS:
            self._bounds.pop(next(iter(self._bounds)))
        self.coverage.prune(seg.pts_start - 30.0)
        self._timing("segment", seg, seg.arrival, self.clock())
        if not seg.voiced:
            return  # music, noise and silence pass
        self.stats["voiced"] += 1
        loop = asyncio.get_running_loop()
        prev, done = self._last_gate, loop.create_future()
        self._last_gate = done
        task = loop.create_task(self._run(seg, prev, done), name=f"audio-seg-{seg.seg_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, seg: Segment, prev: asyncio.Future[None] | None, done: asyncio.Future[None]) -> None:
        deadline = soft_deadline(seg.arrival, self.delay)
        try:
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise TimeoutError
            await asyncio.wait_for(self._verdict(seg, prev), remaining)
        except TimeoutError:
            self._failsafe(seg, "deadline")
        except SttAllFailed:
            self.stats["stt_failed"] += 1
            self._failsafe(seg, "stt_failed")
        except ThinTranscript:
            self.stats["thin_transcript"] += 1
            self._failsafe(seg, "thin_transcript")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never leave voiced speech without a verdict
            log.warning("audio segment %d failed (%s): fail-safe mute", seg.seg_id, type(exc).__name__)
            self._failsafe(seg, "error")
        finally:
            if not done.done():
                done.set_result(None)

    def _context(self, seg: Segment, min_s: float) -> tuple[Any, float]:
        return self.ring.slice(seg.pts_end - min_s, seg.pts_end)

    async def _verdict(self, seg: Segment, prev: asyncio.Future[None] | None) -> None:
        t0 = self.clock()
        tr = await self.race.transcribe(seg, self.live.lang_hint, self._context)
        self._timing("stt", seg, t0, self.clock())
        if too_thin(seg, tr):
            self.bus.publish(Topic.TRANSCRIPT, tr)  # still local-only; the guard sees it
            raise ThinTranscript
        if prev is not None:
            await asyncio.shield(prev)  # commit in stream order (the previous one ends by its own deadline)
        self.bus.publish(Topic.TRANSCRIPT, tr)  # local only: never bridged
        win = self.window.add(tr.words, seg.pts_start, seg.pts_end, self._prev_end)
        self._prev_end = seg.pts_end
        spans = list(self.planner.leading_numbers(self._carry, win))
        spans += await self.detector.detect(win)
        spans = [
            s for s in spans if self.live.allows(s.category, s.confidence)
        ]  # categories the streamer turned off
        planned = self.planner.plan(spans, win, self._bounds, seg.pts_end)
        tail = [p for p in planned if p.tail_extended]
        self._carry = (
            Carry(tail[-1].anchor_t1, tail[-1].category, tail[-1].confidence, tail[-1].carry_kind)
            if tail
            else None
        )
        self.stats["verdicts"] += 1
        self._emit(planned, seg)

    # ------------------------------------------------------------------ fail-safe
    def _failsafe(self, seg: Segment, why: str) -> None:
        self._carry = Carry(
            seg.pts_end, Category.SPOKEN_UNVERIFIED, 0.5
        )  # digits may continue into the next one
        if not self.cfg.audio.failsafe_mute_voiced:
            return
        self.stats["failsafe"] += 1
        lo = max(0.0, seg.pts_start - FAILSAFE_PAD_S)
        hi = seg.pts_end + FAILSAFE_PAD_S
        self._emit(
            [PlannedSpan(lo, hi, Category.SPOKEN_UNVERIFIED, 0.5, "failsafe_" + why, "failsafe", False, hi)],
            seg,
        )

    # ------------------------------------------------------------------ output
    def _emit(self, planned: list[PlannedSpan], seg: Segment) -> None:
        now = self.clock()
        for p in planned:
            # a fail-safe span keeps every sliver: a 10 ms gap at a segment edge would let a syllable through
            min_len = 0.0 if p.granularity == "failsafe" else 0.02
            for lo, hi in self.coverage.uncovered(p.pts_start, p.pts_end, min_len=min_len):
                self._n_span += 1
                sid = f"au-{self._n_span}"
                span = MuteSpan(
                    sid,
                    lo,
                    hi,
                    p.category,
                    p.granularity,
                    p.confidence,
                    p.reason,
                    now,
                    self.live.mute_mode,  # type: ignore[arg-type]
                )
                self.coverage.add(lo, hi)
                self.stats["spans"] += 1
                self._account(span, now)
                self.bus.publish(Topic.MUTE_SPAN, span)
                self.bus.publish(
                    Topic.MASK_REQUEST,
                    MaskRequest(
                        f"{sid}-mouth",
                        "audio",
                        p.category,
                        MaskKind.MOUTH_OF_SPEAKER,
                        (),
                        lo,
                        lo,
                        hi,
                        MaskStyle.BLUR,
                        p.confidence,
                        p.reason,
                        now,
                    ),  # fmt: skip
                )
        if planned:
            self._timing("span_ready", seg, seg.arrival, now, pts=planned[0].pts_start)
            self.latencies.append(now - seg.arrival)

    def _account(self, span: MuteSpan, now: float) -> None:
        arr = self.arrivals.arrival_of(span.pts_start)
        if arr is None:
            return
        margin = hard_deadline(arr, self.delay) - now
        self.margins.append(margin)
        if margin < 0:
            self.stats["late_spans"] += 1
            gone = self.arrivals.pts_at(now - self.delay)  # audio before this pts has left the delay buffer
            if gone is not None:
                self.leaked_s += max(0.0, min(span.pts_end, gone) - span.pts_start)

    def _timing(self, stage: str, seg: Segment, t0: float, t1: float, pts: float | None = None) -> None:
        self.bus.publish(
            Topic.TIMING,
            StageTiming(stage, seg.seg_id, seg.pts_start if pts is None else pts, t0, max(t0, t1)),
        )

    def stage_timing(self, stage: str, t0: float, t1: float) -> None:
        """Callback for the PII detector (``pii_rules``, ``pii_llm``)."""
        self.bus.publish(Topic.TIMING, StageTiming(stage, self.stats["segments"], None, t0, max(t0, t1)))
