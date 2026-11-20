"""STT adapters against mocked HTTP (httpx.MockTransport), a fake faster-whisper model and FakeSTT.

No network, no API keys, no model files. Live checks are in ``test_audio_stt_live.py`` (skipped without keys).
"""

from __future__ import annotations

import asyncio
import io
import threading
import time
import wave
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from audio.segmenter import Segmenter
from audio.stt.base import (
    SttBusy,
    SttProviderError,
    SttRateLimited,
    SttUnavailable,
    TokenBucket,
    interpolate_words,
    normalise_lang,
    pcm_to_wav,
    with_seg_id,
)
from audio.stt.groq import GroqChunker, GroqSTT
from audio.stt.local import LocalWhisperSTT, preprocess
from audio.stt.sarvam import SarvamSTT, sarvam_language_code
from audio.vad import EnergyFrameModel, StreamingVad
from contracts import SpeechToText, Transcript, Word
from contracts.fakes import FakeSTT

SR = 16_000


def tone(s: float, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(s * SR)) / SR
    return (amp * 32767 * np.sin(2 * np.pi * 220 * t)).astype(np.int16)


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- base helpers
def test_token_bucket_refills_with_the_clock():
    now = [0.0]
    b = TokenBucket(rate_per_min=60, burst=2, clock=lambda: now[0])
    assert b.try_acquire() and b.try_acquire() and not b.try_acquire()
    assert b.retry_after_s() == pytest.approx(1.0)
    now[0] += 1.0
    assert b.try_acquire() and not b.try_acquire()
    now[0] += 100.0
    assert b.try_acquire() and b.try_acquire() and not b.try_acquire()  # capped at burst


def test_interpolated_words_are_approx_contiguous_and_cover_the_span():
    ws = interpolate_words("ek do teen char", 10.0, 12.0, seg_id=7)
    assert [w.approx for w in ws] == [True] * 4 and {w.seg_id for w in ws} == {7}
    assert ws[0].t0 == 10.0 and ws[-1].t1 == 12.0
    assert all(a.t1 == pytest.approx(b.t0) for a, b in zip(ws, ws[1:], strict=False))
    assert interpolate_words("   ", 0, 1) == ()


def test_language_helpers():
    assert (
        normalise_lang("hi-IN") == "hi"
        and normalise_lang("Hindi") == "hi"
        and normalise_lang(None) == "unknown"
    )
    assert sarvam_language_code(None) == "unknown" and sarvam_language_code("hinglish") == "unknown"
    assert sarvam_language_code("hi") == "hi-IN" and sarvam_language_code("kn-IN") == "kn-IN"
    assert sarvam_language_code("xx") == "unknown"


def test_wav_container_roundtrip():
    pcm = tone(0.1)
    with wave.open(io.BytesIO(pcm_to_wav(pcm)), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, SR, len(pcm))
        assert w.readframes(w.getnframes()) == pcm.tobytes()


def test_with_seg_id_rewrites_words_only():
    tr = Transcript(0.0, 1.0, "a b", interpolate_words("a b", 0, 1), "hi", "local", 0.1)
    out = with_seg_id(tr, 9)
    assert {w.seg_id for w in out.words} == {9} and out.text == tr.text and out.provider == "local"


# ---------------------------------------------------------------- Sarvam
def sarvam_ok(request: httpx.Request) -> httpx.Response:
    body = request.content
    assert request.headers["api-subscription-key"] == "test-key"
    for needle in (
        b'name="model"',
        b"saaras:v4",
        b'name="mode"',
        b"codemix",
        b'name="language_code"',
        b"RIFF",
    ):
        assert needle in body, needle
    return httpx.Response(200, json={"transcript": "mera number hai", "language_code": "hi-IN"})


async def test_sarvam_success_maps_to_transcript_with_interpolated_words():
    stt = SarvamSTT("test-key", client=client_for(sarvam_ok))
    assert isinstance(stt, SpeechToText) and stt.name == "sarvam"
    tr = await stt.transcribe(tone(1.5), 12.0, "hi")
    assert tr.provider == "sarvam" and tr.lang == "hi" and tr.text == "mera number hai"
    assert (tr.pts_start, tr.pts_end) == (12.0, 13.5)
    assert [w.text for w in tr.words] == ["mera", "number", "hai"]
    assert all(w.approx for w in tr.words) and not tr.has_word_times
    assert tr.words[0].t0 == 12.0 and tr.words[-1].t1 == pytest.approx(13.5)
    assert 0.0 <= tr.latency_s < 1.0


