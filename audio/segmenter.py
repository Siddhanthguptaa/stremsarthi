"""Cut a 16 kHz audio stream into speech segments for speech-to-text.

Rules (docs/specs/audio.md 5.1):
* A segment opens after ``min_speech_ms`` of speech and starts ``speech_pad_ms`` earlier.
* It closes after ``min_silence_ms`` of silence and ends ``speech_pad_ms`` after the last speech.
* Hard cap ``segment_max_s``. When no pause comes, cut at the quietest 30 ms point in the last third
  of the cap (``[1.0, 1.5]`` s for the default 1.5 s). The next segment starts ``overlap_s`` before the cut
  (it carries the tail so a word split by the cut is heard whole) and is flagged ``is_continuation``.
* A segment is ``voiced`` when it holds at least ``voiced_min_ms`` of its own speech. For a continuation
  only speech after the cut counts, so a silent tail never forces a fail-safe mute.

Times are source PTS (from the chunks). ``arrival`` is the monotonic time the segment's first sample arrived,
taken from ``AudioChunk.arrival`` (assumed to stamp the chunk's first sample, accurate to one chunk, 20 ms).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np

from audio.vad import SAMPLE_RATE, WINDOW, FrameModel, StreamingVad, VadFrame
from contracts.config import Config, VadConfig
from contracts.media import AudioChunk

ENERGY_WINDOW = 480  # 30 ms
ENERGY_HOP = 160  # 10 ms


@dataclass(frozen=True, slots=True)
class Segment:
    seg_id: int
    pts_start: float
    pts_end: float
    pcm: Any  # int16 numpy array, 16 kHz mono
    arrival: float  # monotonic arrival of the first sample
    voiced: bool
    speech_ms: float  # own speech (continuations: after the cut)
    is_continuation: bool
    cut_reason: str  # "pause" | "cap" | "flush" | "gap"

    @property
    def duration(self) -> float:
        return self.pts_end - self.pts_start

    def __repr__(self) -> str:
        return (
            f"Segment(id={self.seg_id}, pts=({self.pts_start:.3f}, {self.pts_end:.3f}), voiced={self.voiced}, "
            f"cont={self.is_continuation}, cut={self.cut_reason})"
        )


class Segmenter:
    def __init__(
        self,
        vad: StreamingVad,
        *,
        segment_max_s: float = 1.5,
        overlap_s: float = 0.4,
        vad_cfg: VadConfig | None = None,
        speech_pad_ms: int = 60,
        voiced_min_ms: int = 200,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        if sample_rate != SAMPLE_RATE:
            raise ValueError("segmenter works on 16 kHz audio")
        cfg = vad_cfg or vad.cfg
        self.vad = vad
        self.sr = sample_rate
        self.cap = int(segment_max_s * sample_rate)
        self.cut_from = int(self.cap * 2 / 3)  # 1.0 s for a 1.5 s cap
        self.overlap = int(overlap_s * sample_rate)
        self.min_speech = int(cfg.min_speech_ms * sample_rate / 1000)
        self.min_silence = int(cfg.min_silence_ms * sample_rate / 1000)
        self.pad = int(speech_pad_ms * sample_rate / 1000)
        self.voiced_min = voiced_min_ms * sample_rate / 1000
        self._next_id = 1
        self._reset_stream()

    @classmethod
    def from_config(cls, config: Config, model: FrameModel | None = None) -> Segmenter:
        """Build a segmenter with Silero VAD (or ``model``) and the ``Config.audio`` settings."""
        a = config.audio
        vad = StreamingVad(model, a.vad) if model is not None else StreamingVad.silero(a.vad)
        return cls(vad, segment_max_s=a.segment_max_s, overlap_s=a.overlap_s, vad_cfg=a.vad)

    # ------------------------------------------------------------------ state
    def _reset_stream(self) -> None:
        self._origin: float | None = None  # pts of sample index 0 (re-anchored after a gap)
        self._buf = np.zeros(0, dtype=np.int16)
        self._buf_start = 0
        self._head = 0  # samples received so far (absolute index)
        self._arrivals: deque[tuple[int, float]] = deque()  # (first sample index of chunk, arrival)
        self._expected_pts: float | None = None
        self._run = 0  # consecutive speech samples while idle
        self._run_start = 0
        self._open = False
        self._seg_start = 0
        self._own_from = 0
        self._cont = False
        self._frames: list[tuple[int, bool]] = []  # (start, speech) of windows in the open segment
        self._sil = 0  # consecutive silent samples while open

    def reset(self) -> None:
        """Drop all state (segment ids keep counting)."""
        self.vad.reset()
        self._reset_stream()

    # ------------------------------------------------------------------ API
    def feed_chunk(self, chunk: AudioChunk) -> list[Segment]:
        return self.feed(chunk.pcm, chunk.pts_start, chunk.arrival)

    def feed(self, pcm: Any, pts_start: float, arrival: float) -> list[Segment]:
        data = np.asarray(pcm)
        if data.dtype != np.int16:
            raise TypeError("segmenter needs int16 PCM")
        out: list[Segment] = []
        if self._expected_pts is not None and abs(pts_start - self._expected_pts) > 0.1:
            out.extend(self._flush("gap"))  # a hole in the stream: never stitch across it
            self.vad.restart(self._head)
            self._buf = np.zeros(0, dtype=np.int16)
            self._buf_start = self._head
            self._arrivals.clear()
            self._origin = None
        if self._origin is None:
            self._origin = pts_start - self._head / self.sr
        self._expected_pts = pts_start + len(data) / self.sr
        self._arrivals.append((self._head, arrival))
        self._buf = np.concatenate([self._buf, data]) if self._buf.size else data.copy()
        self._head += len(data)
        for frame in self.vad.feed(data):
            self._on_frame(frame, out)
        self._trim()
        return out

    def flush(self) -> list[Segment]:
        """End of stream: run the partial window and close any open segment."""
        out: list[Segment] = []
        for frame in self.vad.flush():
            self._on_frame(frame, out)
        out.extend(self._flush("flush"))
        return out

    # ------------------------------------------------------------------ core
    def _on_frame(self, f: VadFrame, out: list[Segment]) -> None:
        start = f.start
        end = min(f.start + WINDOW, self._head)
        n = end - start
        if n <= 0:
            return
        if not self._open:
            if f.speech:
                if self._run == 0:
                    self._run_start = start
                self._run += n
                if self._run >= self.min_speech:
                    run_start = self._run_start
                    n_frames = -(-(end - run_start) // WINDOW)
                    self._open_segment(max(self._buf_start, run_start - self.pad), False, run_start)
                    self._frames = [(run_start + i * WINDOW, True) for i in range(n_frames)]
                    self._sil = 0
            else:
                self._run = 0
            return

        self._frames.append((start, f.speech))
        if f.speech:
            self._sil = 0
        else:
            self._sil += n
        if self._sil >= self.min_silence:
            self._close(self._last_speech_end(end) + self.pad, "pause", out)
        elif end - self._seg_start >= self.cap:
            self._cap_cut(end, out)

    def _open_segment(self, start: int, cont: bool, own_from: int) -> None:
        self._open = True
        self._seg_start = start
        self._own_from = own_from
        self._cont = cont
        self._run = 0

    def _last_speech_end(self, head: int) -> int:
        for s, speech in reversed(self._frames):
            if speech:
                return min(s + WINDOW, head)
        return self._own_from

    def _own_speech(self, end: int) -> int:
        total = 0
        for s, speech in self._frames:
            if speech:
                lo = max(s, self._own_from)
                hi = min(s + WINDOW, end)
                if hi > lo:
                    total += hi - lo
        return total

    def _cap_cut(self, frame_end: int, out: list[Segment]) -> None:
        lo = self._seg_start + self.cut_from
        hi = self._seg_start + self.cap
        cut = self._quietest_point(lo, hi, frame_end)
        self._emit(cut, "cap", out)
        new_start = max(self._buf_start, cut - self.overlap, self._seg_start + (cut - self._seg_start) // 2)
        self._open_segment(new_start, True, cut)
        self._frames = [(s, sp) for s, sp in self._frames if s + WINDOW > new_start]

    def _quietest_point(self, lo: int, hi: int, frame_end: int) -> int:
        """Centre of the quietest 30 ms window within [lo, hi]; latest on ties.

        Only audio up to the VAD frame that triggered the cut is used, so the result does not depend on how
        the stream was chunked.
        """
        a = max(self._buf_start, lo - ENERGY_WINDOW // 2)
        b = min(frame_end, self._head, hi + ENERGY_WINDOW // 2)
        seg = self._buf[a - self._buf_start : b - self._buf_start].astype(np.float64)
        if len(seg) < ENERGY_WINDOW:
            return min(hi, self._head)
        starts = np.arange(0, len(seg) - ENERGY_WINDOW + 1, ENERGY_HOP)
        csum = np.concatenate([[0.0], np.cumsum(seg * seg)])
        energy = (csum[starts + ENERGY_WINDOW] - csum[starts]) / ENERGY_WINDOW
        best = int(np.flatnonzero(energy <= energy.min() * 1.0001 + 1e-6)[-1])
        cut = a + int(starts[best]) + ENERGY_WINDOW // 2
        return min(max(cut, lo), hi)

    def _close(self, end: int, reason: str, out: list[Segment]) -> None:
        self._emit(min(end, self._head), reason, out)
        self._open = False
        self._frames = []
        self._sil = 0
        self._run = 0

    def _flush(self, reason: str) -> list[Segment]:
        out: list[Segment] = []
        if self._open:
            self._close(self._head, reason, out)
        self._run = 0
        return out

    def _emit(self, end: int, reason: str, out: list[Segment]) -> None:
        end = max(end, self._seg_start)
        own = self._own_speech(end)
        if self._cont and own == 0:
            return  # only the overlap tail of a previous segment: nothing new to transcribe
        assert self._origin is not None
        lo = max(self._seg_start, self._buf_start)
        pcm = self._buf[lo - self._buf_start : end - self._buf_start].copy()
        out.append(
            Segment(
                seg_id=self._next_id,
                pts_start=self._origin + lo / self.sr,
                pts_end=self._origin + end / self.sr,
                pcm=pcm,
                arrival=self._arrival_of(lo),
                voiced=own >= self.voiced_min,
                speech_ms=own * 1000.0 / self.sr,
                is_continuation=self._cont,
                cut_reason=reason,
            )
        )
        self._next_id += 1

    def _arrival_of(self, idx: int) -> float:
        rec_idx, rec_arrival = self._arrivals[0]
        for i, a in self._arrivals:
            if i <= idx:
                rec_idx, rec_arrival = i, a
            else:
                break
        return rec_arrival + (idx - rec_idx) / self.sr

    def _trim(self) -> None:
        if self._open:
            keep = self._seg_start
        else:
            keep = (self._run_start if self._run else self._head) - self.pad - 2 * WINDOW
            keep = min(keep, self._head - self.pad - 2 * WINDOW)
        keep = max(self._buf_start, keep)
        if keep > self._buf_start:
            self._buf = self._buf[keep - self._buf_start :]
            self._buf_start = keep
        while len(self._arrivals) > 1 and self._arrivals[1][0] <= self._buf_start:
            self._arrivals.popleft()
