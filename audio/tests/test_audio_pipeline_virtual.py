"""T3.2: orchestrator pipeline on a virtual-time loop (docs/specs/audio.md 5.3, 5.8, 9.10-9.11, 9.13).

Speech is synthetic (tone bursts with known word times); the STT is scripted. No network, no real audio.
"""

from __future__ import annotations

import asyncio

import pytest

from audio.orchestrator.pipeline import FAILSAFE_PAD_S, AudioPipeline
from audio.orchestrator.race import SttRace
from audio.pii.detector import SpokenPiiDetector
from audio.pii.rules import SpokenRules
from audio.segmenter import Segmenter
from audio.stt.base import SttProviderError
from audio.testing import ScriptedSTT, Timeline, feed_realtime, intervals_cover, run_virtual
from audio.vad import EnergyFrameModel, StreamingVad
from contracts.config import AudioConfig, Config
from contracts.detection import MaskRequest, MuteSpan
from contracts.enums import Category, MaskKind, Topic
from contracts.events import StageTiming
from contracts.fakes import InProcessBus
from contracts.speech import Transcript, Word

D = 4.0
PHONE = "nau aath saat chhe paanch char teen do ek shunya"


def phone_timeline() -> Timeline:
    tl = Timeline()
    tl.say("aaj ka stream shuru karte hain", pause_after=0.7)
    tl.say("mera number hai " + PHONE, pii=True)  # only the digits are flagged below
    for w in tl.words:
        if w.text in PHONE.split():
            w.pii = w.digit = True
        else:
            w.pii = False
    tl.pause(0.8).say("dhanyavaad doston bahut maza aaya aaj", pause_after=0.5)
    return tl


def build(loop, stts: dict, tl: Timeline | None = None, **cfg) -> tuple[AudioPipeline, InProcessBus]:
    config = Config(delay_seconds=cfg.pop("delay", D), **cfg)
    bus = InProcessBus()
    seg = Segmenter(StreamingVad(EnergyFrameModel(), config.audio.vad), overlap_s=config.audio.overlap_s)
    race = SttRace(stts, order=cfg.get("order", ("sarvam", "local")), hedge_s=0.8, clock=loop.time)
    det = SpokenPiiDetector(SpokenRules())
    return AudioPipeline(config, bus, seg, race, det, clock=loop.time), bus


def covered(spans, w) -> float:
    return intervals_cover([(s.pts_start, s.pts_end) for s in spans], w.t0, w.t1)


def spans_of(bus) -> list[MuteSpan]:
    return [e for t, e in bus.history if t is Topic.MUTE_SPAN]


async def run_scenario(loop, pipe, tl: Timeline):
    t0 = loop.time()
    await feed_realtime(pipe, tl.render(), pts0=0.0)
    await asyncio.sleep(D + 1)  # let the deadlines pass
    await pipe.drain()
    return t0


def test_pii_words_are_muted_before_the_hard_deadline():
    tl = phone_timeline()

    async def main(loop):
        pipe, bus = build(
            loop, {"sarvam": ScriptedSTT(tl, latency=0.9), "local": ScriptedSTT(tl, "local", 5.0)}
        )
        t0 = await run_scenario(loop, pipe, tl)
        return t0, pipe, bus

    t0, pipe, bus = run_virtual(main)
    spans = spans_of(bus)
    digits = [w for w in tl.words if w.digit]
    assert len(digits) == 10
    for w in digits:
        assert covered(spans, w) == pytest.approx(w.t1 - w.t0, abs=1e-6), f"digit {w.t0:.2f} not covered"
        # every piece of the muted word was emitted before the engine's hard deadline for its audio
        assert (
            max(s.completed_at for s in spans if s.pts_end > w.t0 and s.pts_start < w.t1)
            <= t0 + w.t0 + D - 0.1
        )
    assert pipe.stats["failsafe"] == 0 and pipe.stats["late_spans"] == 0
    assert all(s.category in (Category.SPOKEN_PHONE, Category.SPOKEN_OTHER_PII) for s in spans)
    # over-mute: the closing sentence and the greeting stay audible
    for w in tl.words:
        if not w.pii and w.text in {"aaj", "dhanyavaad", "doston"}:
            assert intervals_cover([(s.pts_start, s.pts_end) for s in spans], w.t0, w.t1) == pytest.approx(
                0, abs=1e-6
            )


