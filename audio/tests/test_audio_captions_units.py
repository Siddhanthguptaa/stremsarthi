"""T5.3 caption units: redaction maths, layout and timing, language planning, translator adapters (no network)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from audio.captions.langs import dominant_script, plan_conversion
from audio.captions.redact import drop_overlap, has_visible, mask_pieces, pieces_text, word_flags
from audio.captions.settings import MASK, CaptionSettings
from audio.captions.testing import FakeTranslator
from audio.captions.timing import ClockMap, schedule, visible_len, wrap_lines, wrap_pages
from audio.captions.translate import (
    Ct2Backend,
    IndicTrans2Translator,
    PassthroughTranslator,
    ResilientTranslator,
    SarvamTranslator,
    TranslateBusy,
    TranslateRateLimited,
    TranslateUnavailable,
    build_translator,
    pick_sarvam_model,
)
from contracts.config import CaptionsConfig, Config
from contracts.speech import Word

S = CaptionSettings()


def W(text: str, t0: float, t1: float, approx: bool = False) -> Word:
    return Word(text, t0, t1, approx, 0)


# ------------------------------------------------------------------ redaction
def test_captions_word_flags_overlap_rule() -> None:
    words = [W("a", 0.0, 0.27), W("b", 0.32, 0.59), W("c", 0.64, 0.91)]
    # span over b padded 0.2 s each side: covers b fully, a by 0.15 s, c by 0.15 s of 0.27
    flags = word_flags(words, [(0.12, 0.79)], 0.06)
    assert flags == [True, True, True]
    assert word_flags(words, [(0.26, 0.40)], 0.06) == [False, True, False]  # 10 ms touch of a: not masked
    assert word_flags(words, [], 0.06) == [False, False, False]


def test_captions_mask_pieces_collapse_and_text() -> None:
    words = [W("mera", 0, 0.3), W("nau", 0.3, 0.6), W("aath", 0.6, 0.9), W("hai", 0.9, 1.2)]
    pieces = mask_pieces(words, [False, True, True, False])
    assert [p.text for p in pieces] == ["mera", None, "hai"]
    assert pieces_text(pieces, MASK) == f"mera {MASK} hai"
    assert not has_visible(mask_pieces(words, [True] * 4))


def test_captions_drop_overlap_removes_repeated_tail() -> None:
    tail = [W("kal", 0.0, 0.3), W("milte", 0.3, 0.7), W("hain", 0.7, 1.0)]
    new = [W("milte", 0.31, 0.7), W("hain", 0.71, 1.0), W("doston", 1.1, 1.5)]
    assert [w.text for w in drop_overlap(tail, new, 1.0)] == ["doston"]
    assert [w.text for w in drop_overlap(tail, [W("alag", 1.1, 1.4)], 1.0)] == ["alag"]


@given(
    st.lists(st.tuples(st.floats(0, 8), st.floats(0.05, 0.6)), min_size=1, max_size=12),
    st.lists(st.tuples(st.floats(0, 8), st.floats(0.05, 2.0)), max_size=4),
)
def test_captions_property_no_muted_word_survives(
    ws: list[tuple[float, float]], ivs: list[tuple[float, float]]
) -> None:
    """Every word with at least half of it (or 60 ms) inside a mute interval is masked; its text never appears."""
    words = [W(f"w{i}x", a, a + d) for i, (a, d) in enumerate(ws)]
    intervals = [(a, a + d) for a, d in ivs]
    flags = word_flags(words, intervals, 0.06)
    text = pieces_text(mask_pieces(words, flags), MASK)
    for w in words:
        inside = any(lo <= w.t0 and w.t1 <= hi for lo, hi in intervals)
        if inside:
            assert w.text not in text.split()


# ------------------------------------------------------------------ layout and timing
def test_captions_wrap_limits_lines_and_indic_marks() -> None:
    text = "यह एक बहुत लंबा वाक्य है जिसे दो पंक्तियों में बांटना है और फिर भी बहुत लंबा है यार"
    for line in wrap_lines(text, 42):
        assert visible_len(line) <= 42
    pages = wrap_pages(text * 3, 42, 2)
    assert all(p.count("\n") <= 1 for p in pages) and len(pages) >= 2
    assert visible_len("क्") == 1  # virama (Mn) is not counted


def test_captions_schedule_single_and_split() -> None:
    one = schedule(["hello"], 10.0, 10.5, S)[0]
    assert one.pts_start == 10.0 and one.pts_end == pytest.approx(11.2)  # min 1.2 s
    long = schedule(["x"], 10.0, 17.0, S)[0]
    assert long.pts_end - long.pts_start == pytest.approx(4.0)  # max 4 s
    mid = schedule(["abc"], 10.0, 11.5, S)[0]
    assert mid.pts_end == pytest.approx(11.8)  # last word end + 0.3
    two = schedule(["a" * 40, "b" * 40], 0.0, 3.0, S)
    assert two[0].pts_start == 0.0 and two[0].pts_end == pytest.approx(two[1].pts_start)
    assert two[1].pts_end == pytest.approx(3.3)


def test_captions_clock_map_due() -> None:
    cm = ClockMap()
    assert cm.due(0.0, 4.0, 0.3) is None
    cm.observe(pts_end=1.5, t_recv=102.0, latency_s=0.5)  # audio to 1.5 s captured at 101.5
    assert cm.due(0.0, 4.0, 0.3) == pytest.approx(100.0 + 3.7)


# ------------------------------------------------------------------ language planning
def test_captions_plan_conversion() -> None:
    assert dominant_script("नमस्ते दोस्तों") == "devanagari"
    assert plan_conversion("hi", "namaste doston", "hi").action == "transliterate"
    assert plan_conversion("hi", "नमस्ते दोस्तों", "hi").action == "none"
    p = plan_conversion("hi", "namaste doston", "kn")
    assert (p.action, p.src, p.tgt) == ("translate", "hi", "kn")
    assert plan_conversion("unknown", "hello friends", "hi").src == "auto"
    assert plan_conversion("en", "नमस्ते", "en").src == "hi"  # tag contradicts the script
    assert plan_conversion("en", "hello", "en").action == "none"
    assert plan_conversion("hi", MASK, "kn").action == "none"


# ------------------------------------------------------------------ Sarvam adapter (mock transport)
def _sarvam(handler, **kw) -> SarvamTranslator:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SarvamTranslator("test-key", client=client, **kw)


async def test_captions_sarvam_request_shape_and_models() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        key = "transliterated_text" if req.url.path.endswith("transliterate") else "translated_text"
        return httpx.Response(200, json={key: "ok"})

    t = _sarvam(handler)
    assert await t.translate("hello", "auto", "kn") == "ok"
    assert await t.translate("hello", "en", "ur") == "ok"
    assert await t.transliterate("namaste", "en", "hi") == "ok"
    import json

    b0, b1, b2 = (json.loads(r.content) for r in seen)
    assert (
        b0["model"] == "mayura:v1"
        and b0["source_language_code"] == "auto"
        and b0["target_language_code"] == "kn-IN"
    )
    assert b1["model"] == "sarvam-translate:v1" and b1["mode"] == "formal"
    assert seen[0].headers["api-subscription-key"] == "test-key"
    assert "model" not in b2 and b2["target_language_code"] == "hi-IN"
    with pytest.raises(TranslateUnavailable):
        pick_sarvam_model("auto", "ur")


async def test_captions_sarvam_errors_and_budget() -> None:
    t = _sarvam(lambda r: httpx.Response(429, json={}))
    with pytest.raises(TranslateRateLimited):
        await t.translate("hi", "en", "hi")
    t = _sarvam(lambda r: httpx.Response(200, json={"x": 1}))
    with pytest.raises(Exception, match="unusable body"):
        await t.translate("hi", "en", "hi")
    with pytest.raises(TranslateUnavailable):
        await SarvamTranslator("").translate("hi", "en", "hi")
    bucket = _sarvam(lambda r: httpx.Response(200, json={"translated_text": "a"}), rpm=60, burst=1)
    await bucket.translate("a", "en", "hi")
    with pytest.raises(TranslateRateLimited):  # local bucket sheds load instead of earning a 429
        await bucket.translate("a", "en", "hi")


# ------------------------------------------------------------------ IndicTrans2 (no weights, never downloads)
def test_captions_indictrans2_unavailable_without_weights(tmp_path: Path) -> None:
    be = Ct2Backend(root=tmp_path)
    assert not be.available()
    assert any(m.startswith("weights:") for m in be.missing())
    assert not IndicTrans2Translator(be).available


async def test_captions_indictrans2_adapter_with_fake_backend() -> None:
    calls: list[tuple[str, str, str, str]] = []

    class Backend:
        def available(self) -> bool:
            return True

        def translate(self, text: str, src_code: str, tgt_code: str, direction: str) -> str:
            calls.append((text, src_code, tgt_code, direction))
            return "ಅನುವಾದ"

    t = IndicTrans2Translator(Backend())
    assert t.available
    assert await t.translate("hello", "en", "kn") == "ಅನುವಾದ"
    await t.translate("नमस्ते", "hi", "en")
    await t.translate("नमस्ते", "hi", "kn")
    assert [c[3] for c in calls] == ["en-indic", "indic-en", "indic-indic"]
    assert calls[0][1:3] == ("eng_Latn", "kan_Knda")
    with pytest.raises(TranslateUnavailable):
        await t.translate("x", "en", "sa")
    with pytest.raises(TranslateUnavailable):
        await t.transliterate("x", "en", "hi")


async def test_captions_indictrans2_sheds_load_when_busy() -> None:
    import threading

    gate = threading.Event()

    class Slow:
        def available(self) -> bool:
            return True

        def translate(self, *a: str) -> str:
            gate.wait(2.0)
            return "x"

    t = IndicTrans2Translator(Slow())
    first = asyncio.create_task(t.translate("a", "en", "hi"))
    await asyncio.sleep(0.05)
    with pytest.raises(TranslateBusy):
        await t.translate("b", "en", "hi")
    gate.set()
    await first


def test_captions_dev_profile_never_uses_indictrans2() -> None:
    cfg = Config(captions=CaptionsConfig(enabled=True))
    tr = build_translator(cfg, CaptionSettings.from_config(cfg))
    assert all(t.name != "indictrans2" for t in tr.chain)  # dev: Sarvam (if a key exists) or nothing


# ------------------------------------------------------------------ chain
async def test_captions_chain_degrades_to_passthrough_and_breaker() -> None:
    from audio.captions.testing import provider_down

    fake = FakeTranslator(latency=0, fail=provider_down())
    chain = ResilientTranslator([fake], failures=3, open_s=20.0)
    plan = plan_conversion("hi", "namaste", "kn")
    for _ in range(5):
        c = await chain.convert("namaste", plan, 0.8)
        assert c.degraded and c.text == "namaste" and c.provider == "passthrough"
    assert fake.calls == 3  # breaker opened after 3 failures: no more calls while it is open


async def test_captions_chain_timeout_and_budget() -> None:
    slow = FakeTranslator(latency=5.0)
    chain = ResilientTranslator([slow], budget=None)
    c = await chain.convert("hello", plan_conversion("en", "hello", "hi"), 0.3)
    assert c.degraded and chain.stats["timeouts"] == 1
    from audio.captions.translate import CharBudget

    ok = FakeTranslator(latency=0)
    chain = ResilientTranslator([ok], budget=CharBudget(10, 100))
    assert not (await chain.convert("hello", plan_conversion("en", "hello", "hi"), 1)).degraded
    assert (await chain.convert("hello world", plan_conversion("en", "hello world", "hi"), 1)).degraded
    assert chain.stats["budget_denied"] == 1


async def test_captions_passthrough_translator() -> None:
    p = PassthroughTranslator()
    assert await p.translate("abc", "en", "hi") == "abc"


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("SARVAM_API_KEY"), reason="no SARVAM_API_KEY")
async def test_captions_sarvam_live() -> None:
    t = SarvamTranslator()
    out = await t.translate("Hello friends, welcome to the stream", "en", "hi")
    assert dominant_script(out) == "devanagari"
