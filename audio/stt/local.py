# Adapted from PrivaStream (Apache-2.0):
#   src/models/audio/audio_redaction_server_faster_whisper.py  (faster-whisper call, int8 on CPU)
#   src/models/audio/training/src/whisper_processor.py          (absolute-offset pattern, DC removal and
#                                                                 peak normalisation)
# Changes: no Flask, no BERT NER, no librosa; word times come straight from faster-whisper and are
# shifted by the segment's source PTS (no character-to-word mapping, which drifts); near-silent input is
# skipped (Whisper hallucinates on it); one worker thread with load shedding instead of an unbounded queue.
# The upstream licence text is in audio/stt/LICENSE-privastream-Apache-2.0.
"""Local faster-whisper fallback (CTranslate2, int8 on CPU) behind the ``SpeechToText`` protocol.

Local Hindi quality is poor, so this engine is for spotting numbers and digits when the APIs are down, not
for captions. It runs in one worker thread so it never blocks the event loop, honours a timeout, and sheds
load (``SttBusy``) rather than building a backlog of segments that would be stale anyway.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from audio.models import WHISPER_REPOS, weights_dir
from audio.stt.base import (
    SttBusy,
    SttProviderError,
    SttUnavailable,
    interpolate_words,
    normalise_lang,
    pcm_duration_s,
    pcm_to_float32,
)
from contracts.config import Config
from contracts.speech import Transcript, Word

log = logging.getLogger("audio.stt.local")

# Model sizes by cost. When the requested weights are missing the engine takes the nearest SMALLER one first
# (a slower model would miss the delay window; the race then mutes), then a larger one as the last resort.
SIZE_ORDER = ("tiny", "base", "small")
REQUIRED_FILES = ("model.bin", "config.json", "tokenizer.json")

SILENCE_DBFS = -55.0  # below this RMS the segment is not sent to Whisper
NO_SPEECH_DROP = 0.8  # drop a Whisper segment that is probably non-speech ...
LOGPROB_DROP = -1.0  # ... and low confidence


def model_dir_for(size: str) -> Path:
    return weights_dir() / f"faster-whisper-{size}"


def model_present(size: str, base: Path | None = None) -> bool:
    d = (base / f"faster-whisper-{size}") if base is not None else model_dir_for(size)
    return all((d / f).is_file() for f in REQUIRED_FILES)


def candidate_sizes(requested: str) -> list[str]:
    """Order to try: the requested size, smaller ones (largest first), then larger ones (smallest first)."""
    if requested not in SIZE_ORDER:
        return list(SIZE_ORDER[1::-1]) + [SIZE_ORDER[2]]  # unknown name: base, tiny, small
    i = SIZE_ORDER.index(requested)
    return [requested, *reversed(SIZE_ORDER[:i]), *SIZE_ORDER[i + 1 :]]


def resolve_model_size(requested: str, base: Path | None = None) -> str | None:
    """The best AVAILABLE weight for ``requested`` (``None`` when no weights are on disk at all)."""
    for size in candidate_sizes(requested):
        if model_present(size, base):
            return size
    return None


def preprocess(pcm: Any) -> np.ndarray:
    """int16 to float32, DC removed, quiet audio lifted to a usable peak (PrivaStream's enhancement step)."""
    a = pcm_to_float32(pcm)
    if a.size == 0:
        return a
    a = a - float(np.mean(a))
    peak = float(np.max(np.abs(a)))
    if 0.02 <= peak < 0.9:
        a = a * (0.9 / peak)
    return a.astype(np.float32, copy=False)


def rms_dbfs(a: np.ndarray) -> float:
    if a.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(a, dtype=np.float64))))
    return 20.0 * float(np.log10(max(rms, 1e-6)))


