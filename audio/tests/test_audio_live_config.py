"""Live config (Topic.CONFIG) in the audio pipeline: spoken category toggles, languages, invalid input."""

from __future__ import annotations

import asyncio

import pytest

from audio.orchestrator.live_config import LiveAudioSettings, min_confidence
from audio.orchestrator.pipeline import AudioPipeline
from audio.orchestrator.race import SttRace
from audio.pii.detector import SpokenPiiDetector
from audio.pii.rules import SpokenRules
from audio.segmenter import Segmenter
from audio.testing import ScriptedSTT, Timeline, feed_realtime, intervals_cover, run_virtual
from audio.vad import EnergyFrameModel, StreamingVad
from contracts.config import Config
from contracts.enums import Category, Topic
from contracts.events import ConfigChange
from contracts.fakes import InProcessBus

D = 4.0
PHONE = "nau aath saat chhe paanch char teen do ek shunya"


def phone_timeline() -> Timeline:
    tl = Timeline()
    tl.say("aaj ka stream shuru karte hain", pause_after=0.7)
    tl.say("mera number hai " + PHONE, pii=True)
    for w in tl.words:
        w.pii = w.digit = w.text in PHONE.split()
    tl.pause(0.8).say("dhanyavaad doston bahut maza aaya aaj", pause_after=0.5)
    return tl


def build(loop, stts):
    config = Config(delay_seconds=D)
    bus = InProcessBus()
    seg = Segmenter(StreamingVad(EnergyFrameModel(), config.audio.vad), overlap_s=config.audio.overlap_s)
    race = SttRace(stts, order=("sarvam", "local"), hedge_s=0.8, clock=loop.time)
    det = SpokenPiiDetector(SpokenRules())
    return AudioPipeline(config, bus, seg, race, det, clock=loop.time), bus


def covered(spans, w) -> float:
    """Fraction of the word inside a mute span."""
    return intervals_cover([(s.pts_start, s.pts_end) for s in spans], w.t0, w.t1) / (w.t1 - w.t0)


def spans_of(bus):
    return [e for t, e in bus.history if t is Topic.MUTE_SPAN]


async def run_scenario(loop, pipe, tl):
    t0 = loop.time()
    await feed_realtime(pipe, tl.render(), pts0=0.0)
    await asyncio.sleep(D + 1)
    await pipe.drain()
    return t0


PHONE_TOKENS = set(PHONE.split())


def digits(tl: Timeline):
    return [w for w in tl.words if w.text in PHONE_TOKENS and w.pii]


def change(**kv) -> ConfigChange:
    return ConfigChange({k.replace("__", "."): v for k, v in kv.items()}, "test")


async def scenario(loop, diffs, tl=None, stts=None):
    tl = tl or phone_timeline()
    pipe, bus = build(
        loop, stts or {"sarvam": ScriptedSTT(tl, latency=0.5), "local": ScriptedSTT(tl, "local", 5.0)}
    )
    sub = bus.subscribe([Topic.CONFIG])
    task = loop.create_task(pipe.watch_config(sub))
    for d in diffs:
        bus.publish(Topic.CONFIG, d)  # a burst before the first chunk
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    t0 = await run_scenario(loop, pipe, tl)
    task.cancel()
    return pipe, bus, tl, t0


def test_phone_category_off_emits_no_phone_span_and_phone_plus_other_off_leaves_no_span():
    # the partial digit run heard first is a shape guess (other PII), so it is only gone when that is off too
    async def main(loop):
        return await scenario(loop, [change(categories__spoken_phone__enabled=False)])

    pipe, bus, tl, _ = run_virtual(main)
    assert not [s for s in spans_of(bus) if s.category is Category.SPOKEN_PHONE]
    assert pipe.stats["failsafe"] == 0

    async def main2(loop):
        both = ConfigChange(
            {"categories.spoken_phone.enabled": False, "categories.spoken_other_pii.enabled": False}, "test"
        )
        return await scenario(loop, [both])

    _, bus2, tl2, _ = run_virtual(main2)
    assert all(covered(spans_of(bus2), w) < 0.5 for w in digits(tl2))


def test_a_disabled_kind_does_not_stop_other_kinds_and_on_resumes():
    # "otp" style digits are SPOKEN_OTHER_PII: switching phone off leaves them muted (6-9 digit run, no 10)
    tl = Timeline().say("ye code hai one two three four five six", pause_after=0.5)
    for w in tl.words:
        w.pii = w.digit = w.text in {"one", "two", "three", "four", "five", "six"}

    async def main(loop):
        return await scenario(loop, [change(categories__spoken_phone__enabled=False)], tl)

    _, bus, tl, _ = run_virtual(main)
    assert all(covered(spans_of(bus), w) > 0.5 for w in tl.words if w.pii)

    async def main2(loop):  # off, then on again before audio starts: spans resume
        return await scenario(
            loop,
            [change(categories__spoken_phone__enabled=False), change(categories__spoken_phone__enabled=True)],
        )

    _, bus2, tl2, _ = run_virtual(main2)
    assert all(covered(spans_of(bus2), w) > 0.5 for w in digits(tl2))


def test_failsafe_spans_stay_when_every_category_is_off():
    tl = phone_timeline()
    all_off = ConfigChange(
        {
            f"categories.{c}.enabled": False
            for c in ("spoken_phone", "spoken_upi", "spoken_address", "spoken_other_pii")
        },
        "test",
    )

    async def main(loop):
        down = {"sarvam": ScriptedSTT(tl, latency=5.0), "local": ScriptedSTT(tl, "local", 5.0)}
        return await scenario(loop, [all_off], tl, down)

    pipe, bus, tl, _ = run_virtual(main)
    spans = spans_of(bus)
    assert spans and all(s.category is Category.SPOKEN_UNVERIFIED for s in spans)
    assert all(covered(spans, w) > 0.99 for w in tl.words)  # unverified voiced speech is still muted