def test_span_ready_latency_stays_inside_the_pipeline_budget():
    tl = phone_timeline()

    async def main(loop):
        pipe, bus = build(loop, {"sarvam": ScriptedSTT(tl, latency=1.0)})
        await run_scenario(loop, pipe, tl)
        return pipe, bus

    pipe, bus = run_virtual(main)
    assert pipe.latencies and max(pipe.latencies) <= 1.5 + 1.0 + 0.1 + 1e-6  # segment <= 1.5 s + STT 1.0 s
    assert min(pipe.margins) > 0
    stages = {e.stage for t, e in bus.history if t is Topic.TIMING}
    assert {"segment", "stt", "span_ready", "pii_rules"} <= stages or {
        "segment",
        "stt",
        "span_ready",
    } <= stages


def test_slow_stt_triggers_failsafe_over_the_whole_voiced_segment_and_nothing_else():
    tl = Timeline().say("yeh ek lambi baat hai jo chalti rahegi", pause_after=1.0)

    async def main(loop):
        pipe, bus = build(
            loop, {"sarvam": ScriptedSTT(tl, latency=5.0), "local": ScriptedSTT(tl, "local", 5.0)}
        )
        t0 = await run_scenario(loop, pipe, tl)
        return t0, pipe, bus, tl.words[-1].t1

    t0, pipe, bus, end = run_virtual(main)
    spans = spans_of(bus)
    assert spans and all(
        s.granularity == "failsafe" and s.category is Category.SPOKEN_UNVERIFIED for s in spans
    )
    cov = [(s.pts_start, s.pts_end) for s in spans]
    assert intervals_cover(cov, 0.0, end) == pytest.approx(end, abs=1e-6)  # all speech muted
    assert intervals_cover(cov, end + FAILSAFE_PAD_S + 0.1, end + 1.0) == 0.0  # trailing silence passes
    for s in spans:  # soft deadline of the segment that holds the span start: arrival + D - 0.3
        assert s.completed_at <= t0 + s.pts_end - 0.1 + D - 0.3 + 1e-6
    assert pipe.stats["failsafe"] == pipe.stats["voiced"]


def test_silence_and_music_like_unvoiced_audio_pass():
    tl = Timeline()
    tl.end = 3.0  # nothing said

    async def main(loop):
        pipe, bus = build(loop, {"sarvam": ScriptedSTT(tl, latency=5.0)})
        await run_scenario(loop, pipe, tl)
        return bus

    assert spans_of(run_virtual(main)) == []


def test_all_providers_failing_mutes_voiced_speech_at_once():
    tl = Timeline().say("kuch bhi bol rahe hain hum yahan", pause_after=0.5)
    err = SttProviderError("down", 500)

    async def main(loop):
        pipe, bus = build(
            loop,
            {
                "sarvam": ScriptedSTT(tl, fail=err, latency=0.1),
                "local": ScriptedSTT(tl, "local", 0.1, fail=err),
            },
        )
        t0 = await run_scenario(loop, pipe, tl)
        return t0, pipe, bus

    t0, pipe, bus = run_virtual(main)
    spans = spans_of(bus)
    assert spans and all(s.granularity == "failsafe" for s in spans)
    assert pipe.stats["stt_failed"] >= 1 and pipe.stats["late_spans"] == 0


def test_failsafe_can_be_disabled_by_config():
    tl = Timeline().say("kuch bhi bol rahe hain hum yahan", pause_after=0.5)

    async def main(loop):
        config = Config(delay_seconds=D, audio=AudioConfig(failsafe_mute_voiced=False))
        bus = InProcessBus()
        seg = Segmenter(StreamingVad(EnergyFrameModel(), config.audio.vad))
        race = SttRace({"sarvam": ScriptedSTT(tl, latency=9.0)}, clock=loop.time)
        pipe = AudioPipeline(config, bus, seg, race, SpokenPiiDetector(), clock=loop.time)
        await run_scenario(loop, pipe, tl)
        return bus

    assert spans_of(run_virtual(main)) == []


def test_mouth_blur_requests_match_every_mute_span():
    tl = phone_timeline()

    async def main(loop):
        pipe, bus = build(loop, {"sarvam": ScriptedSTT(tl, latency=0.6)})
        await run_scenario(loop, pipe, tl)
        return bus

    bus = run_virtual(main)
    spans = spans_of(bus)
    masks = [e for t, e in bus.history if t is Topic.MASK_REQUEST]
    assert spans and len(masks) == len(spans)
    for s, m in zip(spans, masks, strict=True):
        assert isinstance(m, MaskRequest) and m.kind is MaskKind.MOUTH_OF_SPEAKER and m.source == "audio"
        assert (m.pts_start, m.pts_end, m.frame_pts) == (s.pts_start, s.pts_end, s.pts_start) and not m.quads


