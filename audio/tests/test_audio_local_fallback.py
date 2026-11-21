"""No-key / bad-network path: best available local weight, no API client without keys, timeout fallback, slow local
speech-to-text mutes (never leaks). No real network, no real model (fake WhisperModel)."""

from __future__ import annotations

import asyncio
import logging
import sys
import types
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from audio.orchestrator import make_providers
from audio.orchestrator.pipeline import AudioPipeline
from audio.orchestrator.race import SttAllFailed, SttRace
from audio.pii.detector import SpokenPiiDetector, build_detector
from audio.pii.rules import SpokenRules
from audio.segmenter import Segment, Segmenter
from audio.stt.base import SttUnavailable
from audio.stt.local import LocalWhisperSTT, candidate_sizes, resolve_model_size
from audio.stt.sarvam import SarvamSTT
from audio.testing import ScriptedSTT, Timeline, feed_realtime, intervals_cover, run_virtual
from audio.vad import EnergyFrameModel, StreamingVad
from contracts.config import Config
from contracts.enums import Category, Topic
from contracts.fakes import InProcessBus

SR = 16_000


def tone(seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (np.sin(2 * np.pi * 220 * t) * 12000).astype(np.int16)


def make_weights(root, *sizes):
    for s in sizes:
        d = root / f"faster-whisper-{s}"
        d.mkdir(parents=True)
        for f in ("model.bin", "config.json", "tokenizer.json"):
            (d / f).write_bytes(b"x")


@pytest.fixture
def weights(tmp_path, monkeypatch):
    monkeypatch.setenv("SS_AUDIO_WEIGHTS", str(tmp_path))
    return tmp_path


def fake_faster_whisper(monkeypatch, loaded: list, broken: set[str] = frozenset()):
    class WhisperModel:
        def __init__(self, path, **kw):
            name = path.rsplit("faster-whisper-", 1)[-1]
            if name in broken:
                raise RuntimeError("corrupt")
            loaded.append(name)

        def transcribe(self, audio, **kw):
            seg = SimpleNamespace(
                text=" ek do", start=0.0, end=0.5, words=None, no_speech_prob=0.0, avg_logprob=-0.2
            )
            return iter([seg]), SimpleNamespace(language="hi")

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)


# ---------------------------------------------------------------- best available weight
def test_candidate_order_prefers_smaller_then_larger():
    assert candidate_sizes("small") == ["small", "base", "tiny"]
    assert candidate_sizes("base") == ["base", "tiny", "small"]
    assert candidate_sizes("tiny") == ["tiny", "base", "small"]
    assert candidate_sizes("large") == ["base", "tiny", "small"]  # unknown name: still a sane order


def test_resolve_picks_the_best_available(weights):
    make_weights(weights, "tiny", "base")
    assert resolve_model_size("small") == "base"
    assert resolve_model_size("base") == "base"
    assert resolve_model_size("tiny") == "tiny"
    (weights / "faster-whisper-base" / "model.bin").unlink()  # half-downloaded: not available
    assert resolve_model_size("small") == "tiny"


def test_resolve_with_no_weights_is_none_not_a_crash(weights):
    assert resolve_model_size("base") is None


async def test_demo_profile_small_falls_back_to_base_and_logs_it(weights, monkeypatch, caplog):
    make_weights(weights, "tiny", "base")
    loaded: list[str] = []
    fake_faster_whisper(monkeypatch, loaded)
    caplog.set_level(logging.INFO, logger="audio.stt.local")
    stt = LocalWhisperSTT("small")
    tr = await stt.transcribe(tone(1.0), 0.0, "hi")
    await stt.aclose()
    assert loaded == ["base"] and stt.active_model_size == "base" and tr.text == "ek do"
    msgs = [r.getMessage() for r in caplog.records]
    assert any("small is not available, using base" in m for m in msgs)


async def test_corrupt_weight_falls_through_to_the_next_one(weights, monkeypatch):
    make_weights(weights, "tiny", "base")
    loaded: list[str] = []
    fake_faster_whisper(monkeypatch, loaded, broken={"base"})
    stt = LocalWhisperSTT("base")
    await stt.transcribe(tone(1.0), 0.0, "hi")
    await stt.aclose()
    assert loaded == ["tiny"] and stt.active_model_size == "tiny"