def test_toggle_takes_effect_within_one_segment():
    tl = phone_timeline()

    async def main(loop):
        pipe, bus = build(
            loop, {"sarvam": ScriptedSTT(tl, latency=0.5), "local": ScriptedSTT(tl, "local", 5.0)}
        )
        sub = bus.subscribe([Topic.CONFIG])
        task = loop.create_task(pipe.watch_config(sub))
        t0 = loop.time()
        await feed_realtime(pipe, tl.render(), pts0=0.0)
        await asyncio.sleep(5.0)
        await pipe.drain()
        n_before = len(spans_of(bus))
        bus.publish(Topic.CONFIG, change(categories__spoken_phone__enabled=False))
        await asyncio.sleep(0.01)
        task.cancel()
        return pipe, n_before, t0

    pipe, n_before, _ = run_virtual(main)
    assert n_before > 0 and pipe.live.enabled[Category.SPOKEN_PHONE] is False


def test_languages_change_the_stt_hint_and_invalid_lists_are_ignored():
    s = LiveAudioSettings(["hi", "en", "kn", "ta", "te", "bn"])
    assert s.lang_hint is None
    assert s.apply({"audio.languages": ["kn"]}) and s.lang_hint == "kn"
    for bad in ([], ["xx"], ["hi", 3], "hi", None, ["kn", "klingon"], 7):
        assert not s.apply({"audio.languages": bad})
        assert s.lang_hint == "kn"  # the last valid list is kept
    assert s.apply({"audio.languages": [" HI ", "en", "hi"]}) and s.languages == ("hi", "en")


def test_pipeline_uses_the_live_language_hint_for_the_next_segment():
    tl = phone_timeline()
    seen: list[str | None] = []

    class Spy(ScriptedSTT):
        async def transcribe(self, pcm16k, pts_start, lang_hint):
            seen.append(lang_hint)
            return await super().transcribe(pcm16k, pts_start, lang_hint)

    async def main(loop):
        return await scenario(
            loop,
            [change(audio__languages=["ta"])],
            tl,
            {"sarvam": Spy(tl, latency=0.5), "local": ScriptedSTT(tl, "local", 5.0)},
        )

    run_virtual(main)
    assert seen and set(seen) == {"ta"}


@pytest.mark.parametrize(
    "diff",
    [
        {"categories.spoken_phone.enabled": "false"},  # a string is not a bool
        {"categories.spoken_phone.enabled": 0},
        {"categories.spoken_phone.enabled": None},
        {"categories.spoken_phone.sensitivity": 7},
        {"categories.spoken_phone.sensitivity": True},
        {"categories.face.enabled": False},  # not an audio category
        {"categories.spoken_unverified.enabled": False},  # fail-safe is not a category
        {"categories.spoken_phone": False},
        {"categories.spoken_phone.enabled.x": False},
        {"audio.mute_mode": "mumble"},
        {"audio.unknown": 1},
    ],
)
def test_malformed_or_unknown_diffs_never_disable_a_category(diff):
    s = LiveAudioSettings()
    assert not s.apply(diff)
    assert all(s.enabled.values()) and s.mute_mode == "silence"
    assert s.allows(Category.SPOKEN_PHONE, 0.5) and s.allows(Category.SPOKEN_UNVERIFIED, 0.0)


def test_apply_is_idempotent_and_a_bad_entry_does_not_block_the_rest():
    s = LiveAudioSettings()
    d = {
        "categories.spoken_upi.enabled": False,
        "categories.spoken_phone.enabled": "no",
        "audio.mute_mode": "bleep",
    }
    assert s.apply(d) and s.version == 1
    assert not s.apply(d) and s.version == 1  # the startup republish changes nothing
    assert not s.enabled[Category.SPOKEN_UPI] and s.enabled[Category.SPOKEN_PHONE] and s.mute_mode == "bleep"
    assert not s.allows(Category.SPOKEN_UPI, 1.0) and s.allows(Category.SPOKEN_UNVERIFIED, 0.0)


def test_sensitivity_sets_a_confidence_floor():
    s = LiveAudioSettings()
    assert s.allows(Category.SPOKEN_OTHER_PII, 0.5) and min_confidence(0.5) == pytest.approx(0.4)
    s.apply({"categories.spoken_other_pii.sensitivity": 0.0})
    assert not s.allows(Category.SPOKEN_OTHER_PII, 0.5) and s.allows(Category.SPOKEN_OTHER_PII, 0.9)
    assert s.allows(Category.SPOKEN_PHONE, 0.5)  # per category
    s.apply({"categories.spoken_other_pii.sensitivity": 1.0})
    assert s.allows(Category.SPOKEN_OTHER_PII, 0.1)


def test_initial_categories_come_from_the_config():
    from contracts.config import Config

    cfg = Config(delay_seconds=4.0)
    cfg.categories[Category.SPOKEN_ADDRESS].enabled = False
    s = LiveAudioSettings()
    s.load_categories(cfg.categories)
    assert not s.enabled[Category.SPOKEN_ADDRESS] and s.enabled[Category.SPOKEN_PHONE]
    s2 = LiveAudioSettings()
    s2.load_categories(object())  # malformed: the safe defaults stay
    assert all(s2.enabled.values())
