"""Sender -> file -> reader (offline), and the MediaMTX loopback (skipped without the binary)."""

import numpy as np
import pytest

from eval.harness.procs import find_mediamtx
from eval.sender import sender
from eval.sender.reader import read_stream


def test_sender_file_roundtrip_counters_stamps_beeps(tmp_path):
    out = tmp_path / "s.ts"
    truth = tmp_path / "truth.jsonl"
    stats = sender.run_sender(
        str(out), "card_static:2,card_moving:2,card_absent:2", realtime=False, truth_path=truth
    )
    assert stats["frames_sent"] == 180
    res = read_stream(str(out), seconds=60, open_timeout_s=3)
    s = res.summary()
    assert s["error"] is None and s["stamp_unreadable"] == 0
    assert [f.seq for f in res.frames] == list(range(180))
    assert s["received_fraction"] == 1.0 and s["capture_gaps_over_100ms"] == 0
    # beeps: 6 s of audio gives onsets at 0..5 s, matching the video flag frames
    assert s["beeps_audio"] == 6 and s["beeps_video"] == 6
    assert s["av_offset_ms"]["max_abs_ms"] < 5.0
    # ground truth file has one line per frame plus the header, no pixels or text
    lines = truth.read_text().splitlines()
    assert len(lines) == 181 and '"type":"header"' not in lines[1]


def test_sender_truth_targets_follow_scene_script(tmp_path):
    import json

    truth = tmp_path / "t.jsonl"
    sender.run_sender(str(tmp_path / "x.ts"), "card_absent:1,card_static:1", realtime=False, truth_path=truth)
    frames = [json.loads(line) for line in truth.read_text().splitlines()[1:]]
    assert frames[0]["targets"] == [] and frames[0]["scene"] == "card_absent"
    assert frames[45]["scene"] == "card_static" and frames[45]["targets"][0]["category"] == "aadhaar"
    assert [f["beep"] for f in frames if f["beep"]] and sum(f["beep"] for f in frames) == 2


def test_sender_stream_is_decodable_with_known_audio(tmp_path):
    import av

    out = tmp_path / "s.mp4"
    sender.run_sender(str(out), "idle:2", realtime=False)
    with av.open(str(out)) as c:
        v, a = c.streams.video[0], c.streams.audio[0]
        assert (v.width, v.height) == (1280, 720) and int(v.average_rate) == 30
        assert a.rate == 48000 and a.layout.name == "mono"
        pcm = np.concatenate([f.to_ndarray().reshape(-1) for f in c.decode(audio=0)])
    assert 1.9 < len(pcm) / 48000 < 2.2 and np.abs(pcm).max() > 0.2


needs_mtx = pytest.mark.skipif(find_mediamtx() is None, reason="MediaMTX binary not found (.tmp/tools)")


@needs_mtx
@pytest.mark.slow
@pytest.mark.parametrize("transport", ["srt", "rtmp"])
def test_loopback_through_mediamtx(tmp_path, transport):
    from eval.loopback import run_loopback

    out = run_loopback(10.0, transport, tmp_path / "run", tap="rtmp")
    assert out["passed"], out["checks"]
    r = out["reader"]
    assert r["missing_frames"] == 0 and r["delay"]["p95_s"] < 1.5
    assert r["av_offset_ms"]["max_abs_ms"] is not None and r["av_offset_ms"]["max_abs_ms"] < 40.0


@needs_mtx
@pytest.mark.slow
def test_ffprobe_receives_stream_from_mediamtx(tmp_path):
    from eval.loopback import ffprobe_check

    out = ffprobe_check("srt", tmp_path / "ffp")
    assert out["passed"], out
