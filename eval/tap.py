"""Tap reader: connects to MediaMTX's ``shielded`` path (RTMP or SRT, never RTSP: false A/V offset, see
``eval/README.md``), decodes the optical stamp of every output frame and scores the SPECIMEN card.

Per decoded frame:
* stamp readable -> a ``live`` ``TapFrame`` with capture-to-tap delay; the scene script (the same one the
  sender plays, so ground truth is by construction) says whether a card is in view and where, and the
  structure test of ``eval.cardcheck`` scores the frame (about 2 ms). A sampled subset also goes to an OCR
  worker (``ocr_every_s``, default 1 s) which looks for a Verhoeff-valid 12-digit read.
* stamp unreadable -> a slate (PROTECT, HOLD, transport) when the picture is one flat colour, otherwise a
  genuine ``unreadable`` frame (counted against the stamp_unreadable <= 1 % criterion).

Only numbers are kept: no frame is stored or written. The tap adds one decode per frame (reader <= 0.5 core).
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .cardcheck import CardChecker, OcrReader, is_slate
from .delay import TapFrame
from .harness import metrics
from .sender.reader import ReaderResult, read_stream
from .sender.scenes import ScenePlayer, Target
from .sender.stamp import MS_MODULUS

CARD_CATEGORY = "aadhaar"
MAX_PLAUSIBLE_DELAY_S = 60.0  # a stamp older than this is a decode error, not a delay


@dataclass
class TapResult:
    frames: list[TapFrame]
    reader: ReaderResult
    audio: list[tuple[float, float]]  # (monotonic read time, RMS dBFS) per audio frame
    ocr_submitted: int = 0
    ocr_skipped_busy: int = 0
    ocr_done: int = 0
    errors: list[str] = field(default_factory=list)
    implausible: int = 0  # CRC-valid stamps with an impossible age (counted as unreadable)


class Tap:
    def __init__(
        self,
        url: str,
        script: str,
        fps: int = 30,
        ocr: bool = True,
        ocr_every_s: float = 1.0,
        ocr_threads: int = 2,
        checker: CardChecker | None = None,
    ) -> None:
        self.url = url
        self.player = ScenePlayer(script, fps)
        self.fps = fps
        self.checker = checker or CardChecker(OcrReader(ocr_threads) if ocr else None)
        self.ocr_every_s = ocr_every_s
        self.frames: list[TapFrame] = []
        self.audio: list[tuple[float, float]] = []
        self._next_ocr = 0.0
        self._q: queue.Queue[tuple[TapFrame, np.ndarray, Target] | None] = queue.Queue(maxsize=2)
        self._worker: threading.Thread | None = None
        self.ocr_submitted = 0
        self.ocr_skipped_busy = 0
        self.ocr_done = 0
        self.errors: list[str] = []
        self.implausible = 0

    # ------------------------------------------------------------ hooks

    def _video_hook(self, frame: Any, stamp: Any, t_read: float) -> str | None:
        if stamp is None:
            bgr = frame.to_ndarray(format="bgr24")
            slate = is_slate(bgr)
            self.frames.append(TapFrame(None, t_read, "slate" if slate else "unreadable"))
            return "slate" if slate else None
        counter = stamp.counter
        delay = ((t_read * 1000.0) - stamp.capture_ms) % MS_MODULUS / 1000.0
        if delay > MAX_PLAUSIBLE_DELAY_S:
            # a CRC-valid but wrong stamp (a mangled corner can decode to counter 0, time 0: a "delay" of the
            # whole uptime): never let it into the percentiles, count it against the stamp criterion instead
            self.implausible += 1
            self.frames.append(TapFrame(None, t_read, "unreadable"))
            return None
        pts = float(frame.pts * frame.time_base) if frame.pts is not None else None
        scene, targets = self.player.targets(counter)
        tf = TapFrame(counter, t_read, "live", delay, pts, scene)
        cards = [t for t in targets if t.category == CARD_CATEGORY and t.ref]
        if cards:
            tf.card_visible = True
            try:
                bgr = frame.to_ndarray(format="bgr24")
                if is_slate(bgr):  # a slate that kept its stamp: no card pixels can be in it
                    tf.kind = "slate"
                    tf.leaked = False
                else:
                    self._score(tf, bgr, cards[0], t_read)
            except Exception as exc:  # a scoring bug must not stop the tap
                self.errors.append(f"score:{type(exc).__name__}")
        self.frames.append(tf)
        return None

    def _score(self, tf: TapFrame, bgr: np.ndarray, target: Target, t_read: float) -> None:
        whole, band, crop = self.checker.similarity(bgr, target)
        tf.similarity = round(max(whole, band), 4)
        tf.leaked = tf.similarity >= self.checker.sim_threshold
        suspicious = tf.similarity >= 0.5 * self.checker.sim_threshold
        if self.checker.ocr is not None and (t_read >= self._next_ocr or (suspicious and not self._q.full())):
            try:
                self._q.put_nowait((tf, crop.copy(), target))
                self.ocr_submitted += 1
                self._next_ocr = t_read + self.ocr_every_s
            except queue.Full:
                self.ocr_skipped_busy += 1

    def _audio_hook(self, mono: np.ndarray, _pts: float, t_read: float) -> None:
        if mono.size:
            rms = float(np.sqrt(np.mean(np.square(mono.astype(np.float64)))))
            self.audio.append((t_read, 20.0 * np.log10(max(rms, 1e-6))))

    # ------------------------------------------------------------ ocr worker

    def _ocr_loop(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            tf, crop, target = item
            try:
                valid, exact = self.checker.ocr_only(crop, target)
            except Exception as exc:
                self.errors.append(f"ocr:{type(exc).__name__}")
                continue
            tf.ocr = {"valid_read": valid, "exact_read": exact}
            if valid:
                tf.leaked = True
            self.ocr_done += 1

    # ------------------------------------------------------------ run

    def run(
        self, seconds: float, stop: threading.Event | None = None, open_timeout_s: float = 30.0
    ) -> TapResult:
        if self.checker.ocr is not None:
            self._worker = threading.Thread(target=self._ocr_loop, daemon=True, name="tap-ocr")
            self._worker.start()
        try:
            reader = read_stream(
                self.url,
                seconds,
                stop,
                open_timeout_s,
                video_hook=self._video_hook,
                audio_hook=self._audio_hook,
            )
        finally:
            if self._worker is not None:
                self._q.put(None)
                self._worker.join(timeout=30.0)
        if reader.error:
            self.errors.append(reader.error)
        return TapResult(
            self.frames,
            reader,
            self.audio,
            self.ocr_submitted,
            self.ocr_skipped_busy,
            self.ocr_done,
            self.errors,
            self.implausible,
        )


# ---------------------------------------------------------------- card statistics


def card_stats(frames: list[TapFrame], warmup_s: float = 1.0) -> dict[str, Any]:
    """Card visibility after a warm-up (measured from the first live frame).

    ``leaked_frames`` counts live card-visible frames where the card is recoverable (structure test or a
    Verhoeff-valid OCR read). ``unreadable_recall`` is 1 - leaked / visible with a Wilson interval.
    """
    live = [f for f in frames if f.kind in ("live", "slate") and f.counter is not None]
    if not live:
        return {"visible_frames": 0, "leaked_frames": 0, "unreadable_recall": None}
    first = min(f.t_read for f in live)
    scored = [f for f in live if f.card_visible and f.leaked is not None]
    after = [f for f in scored if f.t_read - first >= warmup_s]
    leaked = [f for f in after if f.leaked]
    ocr = [f for f in after if f.ocr is not None]
    ocr_valid = [f for f in ocr if f.ocr and f.ocr["valid_read"]]
    ocr_exact = [f for f in ocr if f.ocr and f.ocr["exact_read"]]
    r = metrics.ratio(len(after) - len(leaked), len(after))
    return {
        "warmup_s": warmup_s,
        "visible_frames": len(after),
        "leaked_frames": len(leaked),
        "leaked_in_warmup": sum(1 for f in scored if f.t_read - first < warmup_s and f.leaked),
        "unreadable_recall": r.value,
        "recall_ci": [round(r.ci_lo, 4), round(r.ci_hi, 4)],
        "ocr_checked": len(ocr),
        "ocr_valid_reads": len(ocr_valid),
        "ocr_exact_reads": len(ocr_exact),
        "max_similarity": max((f.similarity or 0.0 for f in after), default=None),
        "leaked_counters": [f.counter for f in leaked][:50],
    }


def stamp_stats(result: TapResult) -> dict[str, Any]:
    r = result.reader
    n = r.video_decoded
    bad = r.stamp_unreadable + getattr(result, "implausible", 0)
    return {
        "decoded": n,
        "stamp_unreadable": bad,
        "implausible_stamps": getattr(result, "implausible", 0),
        "slate_frames": r.slate_frames,
        "stamp_unreadable_frac": (bad / n) if n else None,
    }


def now() -> float:
    return time.monotonic()