class LocalWhisperSTT:
    name: ClassVar[str] = "local"

    def __init__(
        self,
        model_size: str = "base",
        *,
        device: str = "cpu",
        compute_type: str | None = None,
        cpu_threads: int = 4,
        model_dir: Path | str | None = None,
        timeout_s: float = 1.5,
        vad_filter: bool = False,
        beam_size: int = 1,
        max_pending: int = 2,
        model_factory: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.model_size = model_size  # what was asked for
        self.active_model_size: str | None = None  # what actually loaded (set by _load)
        self._pinned_dir = model_dir is not None
        self.device = device
        self.compute_type = compute_type or ("int8" if device == "cpu" else "int8_float16")
        self.cpu_threads = cpu_threads
        self.model_dir = Path(model_dir) if model_dir else model_dir_for(model_size)
        self.timeout_s = timeout_s
        self.vad_filter = vad_filter
        self.beam_size = beam_size
        self.max_pending = max_pending
        self._factory = model_factory
        self._clock = clock
        self._model: Any = None
        self._pending = 0
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ss-whisper")

    @classmethod
    def from_config(cls, config: Config, **kwargs: Any) -> LocalWhisperSTT:
        a = config.audio
        return cls(a.local_stt_model, device=a.local_stt_device, **kwargs)

    # ------------------------------------------------------------------ model
    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        if self._factory is not None:
            self._model = self._factory()
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise SttUnavailable("faster-whisper is not installed") from exc
        if self._pinned_dir:
            plan = [(self.model_size, self.model_dir)]
        else:
            plan = [(s, model_dir_for(s)) for s in candidate_sizes(self.model_size) if model_present(s)]
        if not plan:
            repo = WHISPER_REPOS.get(self.model_size, "?")
            raise SttUnavailable(
                f"no whisper weights in {weights_dir()}; fetch {repo} with audio.models.fetch_hf_model()"
            )
        for size, path in plan:
            if not (path / "model.bin").exists():
                continue
            try:
                self._model = WhisperModel(
                    str(path),
                    device=self.device,
                    compute_type=self.compute_type,
                    cpu_threads=self.cpu_threads if self.device == "cpu" else 0,
                    num_workers=1,
                )
            except Exception as exc:  # noqa: BLE001 - a corrupt or incompatible model: try the next one
                log.warning("local stt: whisper %s failed to load (%s)", size, type(exc).__name__)
                continue
            self.active_model_size = size
            self.model_dir = path
            if size != self.model_size:
                log.warning(
                    "local stt: whisper %s is not available, using %s (%s, %s)",
                    self.model_size,
                    size,
                    self.device,
                    self.compute_type,
                )
            else:
                log.info("local stt: using whisper %s (%s, %s)", size, self.device, self.compute_type)
            return self._model
        raise SttUnavailable(f"no usable whisper weights for {self.model_size!r}")

    async def warmup(self) -> None:
        """Load the model (and run one dummy decode) off the event loop so the first segment is not slow."""
        loop = asyncio.get_running_loop()

        def _warm() -> None:
            model = self._load()
            silence = np.zeros(16_000, dtype=np.float32)
            list(model.transcribe(silence, language="en", beam_size=1)[0])

        await loop.run_in_executor(self._pool, _warm)

    async def aclose(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ transcribe
    async def transcribe(self, pcm16k: Any, pts_start: float, lang_hint: str | None) -> Transcript:
        dur = pcm_duration_s(pcm16k)
        pts_end = pts_start + dur
        audio = preprocess(pcm16k)
        t_start = self._clock()
        if rms_dbfs(pcm_to_float32(pcm16k)) < SILENCE_DBFS:
            return Transcript(pts_start, pts_end, "", (), normalise_lang(lang_hint), "local", 0.0)
        with self._lock:
            if self._pending >= self.max_pending:
                raise SttBusy("local whisper is still busy")
            self._pending += 1
        lang = None if lang_hint in (None, "", "unknown", "auto") else normalise_lang(lang_hint, default="")
        if lang_hint == "hinglish":
            lang = "hi"
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(self._pool, self._run, audio, lang, pts_start, pts_end)
        try:
            text, words, detected = await asyncio.wait_for(fut, timeout=self.timeout_s)
        except TimeoutError as exc:
            raise TimeoutError("local stt timed out") from exc
        return Transcript(
            pts_start,
            pts_end,
            text,
            words,
            normalise_lang(detected, default=normalise_lang(lang_hint)),
            "local",
            self._clock() - t_start,
        )

    def _run(
        self, audio: np.ndarray, lang: str | None, pts_start: float, pts_end: float
    ) -> tuple[str, tuple[Word, ...], str | None]:
        try:
            model = self._load()
            try:
                segments, info = model.transcribe(
                    audio,
                    language=lang,
                    task="transcribe",
                    beam_size=self.beam_size,
                    temperature=0.0,
                    condition_on_previous_text=False,
                    word_timestamps=True,
                    vad_filter=self.vad_filter,
                    without_timestamps=False,
                )
                texts: list[str] = []
                words: list[Word] = []
                for seg in segments:  # a generator: decoding happens here, inside the worker thread
                    if (getattr(seg, "no_speech_prob", 0.0) or 0.0) > NO_SPEECH_DROP and (
                        getattr(seg, "avg_logprob", 0.0) or 0.0
                    ) < LOGPROB_DROP:
                        continue
                    seg_text = str(seg.text).strip()
                    if not seg_text:
                        continue
                    texts.append(seg_text)
                    seg_words = getattr(seg, "words", None)
                    if seg_words:
                        for w in seg_words:
                            token = str(w.word).strip()
                            if not token:
                                continue
                            t0 = min(max(pts_start + float(w.start), pts_start), pts_end)
                            t1 = min(max(pts_start + float(w.end), t0), pts_end)
                            words.append(Word(token, t0, t1, False, 0))
                    else:
                        lo = min(max(pts_start + float(seg.start), pts_start), pts_end)
                        hi = min(max(pts_start + float(seg.end), lo), pts_end)
                        words.extend(interpolate_words(seg_text, lo, hi))
            except (SttUnavailable, SttBusy):
                raise
            except Exception as exc:  # noqa: BLE001 - library errors carry no usable detail
                raise SttProviderError(f"local whisper failed ({type(exc).__name__})") from None
            return " ".join(texts), tuple(words), getattr(info, "language", None)
        finally:
            with self._lock:
                self._pending -= 1