async def test_sarvam_chunk_timestamps_bound_the_interpolation():
    def handler(request: httpx.Request) -> httpx.Response:
        assert b"true" in request.content  # with_timestamps
        return httpx.Response(
            200,
            json={
                "transcript": "ek do teen char",
                "language_code": "hi-IN",
                "timestamps": {
                    "words": ["ek do", "teen char"],
                    "start_time_seconds": [0.1, 0.8],
                    "end_time_seconds": [0.6, 1.4],
                },
            },
        )

    stt = SarvamSTT("k", with_timestamps=True, client=client_for(handler))
    tr = await stt.transcribe(tone(1.5), 100.0, None)
    assert [w.text for w in tr.words] == ["ek", "do", "teen", "char"]
    assert tr.words[0].t0 == pytest.approx(100.1) and tr.words[1].t1 == pytest.approx(100.6)
    assert tr.words[2].t0 == pytest.approx(100.8) and tr.words[3].t1 == pytest.approx(101.4)
    assert all(w.approx for w in tr.words)


async def test_sarvam_empty_transcript_is_a_valid_result():
    stt = SarvamSTT("k", client=client_for(lambda r: httpx.Response(200, json={"transcript": ""})))
    tr = await stt.transcribe(tone(1.0), 0.0, None)
    assert tr.text == "" and tr.words == ()


async def test_sarvam_without_key_fails_fast_and_makes_no_request():
    calls = []
    stt = SarvamSTT("", client=client_for(lambda r: calls.append(r) or httpx.Response(200)))
    with pytest.raises(SttUnavailable):
        await stt.transcribe(tone(1.0), 0.0, None)
    assert calls == []


@pytest.mark.parametrize(
    ("status", "exc"),
    [(429, SttRateLimited), (500, SttProviderError), (503, SttProviderError), (403, SttProviderError)],
)
async def test_sarvam_error_statuses(status, exc):
    handler = lambda r: httpx.Response(status, headers={"retry-after": "2"}, text="secret transcript text")  # noqa: E731
    stt = SarvamSTT("k", client=client_for(handler))
    with pytest.raises(exc) as info:
        await stt.transcribe(tone(1.0), 0.0, None)
    assert "secret" not in str(info.value)  # bodies are never echoed
    if status == 429:
        assert info.value.retry_after_s == 2.0
    else:
        assert info.value.status == status


async def test_sarvam_bad_json_is_a_provider_error():
    stt = SarvamSTT("k", client=client_for(lambda r: httpx.Response(200, text="<html>")))
    with pytest.raises(SttProviderError):
        await stt.transcribe(tone(1.0), 0.0, None)


async def test_sarvam_timeout_raises_timeouterror():
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.5)
        return httpx.Response(200, json={"transcript": "x"})

    stt = SarvamSTT("k", timeout_s=0.05, client=client_for(slow))
    t = time.perf_counter()
    with pytest.raises(TimeoutError):
        await stt.transcribe(tone(1.0), 0.0, None)
    assert time.perf_counter() - t < 0.4


async def test_sarvam_token_bucket_sheds_load_before_the_network():
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(200, json={"transcript": "a"})

    stt = SarvamSTT("k", rpm=1, burst=2, client=client_for(handler))
    await stt.transcribe(tone(1.0), 0.0, None)
    await stt.transcribe(tone(1.0), 1.0, None)
    with pytest.raises(SttRateLimited):
        await stt.transcribe(tone(1.0), 2.0, None)
    assert len(calls) == 2


async def test_sarvam_rejects_audio_over_the_rest_limit():
    stt = SarvamSTT("k", client=client_for(sarvam_ok))
    with pytest.raises(ValueError):
        await stt.transcribe(np.zeros(31 * SR, dtype=np.int16), 0.0, None)


# ---------------------------------------------------------------- Groq
def groq_ok(request: httpx.Request) -> httpx.Response:
    body = request.content
    assert request.headers["authorization"] == "Bearer gk"
    for needle in (b"whisper-large-v3-turbo", b"verbose_json", b"timestamp_granularities[]", b"word", b"hi"):
        assert needle in body, needle
    return httpx.Response(
        200,
        json={
            "text": " nau aath saat",
            "language": "hindi",
            "words": [
                {"word": "nau", "start": 0.2, "end": 0.5},
                {"word": "aath", "start": 0.6, "end": 0.9},
                {"word": "saat", "start": 1.0, "end": 9.0},  # beyond the chunk: clamped
            ],
        },
    )


