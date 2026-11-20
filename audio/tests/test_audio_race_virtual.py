"""T3.2: STT race (docs/specs/audio.md 5.2, 9.9) with stubs on a virtual-time loop."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from audio.orchestrator.race import SttAllFailed, SttRace
from audio.segmenter import Segment
from audio.stt.base import SttProviderError, SttRateLimited
from audio.testing import SR, ScriptedSTT, Timeline, run_virtual

TL = Timeline().say("ek do teen char paanch chhe saat aath nau shunya " * 6)


def seg(i: int, t0: float, dur: float = 1.5, arrival: float = 0.0) -> Segment:
    return Segment(
        i, t0, t0 + dur, np.zeros(int(dur * SR), dtype=np.int16), arrival, True, 1000.0, False, "pause"
    )


def ctx(s: Segment, min_s: float):
    return np.zeros(int(min_s * SR), dtype=np.int16), s.pts_end - min_s


def race(loop, **providers):
    return SttRace(providers, order=("sarvam", "local"), hedge_s=0.8, clock=loop.time)


def test_fast_primary_wins_without_hedge():
    async def main(loop):
        sar, loc = ScriptedSTT(TL, "sarvam", 0.3), ScriptedSTT(TL, "local", 0.3)
        r = race(loop, sarvam=sar, local=loc)
        t0 = loop.time()
        tr = await r.transcribe(seg(1, 0.0))
        return tr, loop.time() - t0, loc.calls, r

    tr, dt, local_calls, r = run_virtual(main)
    assert tr.provider == "sarvam" and dt == pytest.approx(0.3) and local_calls == 0 and r.hedges == 0
    assert all(w.seg_id == 1 for w in tr.words)


def test_slow_primary_starts_the_local_hedge_at_0_8s_and_first_valid_wins():
    async def main(loop):
        sar, loc = ScriptedSTT(TL, "sarvam", 1.7), ScriptedSTT(TL, "local", 0.3)
        r = race(loop, sarvam=sar, local=loc)
        t0 = loop.time()
        tr = await r.transcribe(seg(1, 0.0))
        return tr, loop.time() - t0, loc.call_times[0] - t0, r

    tr, dt, hedge_at, r = run_virtual(main)
    assert (
        hedge_at == pytest.approx(0.8)
        and tr.provider == "local"
        and dt == pytest.approx(1.1)
        and r.hedges == 1
    )


def test_late_primary_result_is_discarded_and_empty_text_is_valid():
    async def main(loop):
        empty = ScriptedSTT(Timeline(), "sarvam", 0.2)  # no words: a successful empty transcript
        r = race(loop, sarvam=empty, local=ScriptedSTT(TL, "local", 0.1))
        return await r.transcribe(seg(1, 0.0)), r

    tr, r = run_virtual(main)
    assert tr.provider == "sarvam" and tr.text == "" and r.wins["local"] == 0


def test_primary_error_fails_over_to_groq_with_3s_audio_and_cools_down():
    async def main(loop):
        err = SttRateLimited("429")
        sar = ScriptedSTT(TL, "sarvam", 0.1, fail=err)
        grq = ScriptedSTT(TL, "groq", 0.4)
        loc = ScriptedSTT(TL, "local", 1.5)
        sizes: list[int] = []
        orig = grq.transcribe

        async def spy(pcm, pts, lang):
            sizes.append(len(pcm))
            return await orig(pcm, pts, lang)

        grq.transcribe = spy  # type: ignore[method-assign]
        r = SttRace(
            {"sarvam": sar, "groq": grq, "local": loc},
            order=("sarvam", "local"),
            hedge_s=0.8,
            clock=loop.time,
        )
        out = [await r.transcribe(seg(1, 10.0), None, ctx)]
        for k in range(2, 6):  # inside the 30 s cool-down: sarvam is not asked again
            await asyncio.sleep(3.0)  # 3.4 s per segment: under the 18/min Groq budget
            out.append(await r.transcribe(seg(k, 10.0 + 1.5 * k), None, ctx))
        await asyncio.sleep(30)
        out.append(await r.transcribe(seg(9, 60.0), None, ctx))
        return out, sar.calls, sizes

    out, sarvam_calls, sizes = run_virtual(main)
    assert all(t.provider == "groq" for t in out)
    assert sarvam_calls == 2  # the first segment, and one retry after the cool-down
    assert all(n >= 3.0 * SR for n in sizes)  # Groq always gets at least 3.0 s of audio
    # only the words of the segment itself are kept from the wider request
    assert all(w.t1 > 10.0 for w in out[0].words)


def test_groq_stays_within_18_requests_per_minute_and_local_takes_the_rest():
    async def main(loop):
        sar = ScriptedSTT(TL, "sarvam", 0.1, fail=SttProviderError("down", 500))
        grq = ScriptedSTT(TL, "groq", 0.3)
        loc = ScriptedSTT(TL, "local", 0.3)
        r = SttRace({"sarvam": sar, "groq": grq, "local": loc}, order=("sarvam", "local"), hedge_s=0.8,
                    clock=loop.time, rpm={"groq": 18.0})  # fmt: skip
        got = []
        t0 = loop.time()
        for k in range(40):  # 40 segments: continuous speech
            got.append(await r.transcribe(seg(k, k * 1.5), None, ctx))
            await asyncio.sleep(1.5)
        return got, grq.calls, loc.calls, loop.time() - t0

    got, groq_calls, local_calls, elapsed = run_virtual(main)
    assert len(got) == 40 and groq_calls <= 2 + 18 * elapsed / 60.0  # 18 per minute plus the burst of 2
    assert local_calls >= 40 - groq_calls  # the rest is served locally


def test_every_provider_failing_raises():
    async def main(loop):
        e = SttProviderError("down", 500)
        r = race(
            loop, sarvam=ScriptedSTT(TL, "sarvam", 0.1, fail=e), local=ScriptedSTT(TL, "local", 0.1, fail=e)
        )
        with pytest.raises(SttAllFailed):
            await r.transcribe(seg(1, 0.0))
        return r.errors

    errors = run_virtual(main)
    assert errors["sarvam"] == 1 and errors["local"] == 1


def test_a_provider_that_never_answers_times_out_and_is_cooled_down():
    async def main(loop):
        sar = ScriptedSTT(TL, "sarvam", 60.0)
        loc = ScriptedSTT(
            TL, "local", 2.5
        )  # slower than the primary's 1.8 s timeout: the primary really times out
        r = SttRace({"sarvam": sar, "local": loc}, order=("sarvam", "local"), hedge_s=0.8, clock=loop.time,
                    timeouts={"sarvam": 1.8, "local": 3.0})  # fmt: skip
        t0 = loop.time()
        for k in range(3):
            tr = await r.transcribe(seg(k, k * 1.5))
            assert tr.provider == "local"
        return loop.time() - t0, sar.calls

    elapsed, calls = run_virtual(main)
    assert calls == 2  # after two timeouts the primary is skipped for the cool-down
    assert elapsed < 3 * 3.4
