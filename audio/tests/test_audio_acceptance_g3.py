"""T3.2/T3.3 acceptance (gate G3, docs/specs/audio.md 9.15) on a seeded synthetic scenario.

Speech is a tone timeline with known word times; the STT is scripted (a perfect recogniser for the API
rows, a lossy one for the local-only row). Numbers are stub-based: they test the pipeline logic (timing,
merging, fail-safe), not recogniser accuracy on real Hindi audio.
"""

from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path

from audio.orchestrator import build
from audio.orchestrator.pipeline import AudioPipeline
from audio.orchestrator.race import SttRace
from audio.pii.detector import SpokenPiiDetector
from audio.pii.rules import SpokenRules
from audio.segmenter import Segmenter
from audio.stt.base import SttProviderError
from audio.testing import ScriptedSTT, Timeline, feed_realtime, intervals_cover, run_virtual
from audio.vad import EnergyFrameModel, StreamingVad
from contracts.config import Config
from contracts.detection import MuteSpan
from contracts.enums import Topic
from contracts.fakes import InProcessBus
from contracts.media import AudioChunk

D = 4.0
FILLER = (
    "aaj hum baat karenge gaming ke baare mein aur phir match shuru karenge sab log dekhte rahiye".split()
)
DIGITS = "shunya ek do teen char paanch chhe saat aath nau".split()


def scenario(seed: int = 11) -> Timeline:
    rng = random.Random(seed)
    tl = Timeline()
    for k in range(10):
        tl.say(" ".join(rng.choices(FILLER, k=rng.randint(4, 7))), pause_after=rng.uniform(0.7, 1.1))
        kind = k % 3
        if kind == 0:  # spoken phone number after a cue
            tl.say("mera number hai")
            first = len(tl.words)
            tl.say(
                " ".join([rng.choice("6789") and rng.choice(DIGITS[6:]) or "nau"] + rng.choices(DIGITS, k=9))
            )
            for w in tl.words[first:]:
                w.pii = w.digit = True
        elif kind == 1:  # otp with a cue
            tl.say("otp hai")
            first = len(tl.words)
            tl.say(" ".join(rng.choices(DIGITS[1:], k=6)))
            for w in tl.words[first:]:
                w.pii = w.digit = True
        else:  # spoken upi id
            first = len(tl.words)
            tl.say("rahul nau aath at ok s b i")
            for w in tl.words[first:]:
                w.pii = True
        tl.pause(rng.uniform(0.8, 1.2))
    return tl


def build_pipe(loop, stts, **race_kw):
    cfg = Config(delay_seconds=D)
    bus = InProcessBus()
    seg = Segmenter(StreamingVad(EnergyFrameModel(), cfg.audio.vad), overlap_s=cfg.audio.overlap_s)
    race = SttRace(stts, order=("sarvam", "local"), hedge_s=0.8, clock=loop.time, **race_kw)
    return AudioPipeline(cfg, bus, seg, race, SpokenPiiDetector(SpokenRules()), clock=loop.time), bus


def spans_of(bus):
    return [e for t, e in bus.history if t is Topic.MUTE_SPAN]


async def run(loop, pipe, tl):
    t0 = loop.time()
    await feed_realtime(pipe, tl.render(), pts0=0.0)
    await asyncio.sleep(D + 1)
    await pipe.drain()
    return t0


def measure(tl, spans, t0):
    cov = [(s.pts_start, s.pts_end) for s in spans]
    pii = [w for w in tl.words if w.pii]
    dig = [w for w in tl.words if w.digit]

    def inside(ws):
        ok = 0
        for w in ws:
            full = intervals_cover(cov, w.t0, w.t1) >= (w.t1 - w.t0) - 1e-6
            late = [s.completed_at for s in spans if s.pts_end > w.t0 and s.pts_start < w.t1]
            ok += bool(full and late and max(late) <= t0 + w.t0 + D - 0.1)
        return ok / max(1, len(ws))

    non = [w for w in tl.words if not w.pii]
    over = sum(intervals_cover(cov, w.t0, w.t1) for w in non) / sum(w.t1 - w.t0 for w in non)
    return inside(pii), inside(dig), over


