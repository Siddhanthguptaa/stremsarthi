"""Mute span alignment: the number plus padding must be muted even when the STT only has segment-level times.

Word-level: a perfect recogniser with exact word times. Coarse: the words are interpolated across the segment
(``word_times=False``, what Sarvam and a Whisper run without word timestamps give), so the true digit times are
only known to the segment.
"""

from __future__ import annotations

import asyncio

import pytest

from audio.orchestrator.pipeline import AudioPipeline
from audio.orchestrator.race import SttRace
from audio.pii.detector import SpokenPiiDetector
from audio.pii.rules import SpokenRules
from audio.segmenter import Segmenter
from audio.testing import ScriptedSTT, Timeline, feed_realtime, run_virtual
from audio.vad import EnergyFrameModel, StreamingVad
from contracts.config import Config
from contracts.enums import Topic
from contracts.fakes import InProcessBus

D = 4.0
DIGITS = "nau aath saat chhe paanch char teen do ek shunya".split()
MIN_PAD_S = 0.3  # required lead and trail around the spoken number


def timeline(lead_words: str) -> Timeline:
    tl = Timeline()
    tl.say("aaj ka stream shuru karte hain", pause_after=0.8)
    tl.say(lead_words + " " + " ".join(DIGITS), pause_after=0.0)
    for w in tl.words:
        w.pii = w.digit = w.text in DIGITS
    tl.pause(1.0).say("dhanyavaad doston bahut maza aaya aaj", pause_after=0.5)
    return tl


def run_case(tl: Timeline, word_times: bool, pad_s: float | None = None):
    async def main(loop):
        kw = {} if pad_s is None else {"audio": {"pad_s": pad_s}}
        cfg = Config(delay_seconds=D, **kw)
        bus = InProcessBus()
        seg = Segmenter(StreamingVad(EnergyFrameModel(), cfg.audio.vad), overlap_s=cfg.audio.overlap_s)
        stt = ScriptedSTT(tl, "local", 0.3, word_times=word_times)
        race = SttRace({"local": stt}, order=("local",), clock=loop.time)
        pipe = AudioPipeline(cfg, bus, seg, race, SpokenPiiDetector(SpokenRules()), clock=loop.time)
        await feed_realtime(pipe, tl.render(), pts0=0.0)
        await asyncio.sleep(D + 1)
        await pipe.drain()
        return pipe, bus

    pipe, bus = run_virtual(main)
    spans = sorted((e.pts_start, e.pts_end) for t, e in bus.history if t is Topic.MUTE_SPAN)
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return pipe, merged


def pads(tl: Timeline, merged) -> tuple[float, float, float]:
    """(lead pad, trail pad, fraction of the number muted) around the spoken digits."""
    digs = [w for w in tl.words if w.digit]
    t0, t1 = digs[0].t0, digs[-1].t1
    hit = [m for m in merged if m[0] <= t1 and m[1] >= t0]
    assert hit, "number not muted"
    lo, hi = min(m[0] for m in hit), max(m[1] for m in hit)
    covered = sum(max(0.0, min(b, t1) - max(a, t0)) for a, b in merged) / (t1 - t0)
    return t0 - lo, hi - t1, covered


@pytest.mark.parametrize("word_times", [True, False], ids=["word_level", "segment_level_only"])
@pytest.mark.parametrize("lead", ["mera number hai", "meri phone ki baat ye hai ki mera number hai"])
def test_number_is_muted_with_at_least_0_3s_padding(word_times, lead):
    tl = timeline(lead)
    _, merged = run_case(tl, word_times)
    lead_pad, trail_pad, covered = pads(tl, merged)
    assert covered == pytest.approx(1.0, abs=1e-6)
    assert lead_pad >= MIN_PAD_S - 1e-6, (lead_pad, trail_pad)
    assert trail_pad >= MIN_PAD_S - 1e-6, (lead_pad, trail_pad)


def test_number_that_starts_the_utterance_has_no_audio_before_it_to_pad():
    """Nothing to mute before the first word of the stream; the trail still gets its padding."""
    tl = Timeline()
    tl.say(" ".join(DIGITS), pause_after=1.0)
    for w in tl.words:
        w.pii = w.digit = True
    tl.say("theek hai bas itna hi tha", pause_after=0.5)
    _, merged = run_case(tl, word_times=False)
    _, trail_pad, covered = pads(tl, merged)
    assert covered == pytest.approx(1.0, abs=1e-6) and trail_pad >= MIN_PAD_S - 1e-6