def test_transcript_is_published_local_only_and_never_in_spans_or_timings():
    from contracts.enums import LOCAL_ONLY_TOPICS

    assert Topic.TRANSCRIPT in LOCAL_ONLY_TOPICS
    tl = phone_timeline()

    async def main(loop):
        pipe, bus = build(loop, {"sarvam": ScriptedSTT(tl, latency=0.3)})
        await run_scenario(loop, pipe, tl)
        return bus

    bus = run_virtual(main)
    assert any(isinstance(e, Transcript) for t, e in bus.history if t is Topic.TRANSCRIPT)
    for t, e in bus.history:
        if t in (Topic.MUTE_SPAN, Topic.MASK_REQUEST, Topic.TIMING):
            assert isinstance(e, (MuteSpan, MaskRequest, StageTiming))
            assert "nau" not in repr(e) and "paanch" not in repr(e)


def test_split_number_across_segments_is_muted_as_one_continuous_region():
    # 14 digit-words spoken without a pause: the 1.5 s cap cuts them into several segments
    tl = Timeline().say(
        "mera account number hai " + "nau aath saat chhe paanch char teen do ek shunya nau aath saat chhe",
        pause_after=0.5,
    )
    for w in tl.words:
        w.digit = w.pii = w.text not in {"mera", "account", "number", "hai"}

    async def main(loop):
        pipe, bus = build(loop, {"sarvam": ScriptedSTT(tl, latency=0.5)})
        t0 = await run_scenario(loop, pipe, tl)
        return t0, pipe, bus

    t0, pipe, bus = run_virtual(main)
    spans = spans_of(bus)
    digit_words = [w for w in tl.words if w.digit]
    assert pipe.stats["voiced"] >= 3
    cov = [(s.pts_start, s.pts_end) for s in spans]
    for w in digit_words:
        assert intervals_cover(cov, w.t0, w.t1) == pytest.approx(w.t1 - w.t0, abs=1e-6)


class EmptySTT:
    """A provider that answers successfully with nothing (a 200 with an empty body, a filtered hallucination)."""

    name = "empty"

    def __init__(self, provider: str = "sarvam", latency: float = 0.4, text: str = "") -> None:
        self.provider, self.latency, self.text, self.calls = provider, latency, text, 0

    async def transcribe(self, pcm16k, pts_start, lang_hint):
        self.calls += 1
        await asyncio.sleep(self.latency)
        return Transcript(
            pts_start, pts_start + len(pcm16k) / 16_000, self.text, (), "hi", self.provider, self.latency
        )


def test_empty_transcript_for_voiced_speech_is_muted_as_unverified_not_passed():
    tl = Timeline().say("yeh ek lambi baat hai jo chalti rahegi", pause_after=1.0)

    async def main(loop):
        pipe, bus = build(loop, {"sarvam": EmptySTT(), "local": EmptySTT("local")})
        t0 = await run_scenario(loop, pipe, tl)
        return t0, pipe, bus, tl.words[-1].t1

    t0, pipe, bus, end = run_virtual(main)
    spans = spans_of(bus)
    assert spans and all(s.granularity == "failsafe" and s.category is Category.SPOKEN_UNVERIFIED for s in spans)
    assert intervals_cover([(s.pts_start, s.pts_end) for s in spans], 0.0, end) == pytest.approx(end, abs=1e-6)
    assert pipe.stats["thin_transcript"] == pipe.stats["voiced"] > 0
    for s in spans:  # decided as soon as the empty answer arrives: well inside the delay window
        assert s.completed_at <= t0 + s.pts_end - 0.1 + D - 0.3 + 1e-6


def test_transcript_far_too_short_for_the_speech_is_not_trusted():
    from audio.orchestrator.pipeline import too_thin
    from audio.segmenter import Segment

    def seg(speech_ms: float) -> Segment:
        return Segment(1, 0.0, 1.5, None, 0.0, True, speech_ms, False, "cap")

    def tr(*texts: str) -> Transcript:
        ws = tuple(Word(t, 0.1 * i, 0.1 * i + 0.08, False, 1) for i, t in enumerate(texts))
        return Transcript(0.0, 1.5, " ".join(texts), ws, "hi", "sarvam", 0.4)

    assert too_thin(seg(900.0), tr())  # nothing for 0.9 s of speech
    assert too_thin(seg(900.0), tr("...", " "))  # punctuation only
    assert not too_thin(seg(900.0), tr("haan"))
    assert too_thin(seg(2400.0), tr("haan"))  # one word for 2.4 s of speech
    assert not too_thin(seg(2400.0), tr("haan", "bhai"))
