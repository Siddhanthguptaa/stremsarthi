"""VAD and segmenter tests on synthetic tone/silence signals and one TTS clip (benign text, no personal data).

The TTS clip ``data/tts_two_sentences.wav`` was made with ffmpeg's flite filter:
  lead 0.5 s silence, "Hello, this is a test." (0.5 to 2.32 s), 0.8 s silence,
  "Please wait a moment." (3.12 to 4.61 s), 0.7 s silence. 16 kHz mono int16.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from audio.models import silero_vad_path
from audio.segmenter import Segment, Segmenter
from audio.vad import EnergyFrameModel, Hysteresis, StreamingVad
from contracts.config import VadConfig

SR = 16_000
DATA = Path(__file__).parent / "data"


def silence(s: float) -> np.ndarray:
    return np.zeros(int(s * SR), dtype=np.int16)


def tone(s: float, amp: float = 0.3, f: float = 220.0) -> np.ndarray:
    t = np.arange(int(s * SR)) / SR
    return (amp * 32767 * np.sin(2 * np.pi * f * t)).astype(np.int16)


def make_segmenter(**kw) -> Segmenter:
    return Segmenter(StreamingVad(EnergyFrameModel(), VadConfig()), **kw)


def run(
    seg: Segmenter, x: np.ndarray, chunk: int = 320, t0: float = 0.0, arrival0: float = 1000.0
) -> list[Segment]:
    out: list[Segment] = []
    for i in range(0, len(x), chunk):
        out += seg.feed(x[i : i + chunk], t0 + i / SR, arrival0 + i / SR)
    out += seg.flush()
    return out


def test_hysteresis_on_off_thresholds():
    h = Hysteresis(0.5, 0.35)
    assert [h.step(p) for p in (0.4, 0.6, 0.45, 0.36, 0.34, 0.45, 0.5)] == [
        False, True, True, True, False, False, True,
    ]  # fmt: skip
    with pytest.raises(ValueError):
        Hysteresis(0.3, 0.5)


def test_streaming_vad_framing_is_chunk_invariant():
    x = np.concatenate([silence(0.3), tone(0.5), silence(0.3)])
    a = StreamingVad(EnergyFrameModel()).feed(x)
    vad = StreamingVad(EnergyFrameModel())
    b = []
    for i in range(0, len(x), 77):
        b += vad.feed(x[i : i + 77])
    assert [(f.start, f.speech) for f in a] == [(f.start, f.speech) for f in b]
    assert all(f.start % 512 == 0 for f in a)


def test_silence_yields_no_segments():
    assert run(make_segmenter(), silence(5.0)) == []


def test_two_bursts_give_two_padded_voiced_segments():
    x = np.concatenate([silence(0.5), tone(1.0), silence(0.4), tone(0.8), silence(0.5)])
    segs = run(make_segmenter(), x)
    assert len(segs) == 2
    assert all(s.voiced and not s.is_continuation and s.cut_reason == "pause" for s in segs)
    # onset 0.5 s and offset 1.5 s, padded by 60 ms; windows are 32 ms wide
    assert segs[0].pts_start == pytest.approx(0.5 - 0.06, abs=0.04)
    assert segs[0].pts_end == pytest.approx(1.5 + 0.06, abs=0.04)
    assert segs[1].pts_start == pytest.approx(1.9 - 0.06, abs=0.04)
    assert segs[1].pts_end == pytest.approx(2.7 + 0.06, abs=0.04)
    assert [s.seg_id for s in segs] == [1, 2]
    assert len(segs[0].pcm) == pytest.approx(segs[0].duration * SR, abs=2)


def test_short_bursts_voiced_rule():
    # windows are 32 ms, so 60 ms of speech touches at most 3 windows (96 ms < min_speech 120 ms): no segment;
    # 140 ms opens a segment that is not voiced (< 200 ms of speech)
    assert run(make_segmenter(), np.concatenate([silence(0.5), tone(0.06), silence(1.0)])) == []
    segs = run(make_segmenter(), np.concatenate([silence(0.5), tone(0.14), silence(1.0)]))
    assert len(segs) == 1 and not segs[0].voiced and segs[0].speech_ms < 200
    segs = run(make_segmenter(), np.concatenate([silence(0.5), tone(0.3), silence(1.0)]))
    assert len(segs) == 1 and segs[0].voiced


def test_five_second_utterance_is_capped_with_overlap_and_cut_at_energy_minimum():
    x = np.concatenate([silence(0.5), tone(5.0), silence(1.0)])
    # the first segment starts at about 0.44 s, so its cut window is [1.44, 1.94] s; a 40 ms near-silent dip
    # at 1.65 s is shorter than min_silence (no pause) but is the energy minimum the cut must pick
    dip_at = int(1.65 * SR)
    x[dip_at : dip_at + int(0.04 * SR)] //= 200
    segs = run(make_segmenter(), x)
    assert len(segs) >= 4
    assert all(s.duration <= 1.5 + 1e-6 for s in segs)
    assert not segs[0].is_continuation and all(s.is_continuation for s in segs[1:])
    cap_segs = [s for s in segs if s.cut_reason == "cap"]
    assert len(cap_segs) >= 3
    # first cut is at the dip (centre of the 40 ms dip is 1.67 s)
    assert segs[0].pts_end == pytest.approx(1.67, abs=0.03)
    for prev, nxt in zip(segs, segs[1:], strict=False):
        assert nxt.pts_start == pytest.approx(prev.pts_end - 0.4, abs=0.002)  # 0.4 s overlap
    # the segments cover the whole utterance without holes
    assert segs[0].pts_start < 0.5 and segs[-1].pts_end > 5.5
    assert all(s.voiced for s in segs)


def test_chunking_does_not_change_segment_bounds():
    x = np.concatenate([silence(0.3), tone(2.4), silence(0.5), tone(0.6), silence(0.4)])
    ref = [(s.pts_start, s.pts_end, s.cut_reason) for s in run(make_segmenter(), x, chunk=320)]
    for chunk in (160, 1600, 4800):
        got = [(s.pts_start, s.pts_end, s.cut_reason) for s in run(make_segmenter(), x, chunk=chunk)]
        assert got == ref


def test_arrival_is_that_of_the_first_sample_and_pts_follow_chunks():
    x = np.concatenate([silence(0.5), tone(0.6), silence(0.5)])
    segs = run(make_segmenter(), x, t0=50.0, arrival0=1000.0)
    s = segs[0]
    assert s.pts_start == pytest.approx(50.5 - 0.06, abs=0.04)
    assert s.arrival == pytest.approx(1000.0 + (s.pts_start - 50.0), abs=1e-6)


def test_gap_in_the_stream_closes_the_open_segment():
    seg = make_segmenter()
    out = seg.feed(np.concatenate([silence(0.2), tone(0.5)]), 0.0, 10.0)
    assert out == []
    out = seg.feed(np.concatenate([tone(0.5), silence(0.5)]), 30.0, 40.0)  # 29 s hole
    # the pre-gap segment is closed by the gap; the post-gap burst is its own segment on the new clock
    assert [s.cut_reason for s in out] == ["gap", "pause"]
    assert out[0].pts_end == pytest.approx(0.7, abs=0.001)
    assert out[1].pts_start == pytest.approx(30.0, abs=0.001)  # no audio before the gap to pad with
    assert out[1].pts_end == pytest.approx(30.5 + 0.06, abs=0.04)
    assert seg.flush() == []


def test_flush_closes_open_segment():
    seg = make_segmenter()
    assert seg.feed(np.concatenate([silence(0.2), tone(0.5)]), 0.0, 0.0) == []
    out = seg.flush()
    assert len(out) == 1 and out[0].cut_reason == "flush" and out[0].voiced


def test_continuation_with_only_silent_tail_is_not_emitted():
    # speech 0.3 s .. 1.7 s: cap cut near 1.4, then speech ends right after; the tail is covered by the overlap
    x = np.concatenate([silence(0.3), tone(1.1), silence(1.0)])
    segs = run(make_segmenter(), x)
    assert all(s.voiced for s in segs)
    assert not any(s.is_continuation and s.speech_ms == 0 for s in segs)


def test_int16_required():
    with pytest.raises(TypeError):
        make_segmenter().feed(np.zeros(320, dtype=np.float32), 0.0, 0.0)


# ---------------------------------------------------------------- Silero ONNX (real model, CPU)
needs_model = pytest.mark.skipif(not silero_vad_path().exists(), reason="Silero model not downloaded")


@needs_model
def test_silero_silence_is_not_speech_and_state_is_streamed():
    vad = StreamingVad.silero()
    frames = vad.feed(silence(1.0))
    assert len(frames) == SR // 512
    assert max(f.prob for f in frames) < 0.2 and not any(f.speech for f in frames)


@needs_model
def test_silero_on_tts_clip_finds_two_sentences():
    import soundfile as sf

    x, sr = sf.read(DATA / "tts_two_sentences.wav", dtype="int16")
    assert sr == SR
    seg = Segmenter(StreamingVad.silero())
    segs = run(seg, x)
    # sentence 1 (1.6 s of speech) exceeds the 1.5 s cap: a capped segment, then a short continuation that
    # carries the tail (its own speech is under 200 ms, so it is not voiced); sentence 2 is one segment
    assert [s.cut_reason for s in segs] == ["cap", "pause", "pause"], segs
    s1, s1b, s2 = segs
    assert s1.voiced and not s1.is_continuation and s2.voiced and not s2.is_continuation
    assert s1b.is_continuation and not s1b.voiced
    assert s1b.pts_start == pytest.approx(s1.pts_end - 0.4, abs=0.002)
    assert 0.35 <= s1.pts_start <= 0.9 and 2.2 <= s1b.pts_end <= 2.9
    assert 2.9 <= s2.pts_start <= 3.5 and 4.4 <= s2.pts_end <= 5.0