async def test_groq_word_times_are_absolute_and_clamped():
    stt = GroqSTT("gk", client=client_for(groq_ok))
    assert isinstance(stt, SpeechToText) and stt.name == "groq"
    tr = await stt.transcribe(tone(3.0), 50.0, "hi")
    assert tr.provider == "groq" and tr.lang == "hi" and tr.has_word_times
    assert [(w.text, round(w.t0, 3), round(w.t1, 3)) for w in tr.words] == [
        ("nau", 50.2, 50.5),
        ("aath", 50.6, 50.9),
        ("saat", 51.0, 53.0),
    ]


async def test_groq_without_words_falls_back_to_interpolation():
    stt = GroqSTT(
        "gk", client=client_for(lambda r: httpx.Response(200, json={"text": "ek do", "language": "en"}))
    )
    tr = await stt.transcribe(tone(3.0), 0.0, None)
    assert [w.approx for w in tr.words] == [True, True] and tr.lang == "en"


async def test_groq_requires_three_second_chunks_and_a_key():
    stt = GroqSTT("gk", client=client_for(groq_ok))
    with pytest.raises(ValueError):
        await stt.transcribe(tone(1.5), 0.0, "hi")
    with pytest.raises(SttUnavailable):
        await GroqSTT("", client=client_for(groq_ok)).transcribe(tone(3.0), 0.0, "hi")


async def test_groq_stays_within_18_requests_a_minute():
    now = [0.0]
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(200, json={"text": "a", "words": []})

    stt = GroqSTT("gk", rpm=18, burst=2, client=client_for(handler), clock=lambda: now[0])
    ok = 0
    for i in range(60):  # one request attempt per second for a minute
        now[0] = float(i)
        try:
            await stt.transcribe(tone(3.0), float(i), None)
            ok += 1
        except SttRateLimited:
            pass
    assert ok == len(calls) <= 18 + 2


async def test_groq_429_and_5xx():
    with pytest.raises(SttRateLimited):
        await GroqSTT("gk", client=client_for(lambda r: httpx.Response(429))).transcribe(tone(3.0), 0.0, None)
    with pytest.raises(SttProviderError):
        await GroqSTT("gk", client=client_for(lambda r: httpx.Response(502))).transcribe(tone(3.0), 0.0, None)


def test_groq_chunker_merges_on_the_source_clock():
    ch = GroqChunker(min_s=3.0)
    seg = lambda s: np.full(int(s * SR), 1, dtype=np.int16)  # noqa: E731
    assert ch.add(seg(1.5), 10.0) is None
    # continuation: starts 0.4 s before the previous end, so 0.4 s of it is dropped
    assert ch.add(seg(1.5), 11.1) is None
    assert ch.held_s == pytest.approx(2.6)
    # after a 0.3 s pause the gap is zero filled to keep timestamps aligned
    out = ch.add(seg(1.0), 12.9)
    assert out is not None
    pcm, start = out
    assert start == 10.0 and len(pcm) / SR == pytest.approx(3.9)
    assert pcm[int(2.7 * SR) : int(2.9 * SR)].max() == 0  # the 0.3 s gap (12.6 to 12.9)... sits at 2.6-2.9 s
    assert ch.held_s == 0.0


def test_groq_chunker_pause_flushes_held_audio_and_flush_returns_the_rest():
    ch = GroqChunker(min_s=3.0, max_gap_s=1.0)
    seg = np.ones(SR, dtype=np.int16)
    assert ch.add(seg, 0.0) is None
    ready = ch.add(seg, 5.0)  # 4 s pause: do not wait for more audio
    assert ready is not None and len(ready[0]) == SR and ready[1] == 0.0
    tail = ch.flush()
    assert tail is not None and tail[1] == 5.0 and ch.flush() is None


# ---------------------------------------------------------------- local faster-whisper (fake model)
def fake_model(segments, language="hi", delay: float = 0.0, gate: threading.Event | None = None, seen=None):
    class Model:
        def transcribe(self, audio, **kw):
            if seen is not None:
                seen.append((audio, kw))
            if gate is not None:
                gate.wait(2.0)
            if delay:
                time.sleep(delay)
            return iter(segments), SimpleNamespace(language=language)

    return Model()


def seg_obj(text, start, end, words=(), no_speech=0.0, logprob=-0.2):
    ws = [SimpleNamespace(word=w, start=a, end=b) for w, a, b in words]
    return SimpleNamespace(
        text=text, start=start, end=end, words=ws or None, no_speech_prob=no_speech, avg_logprob=logprob
    )