def test_g3_mute_coverage_overmute_and_latency_with_apis_up():
    tl = scenario()

    async def main(loop):
        lat = random.Random(3)
        sar = ScriptedSTT(tl, "sarvam", latency=lambda: lat.uniform(0.4, 1.0))
        pipe, bus = build_pipe(loop, {"sarvam": sar, "local": ScriptedSTT(tl, "local", 5.0)})
        t0 = await run(loop, pipe, tl)
        return t0, pipe, bus

    t0, pipe, bus = run_virtual(main)
    pii_cov, dig_cov, over = measure(tl, spans_of(bus), t0)
    lat = sorted(pipe.latencies)
    p95 = lat[int(0.95 * (len(lat) - 1))]
    print(
        f"G3 apis-up: pii={pii_cov:.3f} digits={dig_cov:.3f} overmute={over:.3f} p95={p95:.2f}s late={pipe.stats['late_spans']}"
    )
    assert pii_cov >= 0.90 and dig_cov >= 0.95
    assert over <= 0.15
    assert p95 <= 3.3
    assert pipe.stats["failsafe"] == 0


def test_g3_api_down_mutes_all_voiced_speech_and_local_covers_pii():
    tl = scenario()
    err = SttProviderError("down", 500)

    async def down(loop):
        pipe, bus = build_pipe(
            loop,
            {
                "sarvam": ScriptedSTT(tl, fail=err, latency=0.1),
                "local": ScriptedSTT(tl, "local", 0.1, fail=err),
            },
        )
        t0 = await run(loop, pipe, tl)
        return t0, pipe, bus

    t0, pipe, bus = run_virtual(down)
    cov = [(s.pts_start, s.pts_end) for s in spans_of(bus)]
    speech = [w for w in tl.words]
    assert all(
        intervals_cover(cov, w.t0, w.t1) >= (w.t1 - w.t0) - 1e-6 for w in speech
    )  # 100 % of voiced speech
    assert all(s.granularity == "failsafe" for s in spans_of(bus))

    async def local_only(loop):
        loc = ScriptedSTT(tl, "local", 0.9, dropout=0.15, seed=5)  # lossy local recogniser (stub)
        pipe, bus = build_pipe(loop, {"sarvam": ScriptedSTT(tl, fail=err, latency=0.1), "local": loc})
        t0 = await run(loop, pipe, tl)
        return t0, pipe, bus

    t0, pipe, bus = run_virtual(local_only)
    pii_cov, _, _ = measure(tl, spans_of(bus), t0)
    print(f"G3 local-only (15 % word loss stub): pii={pii_cov:.3f}")
    assert pii_cov >= 0.70


def test_g3_mouth_blur_request_for_every_muted_span():
    tl = scenario()

    async def main(loop):
        pipe, bus = build_pipe(loop, {"sarvam": ScriptedSTT(tl, "sarvam", 0.5)})
        await run(loop, pipe, tl)
        return bus

    bus = run_virtual(main)
    spans = spans_of(bus)
    masks = [e for t, e in bus.history if t is Topic.MASK_REQUEST]
    assert spans and len(masks) == len(spans)
    assert all(
        (m.pts_start, m.pts_end) == (s.pts_start, s.pts_end) for s, m in zip(spans, masks, strict=True)
    )


def test_host_thread_smoke_feeds_without_blocking_and_reaches_a_verdict():
    """Real Silero VAD on the benign TTS clip through the real host thread: no API keys, so the verdict is the
    local engine's transcript (if its weights exist) or the fail-safe mute (if not). Either way speech is not lost."""
    import soundfile as sf

    pcm, rate = sf.read(str(Path(__file__).parent / "data" / "tts_two_sentences.wav"), dtype="int16")
    assert rate == 16000
    host = build(Config(delay_seconds=3.0), InProcessBus())
    bus = host.bus
    host.start()
    try:
        t0 = time.monotonic()
        for i in range(0, len(pcm) - 320, 320):
            host.feed(AudioChunk(i // 320, i / 16000, 16000, pcm[i : i + 320], time.monotonic()))
            time.sleep(0.001)
        assert time.monotonic() - t0 < 3.0  # feed never blocks
        deadline = time.monotonic() + 9.0
        while time.monotonic() < deadline and not any(
            t in (Topic.MUTE_SPAN, Topic.TRANSCRIPT) for t, _ in bus.history
        ):
            time.sleep(0.05)
    finally:
        host.close()
    got = [(t, e) for t, e in bus.history if t in (Topic.MUTE_SPAN, Topic.TRANSCRIPT)]
    assert got
    assert all(isinstance(e, MuteSpan) for t, e in got if t is Topic.MUTE_SPAN)
