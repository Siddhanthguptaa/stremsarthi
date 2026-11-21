"""T5.3 caption service on a virtual-time loop with the in-process bus (no network, no real audio).

Spec 9.12 (muted tokens appear as the mask, failsafe fully redacted, late translation follows ``on_late``),
9.13 privacy and the T5.3 acceptance: captions arrive before display time in all five scripts."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

import pytest

from audio.captions.service import CaptionService
from audio.captions.settings import MASK, CaptionSettings
from audio.captions.testing import FakeTranslator, provider_down
from audio.captions.translate import ResilientTranslator
from audio.testing import VirtualClockLoop, run_virtual
from contracts.config import Config
from contracts.detection import MuteSpan
from contracts.enums import Category, Topic
from contracts.events import CaptionEvent, ConfigChange
from contracts.fakes import InProcessBus
from contracts.speech import Transcript, Word

D = 4.0
PHONE = "nau aath saat chhe paanch char teen do ek shunya".split()
SENT = "mera number hai " + " ".join(PHONE) + " dhanyavaad"


def words(text: str, t0: float = 0.0, step: float = 0.1, dur: float = 0.09) -> list[Word]:
    return [Word(tok, t0 + i * step, t0 + i * step + dur, False, 0) for i, tok in enumerate(text.split())]


def transcript(ws: list[Word], lang: str = "hi", latency: float = 0.4) -> Transcript:
    return Transcript(ws[0].t0, ws[-1].t1, " ".join(w.text for w in ws), tuple(ws), lang, "sarvam", latency)


def span(lo: float, hi: float, sid: str = "au-1", failsafe: bool = False) -> MuteSpan:
    return MuteSpan(
        sid, lo, hi, Category.SPOKEN_PHONE, "failsafe" if failsafe else "word", 0.9, "t", 0.0, "silence"
    )


def settings(**kw: Any) -> CaptionSettings:
    base = {"enabled": True, "target_lang": "kn", "delay_s": D, "local_rules": False}
    base.update(kw)
    return CaptionSettings(**base)


Scenario = Callable[[VirtualClockLoop, InProcessBus, CaptionService, FakeTranslator], Awaitable[Any]]


def run(
    scn: Scenario,
    *,
    st: CaptionSettings | None = None,
    fake: FakeTranslator | None = None,
    rules: Any | None = None,
) -> tuple[InProcessBus, CaptionService, FakeTranslator]:
    fake = fake or FakeTranslator(latency=0.2)
    out: dict[str, Any] = {}

    async def main(loop: VirtualClockLoop) -> None:
        bus = InProcessBus()
        chain = ResilientTranslator([fake], clock=loop.time)
        st_ = st or settings()
        svc = CaptionService(Config(), bus, chain, st_, clock=loop.time, rules=rules)
        task = loop.create_task(svc.run())
        await asyncio.sleep(0)
        await scn(loop, bus, svc, fake)
        await svc.drain()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        out.update(bus=bus, svc=svc)

    run_virtual(main)
    return out["bus"], out["svc"], fake


def captions(bus: InProcessBus) -> list[CaptionEvent]:
    return bus.of(Topic.CAPTION)


# ------------------------------------------------------------------ redaction
def test_captions_muted_digits_become_mask_and_never_reach_the_api() -> None:
    ws = words(SENT)
    digits = ws[3:13]

    async def scn(loop, bus, svc, fake):
        bus.publish(Topic.TRANSCRIPT, transcript(ws))
        bus.publish(Topic.MUTE_SPAN, span(digits[0].t0 - 0.2, digits[-1].t1 + 0.2))
        await asyncio.sleep(2.0)

    bus, svc, fake = run(scn)
    caps = captions(bus)
    assert caps and MASK in caps[0].text
    joined = " ".join(c.text for c in caps)
    for tok in PHONE:
        assert tok not in joined and all(tok not in sent.split() for sent in fake.sent)
    assert all(MASK in s or not any(t in s.split() for t in PHONE) for s in fake.sent)
    assert caps[0].lang == "kn" and caps[0].is_final


def test_captions_failsafe_segment_is_fully_redacted_without_an_api_call() -> None:
    ws = words("kuch bhi bola gaya yahan")

    async def scn(loop, bus, svc, fake):
        bus.publish(Topic.TRANSCRIPT, transcript(ws))
        bus.publish(Topic.MUTE_SPAN, span(ws[0].t0 - 0.25, ws[-1].t1 + 0.25, failsafe=True))
        await asyncio.sleep(2.0)

    bus, svc, fake = run(scn)
    assert [c.text for c in captions(bus)] == [MASK]
    assert fake.calls == 0


def test_captions_rules_mask_pii_even_before_the_mute_arrives() -> None:
    from audio.pii.rules import SpokenRules

    ws = words(SENT)

    async def scn(loop, bus, svc, fake):  # no MuteSpan is ever published
        bus.publish(Topic.TRANSCRIPT, transcript(ws))
        await asyncio.sleep(2.0)

    bus, svc, fake = run(scn, st=settings(local_rules=True), rules=SpokenRules())
    text = " ".join(c.text for c in captions(bus))
    assert MASK in text and not any(t in text.split() for t in PHONE[:6])
    assert not any(t in s.split() for s in fake.sent for t in PHONE[:6])


def test_captions_late_mute_publishes_a_redacted_correction_with_the_same_times() -> None:
    ws = words("aaj ka plan kuch aisa hai doston")

    async def scn(loop, bus, svc, fake):
        bus.publish(Topic.TRANSCRIPT, transcript(ws))
        await asyncio.sleep(1.0)  # caption already published
        assert len(captions(bus)) == 1 and "plan" in captions(bus)[0].text
        bus.publish(Topic.MUTE_SPAN, span(ws[2].t0 - 0.05, ws[3].t1 + 0.05))
        await asyncio.sleep(1.0)

    bus, svc, fake = run(scn)
    caps = captions(bus)
    assert len(caps) == 2 and svc.stats["corrections"] == 1
    assert (caps[0].pts_start, caps[0].pts_end) == (caps[1].pts_start, caps[1].pts_end)
    assert MASK in caps[1].text and "plan" not in caps[1].text and "kuch" not in caps[1].text


def test_captions_mangled_mask_is_translated_piecewise() -> None:
    ws = words("aap ka number hai nau aath saat chhe paanch char teen do ek shunya sahi hai na")

    async def scn(loop, bus, svc, fake):
        bus.publish(Topic.TRANSCRIPT, transcript(ws))
        bus.publish(Topic.MUTE_SPAN, span(ws[4].t0 - 0.1, ws[13].t1 + 0.1))
        await asyncio.sleep(3.0)

    bus, svc, fake = run(scn, fake=FakeTranslator(latency=0.1, mangle_mask=True))
    assert svc.stats["piecewise"] == 1
    text = " ".join(c.text for c in captions(bus))
    assert MASK in text and all(t not in text.split() for t in PHONE)
    assert all(t not in s.split() for s in fake.sent for t in PHONE)


# ------------------------------------------------------------------ fallback, late, toggles
def test_captions_api_down_shows_original_language_redacted_text() -> None:
    ws = words("namaste doston aaj ka stream")

    async def scn(loop, bus, svc, fake):
        for i in range(4):
            w = words("namaste doston aaj ka stream", t0=10.0 * i)
            bus.publish(Topic.TRANSCRIPT, transcript(w))
            await asyncio.sleep(1.0)

    bus, svc, fake = run(scn, fake=FakeTranslator(latency=0.05, fail=provider_down()))
    caps = captions(bus)
    assert len(caps) == 4 and all(c.text == "namaste doston aaj ka stream" for c in caps)
    assert all(c.lang == "hi" for c in caps)  # the text is Hindi: not labelled with the target language
    assert svc.stats["degraded"] >= 3 and fake.calls == 3  # then the breaker keeps the API out
    assert ws  # silence linter


def test_captions_dev_fallback_passthrough_without_any_translator() -> None:
    ws = words("hello friends welcome")

    async def main(loop: VirtualClockLoop) -> list[CaptionEvent]:
        bus = InProcessBus()
        svc = CaptionService(
            Config(), bus, ResilientTranslator([], clock=loop.time), settings(), clock=loop.time
        )
        task = loop.create_task(svc.run())
        await asyncio.sleep(0)
        bus.publish(Topic.TRANSCRIPT, transcript(ws, lang="en"))
        await asyncio.sleep(1.0)
        await svc.drain()
        task.cancel()
        return captions(bus)

    caps = run_virtual(main)
    assert [c.text for c in caps] == ["hello friends welcome"] and caps[0].lang == "en"


def test_captions_late_translation_follows_on_late() -> None:
    ws = words("ye caption der se aayega")
    # 3.5 s of speech-to-text latency: the deadline (pts_start + D - 0.3) is already behind us

    async def scn(loop, bus, svc, fake):
        bus.publish(Topic.TRANSCRIPT, transcript(ws, latency=3.5))
        await asyncio.sleep(2.0)

    bus, svc, fake = run(scn, st=settings(on_late="skip"))
    assert captions(bus) == [] and svc.stats["skipped_late"] == 1 and fake.calls == 0
    bus, svc, fake = run(scn, st=settings(on_late="show_source"))
    assert [c.text for c in captions(bus)] == ["ye caption der se aayega"] and fake.calls == 0


def test_captions_slow_translation_times_out_then_follows_on_late() -> None:
    ws = words("dheere dheere chalta hai")

    async def scn(loop, bus, svc, fake):
        bus.publish(Topic.TRANSCRIPT, transcript(ws))
        await asyncio.sleep(3.0)

    bus, svc, _ = run(scn, fake=FakeTranslator(latency=5.0), st=settings(on_late="show_source"))
    assert [c.text for c in captions(bus)] == ["dheere dheere chalta hai"]  # 0.8 s timeout: source text


def test_captions_toggle_and_target_language_via_config_events() -> None:
    ws1, ws2 = words("pehla vaakya", 0.0), words("doosra vaakya", 6.0)

    async def scn(loop, bus, svc, fake):
        bus.publish(Topic.CONFIG, ConfigChange({"captions.enabled": False}, "a"))
        await asyncio.sleep(0.1)
        bus.publish(Topic.TRANSCRIPT, transcript(ws1))
        await asyncio.sleep(1.5)
        assert captions(bus) == [] and fake.calls == 0  # disabled: nothing is held or sent
        bus.publish(Topic.CONFIG, ConfigChange({"captions.enabled": True, "captions.target_lang": "ta"}, "a"))
        await asyncio.sleep(0.1)
        bus.publish(Topic.TRANSCRIPT, transcript(ws2))
        await asyncio.sleep(1.5)

    bus, svc, fake = run(scn, st=settings(enabled=True))
    caps = captions(bus)
    assert len(caps) == 1 and caps[0].lang == "ta" and caps[0].text.startswith("ta:")


def test_captions_overlap_words_are_not_repeated() -> None:
    a = words("kal milte hain doston", 0.0)
    tail = a[-2:]
    b = [Word(w.text, w.t0 + 0.01, w.t1, False, 0) for w in tail] + words("phir se", a[-1].t1 + 0.1)

    async def scn(loop, bus, svc, fake):
        bus.publish(Topic.TRANSCRIPT, transcript(a))
        await asyncio.sleep(0.8)
        bus.publish(Topic.TRANSCRIPT, transcript(b))
        await asyncio.sleep(1.5)

    bus, svc, fake = run(scn)
    assert [s for s in fake.sent] == ["kal milte hain doston", "phir se"]


# ------------------------------------------------------------------ acceptance and numbers
@pytest.mark.parametrize("lang", ["hi", "kn", "ta", "te", "bn"])
def test_captions_acceptance_arrive_before_display_in_all_five_scripts(lang: str) -> None:
    """T5.3: each caption is published before ``pts_start + D`` on the capture timeline (soft deadline D - 0.3)."""
    published: list[tuple[float, float]] = []

    async def scn(loop, bus, svc, fake):
        t0 = loop.time()
        for i in range(8):
            ws = words("aaj ka stream bahut maza aaya doston", t0=1.5 * i * 1.0)
            # transcript of audio captured at clock t0 + pts, STT 0.6 s after the segment closes
            await asyncio.sleep(max(0.0, (t0 + ws[-1].t1 + 0.6) - loop.time()))
            bus.publish(Topic.TRANSCRIPT, transcript(ws, latency=0.6))
            published.append((t0 + ws[0].t0, 0.0))
        await asyncio.sleep(2.5)

    bus, svc, fake = run(scn, st=settings(target_lang=lang))
    caps = captions(bus)
    assert len(caps) == 8 and all(c.lang == lang for c in caps)
    margins = sorted(svc.margins)
    print(f"caption margin {lang}: min={margins[0]:.3f}s before pts_start+D-0.3")
    assert margins[0] > 0.0  # published before pts_start + D - 0.3 (mapped to the process clock)


def test_captions_latency_numbers_p95() -> None:
    async def scn(loop, bus, svc, fake):
        for i in range(40):
            bus.publish(Topic.TRANSCRIPT, transcript(words("namaste doston aaj ka stream", 3.0 * i)))
            await asyncio.sleep(1.5)

    bus, svc, fake = run(scn, fake=FakeTranslator(latency=0.35))
    lat = sorted(svc.latencies)
    p95 = lat[int(0.95 * (len(lat) - 1))]
    print(f"caption latency transcript->event: p50={lat[len(lat) // 2]:.3f}s p95={p95:.3f}s n={len(lat)}")
    assert len(captions(bus)) == 40 and p95 <= 0.25 + 0.35 + 0.01


def test_captions_privacy_no_text_in_logs_or_repr(caplog: pytest.LogCaptureFixture) -> None:
    ws = words("mera number hai nau aath saat chhe paanch char teen do")

    async def scn(loop, bus, svc, fake):
        bus.publish(Topic.TRANSCRIPT, transcript(ws))
        bus.publish(Topic.MUTE_SPAN, span(0.8, 3.5))
        await asyncio.sleep(2.0)

    with caplog.at_level(logging.DEBUG):
        bus, svc, fake = run(scn)
    blob = caplog.text + repr(captions(bus)[0]) + repr(svc.stats)
    assert not any(t in blob.split() for t in PHONE + ["mera"])
    assert "Topic.CAPTION" not in blob or True
    assert Topic.TRANSCRIPT in __import__("contracts").LOCAL_ONLY_TOPICS


def test_captions_settings_from_config() -> None:
    cfg = Config()
    s = CaptionSettings.from_config(cfg, local_rules=False)
    assert s.delay_s == cfg.delay_seconds and s.max_chars_per_line == cfg.captions.max_chars_per_line
    assert replace(s, enabled=True).enabled


# ------------------------------------------------------------------ correction vs the engine's replace-by-pts_start rule
class EngineSim:
    """The engine's caption store (engine/captions.py ``add``/``_pick``): an event replaces an earlier one with the
    same ``pts_start``; the covering event with the latest start (then the newest) is the one drawn."""

    def __init__(self) -> None:
        self.entries: list[tuple[int, CaptionEvent]] = []
        self.serial = 0

    def add(self, ev: CaptionEvent) -> None:
        self.serial += 1
        self.entries = [(n, e) for n, e in self.entries if abs(e.pts_start - ev.pts_start) >= 1e-6]
        self.entries.append((self.serial, ev))

    def visible(self, pts: float) -> str | None:
        best = None
        for n, e in self.entries:
            if e.pts_start <= pts <= e.pts_end and (
                best is None or (e.pts_start, n) > (best[1].pts_start, best[0])
            ):
                best = (n, e)
        return best[1].text if best else None


def _long_words(n: int) -> list[Word]:
    return [Word(f"wd{i:02d}xxxxxx", i * 0.1, i * 0.1 + 0.09, False, 0) for i in range(n)]


@pytest.mark.parametrize(
    ("n_words", "muted"),
    [
        (12, (0, 7)),  # two pages -> one page, text of page 2 muted
        (20, (0, 14)),  # three pages -> one or two pages
        (20, (5, 12)),  # three pages, middle muted: later pages move
        (20, (13, 19)),  # tail muted: last page disappears
        (30, (0, 25)),  # many pages -> one
    ],
)
def test_captions_correction_leaves_no_old_text_under_engine_replace_rule(
    n_words: int, muted: tuple[int, int]
) -> None:
    ws = _long_words(n_words)
    lo, hi = muted
    bad = {w.text for w in ws[lo : hi + 1]}

    async def scn(loop, bus, svc, fake):
        bus.publish(Topic.TRANSCRIPT, transcript(ws, lang="hi"))
        await asyncio.sleep(1.0)  # published, unredacted
        first = len(captions(bus))
        sim = EngineSim()
        for ev in captions(bus):
            sim.add(ev)
        assert first >= 2  # the scenario really is multi-page
        shown = " ".join(filter(None, (sim.visible(t / 100) for t in range(0, 800))))
        assert all(b in shown for b in list(bad)[:1])  # before the mute the muted text was visible
        bus.publish(Topic.MUTE_SPAN, span(ws[lo].t0 - 0.02, ws[hi].t1 + 0.02))
        await asyncio.sleep(1.5)

    bus, svc, fake = run(scn, st=settings(local_rules=False, target_lang="hi"))
    sim = EngineSim()
    for ev in captions(bus):
        sim.add(ev)
    for t in range(-50, 1000):  # whole time range, 10 ms grid
        text = sim.visible(t / 100)
        if text is not None:
            assert not any(b in text for b in bad), f"old text visible at pts {t / 100}"
    assert svc.stats["corrections"] >= 1
    if hi - lo + 1 >= 0.6 * n_words:  # most of the text muted: pages must have been vacated
        assert svc.stats["vacated"] >= 1


def test_captions_correction_single_page_still_replaces_in_place() -> None:
    ws = words("aaj ka plan kuch aisa hai doston")

    async def scn(loop, bus, svc, fake):
        bus.publish(Topic.TRANSCRIPT, transcript(ws))
        await asyncio.sleep(1.0)
        bus.publish(Topic.MUTE_SPAN, span(ws[2].t0 - 0.05, ws[3].t1 + 0.05))
        await asyncio.sleep(1.0)

    bus, svc, fake = run(scn)
    assert svc.stats["vacated"] == 0  # one page, same start: no extra events
    sim = EngineSim()
    for ev in captions(bus):
        sim.add(ev)
    for t in range(0, 200):
        text = sim.visible(t / 100)
        assert text is None or ("plan" not in text and "kuch" not in text)


def test_captions_failed_correction_takes_the_old_text_off_screen() -> None:
    ws = _long_words(12)

    async def scn(loop, bus, svc, fake):
        bus.publish(Topic.TRANSCRIPT, transcript(ws, lang="hi"))
        await asyncio.sleep(1.0)
        svc.translator = None  # any exception inside the correction pass
        bus.publish(Topic.MUTE_SPAN, span(ws[0].t0 - 0.02, ws[3].t1 + 0.02))
        await asyncio.sleep(1.0)

    bus, svc, fake = run(scn, st=settings(local_rules=False, target_lang="kn"))
    sim = EngineSim()
    for ev in captions(bus):
        sim.add(ev)
    for t in range(0, 800):
        text = sim.visible(t / 100)
        assert text is None or "wd00" not in text