async def test_local_word_times_are_shifted_to_absolute_pts():
    segs = [seg_obj(" nau aath", 0.1, 0.9, [(" nau", 0.1, 0.4), (" aath", 0.5, 0.9)])]
    seen: list = []
    stt = LocalWhisperSTT(model_factory=lambda: fake_model(segs, seen=seen))
    assert isinstance(stt, SpeechToText) and stt.name == "local"
    tr = await stt.transcribe(tone(1.0), 200.0, "hi")
    assert tr.provider == "local" and tr.lang == "hi" and tr.has_word_times
    assert [(w.text, round(w.t0, 3), round(w.t1, 3)) for w in tr.words] == [
        ("nau", 200.1, 200.4),
        ("aath", 200.5, 200.9),
    ]
    audio, kw = seen[0]
    assert audio.dtype == np.float32 and kw["word_timestamps"] is True and kw["language"] == "hi"
    assert kw["condition_on_previous_text"] is False


async def test_local_segment_without_words_is_interpolated_and_hallucinations_dropped():
    segs = [
        seg_obj("ek do", 0.0, 0.8),
        seg_obj("thank you for watching", 0.8, 1.0, no_speech=0.95, logprob=-1.5),
    ]
    stt = LocalWhisperSTT(model_factory=lambda: fake_model(segs))
    tr = await stt.transcribe(tone(1.0), 5.0, None)
    assert tr.text == "ek do" and all(w.approx for w in tr.words) and tr.words[-1].t1 == pytest.approx(5.8)


async def test_local_skips_near_silence_without_calling_the_model():
    seen: list = []
    stt = LocalWhisperSTT(model_factory=lambda: fake_model([seg_obj("x", 0, 1)], seen=seen))
    quiet = (np.random.default_rng(1).normal(0, 3, SR)).astype(np.int16)  # about -80 dBFS
    tr = await stt.transcribe(quiet, 0.0, "hi")
    assert tr.text == "" and tr.words == () and seen == []


async def test_local_timeout_and_load_shedding():
    gate = threading.Event()
    stt = LocalWhisperSTT(
        model_factory=lambda: fake_model([seg_obj("a", 0, 1)], gate=gate), timeout_s=0.05, max_pending=1
    )
    with pytest.raises(TimeoutError):
        await stt.transcribe(tone(1.0), 0.0, None)  # worker is now stuck behind the gate
    with pytest.raises(SttBusy):
        await stt.transcribe(tone(1.0), 1.0, None)
    gate.set()
    await asyncio.sleep(0.1)  # worker drains; capacity returns
    stt.timeout_s = 1.0
    assert (await stt.transcribe(tone(1.0), 2.0, None)).provider == "local"
    await stt.aclose()


async def test_local_model_errors_do_not_leak_details():
    class Boom:
        def transcribe(self, *a, **k):
            raise RuntimeError("contains transcript text 98765")

    stt = LocalWhisperSTT(model_factory=lambda: Boom())
    with pytest.raises(SttProviderError) as info:
        await stt.transcribe(tone(1.0), 0.0, None)
    assert "98765" not in str(info.value)


async def test_local_unavailable_when_model_files_are_missing(tmp_path):
    stt = LocalWhisperSTT("base", model_dir=tmp_path / "nope")
    with pytest.raises(SttUnavailable):
        await stt.transcribe(tone(1.0), 0.0, None)


def test_local_preprocess_removes_dc_and_lifts_quiet_audio():
    x = (tone(0.5, amp=0.05).astype(np.int32) + 3000).clip(-32768, 32767).astype(np.int16)
    y = preprocess(x)
    assert abs(float(y.mean())) < 1e-3 and float(np.abs(y).max()) == pytest.approx(0.9, abs=0.01)


# ---------------------------------------------------------------- wiring with FakeSTT and privacy
async def test_segmenter_into_fakestt_keeps_pts_and_seg_ids():
    x = np.concatenate([np.zeros(int(0.5 * SR), np.int16), tone(1.0), np.zeros(SR, np.int16)])
    seg = Segmenter(StreamingVad(EnergyFrameModel()))
    segs = []
    for i in range(0, len(x), 320):
        segs += seg.feed(x[i : i + 320], i / SR, 100.0 + i / SR)
    segs += seg.flush()
    assert len(segs) == 1
    stt = FakeSTT(texts=["nau aath"], segment_s=segs[0].duration)
    tr = with_seg_id(await stt.transcribe(segs[0].pcm, segs[0].pts_start, "hi"), segs[0].seg_id)
    assert tr.pts_start == segs[0].pts_start and {w.seg_id for w in tr.words} == {segs[0].seg_id}
    assert tr.words[0].t0 == segs[0].pts_start and tr.words[-1].t1 == pytest.approx(segs[0].pts_end)


def test_reprs_never_show_text():
    tr = Transcript(0, 1, "my number 98765", (Word("98765", 0, 1),), "hi", "local", 0.1)
    assert "98765" not in repr(tr) and "98765" not in repr(tr.words[0])