async def test_no_weights_raises_unavailable_and_the_race_mutes(weights, monkeypatch):
    fake_faster_whisper(monkeypatch, [])
    stt = LocalWhisperSTT("base")
    with pytest.raises(SttUnavailable):
        await stt.transcribe(tone(1.0), 0.0, "hi")
    race = SttRace({"local": stt}, order=("local",))
    seg = Segment(1, 0.0, 1.0, tone(1.0), 0.0, True, 1000.0, False, "pause")
    with pytest.raises(SttAllFailed):  # the pipeline turns this into a fail-safe mute
        await race.transcribe(seg, "hi")
    await stt.aclose()


# ---------------------------------------------------------------- keys unset: no API client is ever built
def test_without_keys_only_local_is_built_and_no_http_client_exists(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)

    def boom(*a, **k):
        raise AssertionError("an HTTP client was constructed without an API key")

    monkeypatch.setattr(httpx, "AsyncClient", boom)
    monkeypatch.setattr(httpx, "Client", boom)
    cfg = Config(_env_file=None)
    providers = make_providers(cfg)
    assert list(providers) == ["local"]
    det = build_detector(cfg)
    assert det.llm is None  # rules only: no Groq backend object
    race = SttRace(providers, order=cfg.audio.stt_order, hedge_s=cfg.audio.race_hedge_s)
    assert race.order == ["local"] and race.failover == []


async def test_without_keys_race_uses_local_and_logs_no_errors(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    cfg = Config(_env_file=None)
    loc = ScriptedSTT(Timeline().say("ek do teen"), "local", 0.0)
    providers = {**make_providers(cfg), "local": loc}
    race = SttRace(providers, order=cfg.audio.stt_order)
    seg = Segment(1, 0.0, 1.0, tone(1.0), 0.0, True, 1000.0, False, "pause")
    for _ in range(3):
        await race.transcribe(seg, "hi")
    assert race.errors == {"local": 0} and race.wins["local"] == 3
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ---------------------------------------------------------------- keys set, network failing
def sarvam_with(handler) -> SarvamSTT:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SarvamSTT("test-key", client=client, timeout_s=0.3)


@pytest.mark.parametrize("failure", ["connect", "timeout", "http500", "hang"])
async def test_network_failure_falls_back_to_local_inside_the_budget(failure):
    async def handler(request: httpx.Request) -> httpx.Response:
        if failure == "connect":
            raise httpx.ConnectError("down")
        if failure == "timeout":
            raise httpx.ReadTimeout("slow")
        if failure == "hang":
            await asyncio.sleep(5)
        return httpx.Response(500)

    sarvam = sarvam_with(handler)
    loc = ScriptedSTT(Timeline().say("nau aath saat"), "local", 0.0)
    race = SttRace(
        {"sarvam": sarvam, "local": loc}, order=("sarvam", "local"), hedge_s=0.1, timeouts={"sarvam": 0.3}
    )
    seg = Segment(1, 0.0, 1.0, tone(1.0), 0.0, True, 1000.0, False, "pause")
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    tr = await race.transcribe(seg, "hi")
    assert tr.provider == "local"
    assert loop.time() - t0 < 1.0  # well inside the 3.7 s soft deadline
    await sarvam.aclose()


# ---------------------------------------------------------------- slow local STT: MUTE, never leak
def test_local_stt_slower_than_its_timeout_mutes_all_voiced_speech():
    import time

    class SlowModel:
        def transcribe(self, audio, **kw):
            time.sleep(0.2)  # real thread time; the virtual loop times out first
            return iter([]), SimpleNamespace(language="hi")

    tl = Timeline().say("mera number hai nau aath saat chhe paanch char teen do ek", pause_after=1.0)

    async def main(loop):
        cfg = Config(delay_seconds=4.0)
        bus = InProcessBus()
        seg = Segmenter(StreamingVad(EnergyFrameModel(), cfg.audio.vad), overlap_s=cfg.audio.overlap_s)
        local = LocalWhisperSTT(model_factory=lambda: SlowModel(), timeout_s=1.5)
        race = SttRace({"local": local}, order=("local",), clock=loop.time)
        pipe = AudioPipeline(cfg, bus, seg, race, SpokenPiiDetector(SpokenRules()), clock=loop.time)
        await feed_realtime(pipe, tl.render(), pts0=0.0)
        await asyncio.sleep(5.0)
        await pipe.drain()
        await local.aclose()
        return pipe, bus

    pipe, bus = run_virtual(main)
    spans = [e for t, e in bus.history if t is Topic.MUTE_SPAN]
    end = tl.words[-1].t1
    assert spans and all(s.category is Category.SPOKEN_UNVERIFIED for s in spans)
    assert intervals_cover([(s.pts_start, s.pts_end) for s in spans], 0.0, end) == pytest.approx(
        end, abs=1e-6
    )
    assert pipe.stats["late_spans"] == 0
