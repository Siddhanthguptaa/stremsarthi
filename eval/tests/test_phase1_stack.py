"""Chain plumbing that needs no processes: engine environment, URLs, preflight, soak maths (T1.4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval import phase1
from eval.phase1 import preflight_problems, rss_growth_pct
from eval.stack import ChainSpec, EnvError, _real_engine_env, _sender_url

PORTS = {"rtmp": 41935, "srt": 41890, "rtsp": 41554, "api": 41997}
CREDS = ("evalpub", "s3cret-token")


def _spec(**kw):
    return ChainSpec(Path("/tmp/x"), "card_static:10", 10.0, **kw)


def test_engine_env_never_streams_to_a_platform_during_a_measurement(monkeypatch):
    monkeypatch.setenv("YOUTUBE_STREAM_KEY", "must-not-reach-the-engine")
    monkeypatch.setenv("TWITCH_STREAM_KEY", "nor-this")
    env = _real_engine_env(_spec(), PORTS, CREDS, Path("/tmp/t.jsonl"), Path("/tmp/f.jsonl"))
    assert "YOUTUBE_STREAM_KEY" not in env and "TWITCH_STREAM_KEY" not in env
    assert json.loads(env["DESTINATIONS"]) == [{"kind": "mediamtx_tap", "enabled": True}]
    assert env["GPU__ENABLED"] == "false" and env["GPU__ORT_PROVIDER"] == "cpu"
    assert env["INGEST_PORT_RTMP"] == "41935" and env["INGEST_PORT_SRT"] == "41890"
    assert env["ENGINE__TIMING_LOG_PATH"] == "/tmp/t.jsonl" and env["SS_FAULT_LOG"] == "/tmp/f.jsonl"
    assert "SS_FAULT" not in env


def test_engine_env_gpu_fault_and_delay(monkeypatch):
    monkeypatch.setenv("SS_FAULT", "stale-value-from-the-shell")
    env = _real_engine_env(
        _spec(gpu=True, fault="lane:accurate:hang@t=20s", delay_s=3.5), PORTS, CREDS, Path("/t"), Path("/f")
    )
    assert env["GPU__ENABLED"] == "true" and env["GPU__ORT_PROVIDER"] == "cuda"
    assert env["SS_FAULT"] == "lane:accurate:hang@t=20s"  # the leg's fault replaces whatever the shell had
    assert env["DELAY_SECONDS"] == "3.5"
    clean = _real_engine_env(_spec(), PORTS, CREDS, Path("/t"), Path("/f"))
    assert "SS_FAULT" not in clean


def test_live_youtube_needs_the_key_in_the_environment(monkeypatch):
    monkeypatch.delenv("YOUTUBE_STREAM_KEY", raising=False)
    with pytest.raises(EnvError) as err:
        _real_engine_env(_spec(live_youtube=True), PORTS, CREDS, Path("/t"), Path("/f"))
    assert "YOUTUBE_STREAM_KEY" in str(err.value)
    monkeypatch.setenv("YOUTUBE_STREAM_KEY", "abc-123")
    env = _real_engine_env(_spec(live_youtube=True), PORTS, CREDS, Path("/t"), Path("/f"))
    assert (
        env["YOUTUBE_STREAM_KEY"] == "abc-123" and "DESTINATIONS" not in env
    )  # config default: youtube + tap


def test_sender_urls():
    srt = _sender_url("srt", PORTS, CREDS, "camera")
    assert srt.startswith("srt://127.0.0.1:41890?streamid=publish:camera:evalpub:s3cret-token")
    assert "mode=caller" in srt
    rtmp = _sender_url("rtmp", PORTS, CREDS, "camera")
    assert rtmp == "rtmp://127.0.0.1:41935/camera?user=evalpub&pass=s3cret-token"
    assert _sender_url("rtmp", PORTS, None, "camera") == "rtmp://127.0.0.1:41935/camera"
    assert _sender_url("srt", PORTS, None, "camera").startswith(
        "srt://127.0.0.1:41890?streamid=publish:camera&"
    )


def test_preflight_reports_each_missing_piece(tmp_path, monkeypatch):
    monkeypatch.setattr(phase1, "find_mediamtx", lambda: None)
    monkeypatch.setattr(phase1.shutil, "which", lambda _n: None)
    problems = preflight_problems("ref", tmp_path)
    assert any("ffmpeg" in p for p in problems) and any("MediaMTX" in p for p in problems)
    real = preflight_problems("real", tmp_path)
    assert any("engine/main.py is missing" in p for p in real)
    assert not any("engine/main.py" in p for p in problems)  # the ref chain needs no engine


def test_preflight_names_the_missing_relay_egress(tmp_path, monkeypatch):
    monkeypatch.setattr(phase1, "find_mediamtx", lambda: "/bin/true")
    monkeypatch.setattr(phase1.shutil, "which", lambda _n: "/bin/true")
    for rel in ("engine/main.py", "vision/host.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("", encoding="utf-8")
    (tmp_path / "relay").mkdir()
    runtime = tmp_path / "relay" / "runtime.py"
    runtime.write_text("return RelayEndpoints(source=s, sink=NullFrameSink())", encoding="utf-8")
    problems = [p for p in preflight_problems("real", tmp_path) if "relay" in p]
    assert len(problems) == 1 and "T1.2" in problems[0] and "shielded" in problems[0]
    runtime.write_text("sink = EgressSink(config)", encoding="utf-8")
    assert not [p for p in preflight_problems("real", tmp_path) if "relay" in p]


def test_rss_growth():
    flat = [(float(i * 5), 1000.0, 40) for i in range(60)]
    g = rss_growth_pct(flat)
    assert g["growth_pct"] == 0.0 and g["threads_first"] == g["threads_last"] == 40.0
    leak = [(float(i * 5), 1000.0 + i * 5.0, 40 + i // 10) for i in range(60)]
    g = rss_growth_pct(leak)
    assert 15.0 < g["growth_pct"] < 25.0 and g["threads_last"] > g["threads_first"]
    assert rss_growth_pct(flat[:3])["growth_pct"] is None
    # a warm-up ramp (first minute) is not counted as growth
    ramp = [(float(i * 5), 400.0 + min(i, 12) * 50.0, 40) for i in range(60)]
    assert abs(rss_growth_pct(ramp)["growth_pct"]) < 1.0


def test_private_mediamtx_config_turns_off_every_unused_listener():
    # a default MediaMTX also binds MoQ/WebRTC/HLS ports, which collide when several runs share the host
    from eval.harness.procs import _CONFIG

    for key in ("api", "metrics", "pprof", "hls", "webrtc", "moq"):
        assert f"\n{key}: false\n" in _CONFIG


def test_a_mediamtx_that_cannot_start_is_an_environment_error(monkeypatch, tmp_path):
    from eval import stack

    class Broken:
        def __init__(self, run_dir):
            pass

        def __enter__(self):
            raise RuntimeError("MediaMTX did not start; see mediamtx.log")

    monkeypatch.setattr(stack, "MediaMtx", Broken)
    monkeypatch.setattr(stack, "find_mediamtx", lambda: Path("/bin/true"))
    with pytest.raises(EnvError, match="did not start"):
        stack.run_chain(ChainSpec(tmp_path, "card_static:10", 10.0, engine="ref"))
