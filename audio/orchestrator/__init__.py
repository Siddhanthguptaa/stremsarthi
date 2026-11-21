"""Audio orchestrator (T3.2): ``audio.orchestrator:build`` returns the ``AudioHost`` the engine loads.

The host runs an asyncio loop in its own thread; ``feed`` only hands a chunk over (never blocks the engine's
audio ingest). Spans, mouth-blur requests and timings go out on the bus; the transcript only on the
local-only ``Topic.TRANSCRIPT``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from audio.orchestrator.failsafe import chunk_failsafe
from audio.orchestrator.fallback_pii import DigitRunPiiDetector, load_pii_detector
from audio.orchestrator.pipeline import AudioPipeline
from audio.orchestrator.race import SttAllFailed, SttRace
from audio.segmenter import Segmenter
from audio.stt.groq import GroqSTT
from audio.stt.local import LocalWhisperSTT
from audio.stt.sarvam import SarvamSTT
from audio.vad import EnergyFrameModel, SileroOnnx
from contracts.config import Config
from contracts.enums import Topic
from contracts.events import StageTiming
from contracts.media import AudioChunk
from contracts.protocols import SpeechToText

log = logging.getLogger("audio.orchestrator")

HEARTBEAT_S = (
    1.0  # StageTiming("audio_heartbeat") period: silence on Topic.TIMING means audio is dead or stuck
)
MAX_PENDING_CHUNKS = 500  # 10 s of 20 ms chunks: beyond this the loop is stuck, so drop instead of queueing

__all__ = ["AudioOrchestrator", "AudioPipeline", "DigitRunPiiDetector", "SttAllFailed", "SttRace", "build"]


def make_segmenter(config: Config) -> Segmenter:
    try:
        # Model downloads can outlast the engine's audio startup deadline.
        return Segmenter.from_config(config, SileroOnnx(download=False))
    except Exception as exc:  # missing Silero weights must not stop the audio path: errs toward muting
        log.warning("Silero VAD unavailable (%s): using the energy detector", type(exc).__name__)
        return Segmenter.from_config(config, EnergyFrameModel())


def make_providers(config: Config) -> dict[str, SpeechToText]:
    out: dict[str, SpeechToText] = {}
    sarvam = SarvamSTT.from_config(config)
    if sarvam.available:
        out["sarvam"] = sarvam
    groq = GroqSTT.from_config(config)
    if groq.available:
        out["groq"] = groq
    out["local"] = LocalWhisperSTT.from_config(config)
    return out


class AudioOrchestrator:
    """``contracts.AudioHost``: ``start`` / non-blocking ``feed`` / ``close``."""

    def __init__(self, config: Config, bus: Any, pipeline: AudioPipeline | None = None) -> None:
        self.config = config
        self.bus = bus
        self._pipeline = pipeline
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._pending = 0
        self._n_failsafe = 0
        self._start_error: str | None = None
        self._warm_task: asyncio.Task[None] | None = None
        self._hb_task: asyncio.Task[None] | None = None
        self._cfg_task: asyncio.Task[None] | None = None
        self._cfg_sub: Any = None
        self._hb_seq = 0
        self._last_pts: float | None = None
        self.heartbeat_s = HEARTBEAT_S
        self.heartbeat_clock = time.monotonic
        self.dropped = 0
        self.failsafe_chunks = 0
        self.failsafe_publish_errors = 0

    @property
    def pipeline(self) -> AudioPipeline | None:
        return self._pipeline

    # ------------------------------------------------------------------ AudioHost
    def start(self) -> None:
        if self._thread is not None:
            return
        self._ready.clear()
        self._start_error = None
        thread = threading.Thread(target=self._run, name="audio-orchestrator", daemon=True)
        self._thread = thread
        thread.start()
        if not self._ready.wait(10.0):
            self._abort_start(thread)
            raise RuntimeError("audio orchestrator did not start")
        if self._start_error is not None:  # a half-started host would discard every chunk unmuted
            err = self._start_error
            self._abort_start(thread)
            raise RuntimeError(f"audio pipeline failed to build ({err})")

    def _abort_start(self, thread: threading.Thread) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        thread.join(2.0)
        self._loop = self._thread = None

    def feed(self, chunk: AudioChunk) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            self._failsafe_chunk(chunk, "not_running")
            return
        with self._lock:
            if self._pending >= MAX_PENDING_CHUNKS:
                self.dropped += 1
                full = True
            else:
                self._pending += 1
                full = False
        if full:  # the loop is stuck: mute from the caller's thread (the bus is thread-safe)
            self._failsafe_chunk(chunk, "queue_full")
            return
        try:
            loop.call_soon_threadsafe(self._on_chunk, chunk)
        except RuntimeError:  # loop closed while feeding
            with self._lock:
                self._pending -= 1
            self._failsafe_chunk(chunk, "not_running")

    def _failsafe_chunk(self, chunk: AudioChunk, why: str) -> None:
        """Publish a whole-chunk fail-safe mute. Never raises into the caller, never logs text or audio."""
        try:
            with self._lock:
                self._n_failsafe += 1
                n = self._n_failsafe
                self.failsafe_chunks += 1
            mode = (
                self._pipeline.live.mute_mode if self._pipeline is not None else self.config.audio.mute_mode
            )
            span, mask = chunk_failsafe(chunk, f"au-fs-{n}", time.monotonic(), mode, why)
            self.bus.publish(Topic.MUTE_SPAN, span)
            self.bus.publish(Topic.MASK_REQUEST, mask)
        except Exception as exc:
            self.failsafe_publish_errors += 1
            log.error("chunk fail-safe could not be published (%s)", type(exc).__name__)

    def close(self) -> None:
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return
        fut = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
        try:
            fut.result(5.0)
        except Exception as exc:
            log.warning("audio shutdown: %s", type(exc).__name__)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5.0)
        self._loop = self._thread = None

    # ------------------------------------------------------------------ loop thread
    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            if self._pipeline is None:
                self._pipeline = self._build_pipeline()
            self._start_config_feed(loop)
            self._warm_task = loop.create_task(self._warmup())
            self._hb_task = loop.create_task(self._heartbeat())  # only once the pipeline exists
            self._ready.set()
            loop.run_forever()
        except Exception as exc:  # start() raises: the host must not look alive with no pipeline
            self._start_error = type(exc).__name__
        finally:
            try:
                self._cancel_pending(loop)
            finally:
                self._ready.set()
                loop.close()

    def _start_config_feed(self, loop: asyncio.AbstractEventLoop) -> None:
        """Subscribe to ``Topic.CONFIG`` before the engine can publish its first (burst of) change(s)."""
        pipe = self._pipeline
        if pipe is None:
            return
        try:
            self._cfg_sub = self.bus.subscribe([Topic.CONFIG])
            self._cfg_task = loop.create_task(
                pipe.supervise_config(self._cfg_sub, lambda: self._resubscribe_config())
            )
        except Exception as exc:  # no live toggles, defaults (everything on) stay: never fewer mutes
            log.error("audio config feed unavailable (%s): defaults stay", type(exc).__name__)

    def _resubscribe_config(self) -> Any:
        self._cfg_sub = self.bus.subscribe([Topic.CONFIG])
        return self._cfg_sub

    @staticmethod
    def _cancel_pending(loop: asyncio.AbstractEventLoop) -> None:
        """Cancel and reap every task still on the loop so none is destroyed while pending."""
        tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))

    def _build_pipeline(self) -> AudioPipeline:
        cfg = self.config
        a = cfg.audio
        race = SttRace(
            make_providers(cfg),
            order=a.stt_order,
            hedge_s=a.race_hedge_s,
            rpm={"groq": float(a.groq_rpm)},
        )
        holder: dict[str, AudioPipeline] = {}
        detector = load_pii_detector(cfg, on_timing=lambda s, t0, t1: holder["p"].stage_timing(s, t0, t1))
        pipe = AudioPipeline(cfg, self.bus, make_segmenter(cfg), race, detector)
        holder["p"] = pipe
        return pipe

    async def _heartbeat(self) -> None:
        """Liveness beat on ``Topic.TIMING``, from the loop thread: a stuck or dead loop stops it."""
        while True:
            await asyncio.sleep(self.heartbeat_s)
            if self._pipeline is None:
                continue
            try:
                t = self.heartbeat_clock()
                self._hb_seq += 1
                self.bus.publish(
                    Topic.TIMING, StageTiming("audio_heartbeat", self._hb_seq, self._last_pts, t, t)
                )
            except Exception as exc:  # a broken bus must not kill the beat or the audio path
                log.warning("audio heartbeat failed: %s", type(exc).__name__)

    async def _warmup(self) -> None:
        local = self._pipeline.race.providers.get("local") if self._pipeline else None
        warm = getattr(local, "warmup", None)
        if warm is not None:
            try:
                await warm()
            except Exception as exc:  # the race disables it on first use
                log.info("local stt warm-up skipped (%s)", type(exc).__name__)

    def _on_chunk(self, chunk: AudioChunk) -> None:
        with self._lock:
            self._pending -= 1
        try:
            self._last_pts = float(chunk.pts_end)
        except Exception:  # malformed chunk: the fail-safe below still covers it
            pass
        pipe = self._pipeline
        if pipe is None:  # cannot happen after start() succeeded; mute rather than discard
            self._failsafe_chunk(chunk, "no_pipeline")
            return
        try:
            pipe.on_chunk(chunk)
        except Exception as exc:  # the audio path must never take the engine down, nor pass audio unmuted
            log.warning("audio chunk failed: %s", type(exc).__name__)
            pipe.failsafe_chunk(chunk, "error")

    async def _shutdown(self) -> None:
        hb = self._hb_task
        if hb is not None:
            hb.cancel()
            await asyncio.gather(hb, return_exceptions=True)
        cfg_task = self._cfg_task
        if cfg_task is not None:
            cfg_task.cancel()
            await asyncio.gather(cfg_task, return_exceptions=True)
        closer = getattr(self._cfg_sub, "close", None)
        if closer is not None:
            closer()
        warm = self._warm_task
        if warm is not None and not warm.done():
            warm.cancel()
        if warm is not None:
            await asyncio.gather(warm, return_exceptions=True)
        if self._pipeline is not None:
            self._pipeline.flush()
            try:
                await asyncio.wait_for(self._pipeline.drain(), 1.0)
            except TimeoutError:
                pass
            await self._pipeline.aclose()


def build(config: Config, bus: Any) -> AudioOrchestrator:
    """Entry point named by ``Config.runtime.modules['audio']``."""
    return AudioOrchestrator(config, bus)
