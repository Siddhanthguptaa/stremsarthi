"""Encoder unit tests: command construction, selection, audio alignment, backoff, and one real worker.

Everything but the last block uses a fake process, so it needs no GPU and no ffmpeg."""

from __future__ import annotations

import subprocess
import time
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import SecretStr

from contracts.config import Config
from relay.destinations import build_destinations
from relay.encoder import (
    CODEC_NVENC,
    CODEC_X264,
    KIND_AUDIO,
    KIND_VIDEO,
    AudioAligner,
    EncodedPacket,
    Encoder,
    EncoderError,
    EncoderProcess,
    EncoderSettings,
    describe_command,
    nvenc_available,
    select_encoder,
    video_options,
)

KEY = "abcd-efgh-ijkl-mnop-qrst"


# ------------------------------------------------------------------ command construction
def test_x264_defaults_follow_the_spec():
    cfg = Config(_env_file=None)
    settings = EncoderSettings.from_config(cfg)
    assert (settings.width, settings.height, settings.fps) == (1280, 720, 30)
    assert settings.codec == CODEC_X264
    assert settings.gop == 60  # keyframe_seconds (2.0) x fps (30)
    options = video_options(settings)
    assert options["preset"] == "veryfast" and options["tune"] == "zerolatency"
    assert options["maxrate"] == "4500k" and options["bufsize"] == "9000k"
    params = dict(p.split("=") for p in options["x264-params"].split(":"))
    assert params["keyint"] == "60" and params["min-keyint"] == "60"
    assert params["scenecut"] == "0" and params["bframes"] == "0"


def test_gop_tracks_config_fps_and_keyframe_seconds():
    cfg = Config(_env_file=None, fps=25, relay={"keyframe_seconds": 1.5, "video_bitrate_kbps": 3000})
    settings = EncoderSettings.from_config(cfg)
    assert settings.gop == round(1.5 * 25)
    assert video_options(settings)["maxrate"] == "3000k"
    assert f"keyint={settings.gop}" in video_options(settings)["x264-params"]


def test_nvenc_options_use_cbr_and_forced_idr():
    options = video_options(EncoderSettings(codec=CODEC_NVENC))
    assert options["preset"] == "p4" and options["rc"] == "cbr" and options["forced-idr"] == "1"
    assert options["maxrate"] == "4500k"


def test_settings_reject_bad_values():
    for bad in ({"codec": "mpeg2"}, {"width": 1281}, {"fps": 0}, {"keyframe_seconds": 0.0}):
        with pytest.raises(ValueError):
            EncoderSettings(**bad)


def test_describe_command_shows_keyframes_bitrate_and_audio_but_no_key():
    settings = EncoderSettings()
    text = describe_command(settings, ["youtube", "twitch", "mediamtx_tap"])
    for needle in (
        "-g 60",
        "-bf 0",
        "-b:v 4500k",
        "-maxrate 4500k",
        "-bufsize 9000k",
        "-c:a aac",
        "-b:a 128k",
        "-ar 48000",
        "-ac 2",
    ):
        assert needle in text
    assert text.count("-f flv") == 3  # one encode feeds three muxers
    assert KEY not in text and "<redacted:youtube>" in text


def test_destination_repr_and_str_never_show_the_key():
    cfg = Config(_env_file=None, youtube_stream_key=SecretStr(KEY))
    built = build_destinations(cfg, tap_url=None, environ={})
    spec = next(s for s in built.specs if s.kind == "youtube")
    assert KEY not in repr(spec) and KEY not in str(spec) and KEY not in repr(built)
    assert spec.url(4321) == f"rtmp://127.0.0.1:4321/live2/{KEY}"  # only the connect-time URL has it


# ------------------------------------------------------------------------------ selection
def test_select_encoder_matrix():
    yes, no = (lambda: True), (lambda: False)
    assert select_encoder("x264", True, probe=yes) == (CODEC_X264, "configured")
    assert select_encoder("nvenc", False, probe=yes)[0] == CODEC_NVENC
    assert select_encoder("nvenc", True, probe=no) == (CODEC_X264, "nvenc_unavailable")
    assert select_encoder("auto", True, probe=yes)[0] == CODEC_NVENC
    assert select_encoder("auto", False, probe=yes)[0] == CODEC_X264  # gpu.enabled is off: never NVENC
    assert select_encoder("auto", True, probe=no) == (CODEC_X264, "nvenc_unavailable")


def _runner(listing: str, rc: int, calls: list):
    def run(cmd, **kwargs):
        calls.append(list(cmd))
        return SimpleNamespace(stdout=listing, returncode=rc)

    return run


def test_nvenc_probe_needs_listing_and_a_working_test_encode(monkeypatch):
    monkeypatch.delenv("STREAMSARTHI_GPU_LOCK_HELD", raising=False)
    monkeypatch.setattr("relay.encoder.shutil.which", lambda name: "/usr/bin/flock")
    calls: list = []
    assert nvenc_available(runner=_runner("libx264 only", 0, calls)) is False
    assert len(calls) == 1  # no GPU run when the encoder is not even listed
    calls.clear()
    assert nvenc_available(runner=_runner("V....D h264_nvenc", 1, calls)) is False
    assert nvenc_available(runner=_runner("V....D h264_nvenc", 0, calls)) is True
    test_cmd = calls[-1]
    assert test_cmd[0] == "flock" and "/tmp/streamsarthi-gpu.lock" in test_cmd  # the GPU run takes the lock


def test_nvenc_probe_does_not_relock_when_the_caller_holds_the_gpu_lock(monkeypatch):
    monkeypatch.setenv("STREAMSARTHI_GPU_LOCK_HELD", "1")
    calls: list = []
    assert nvenc_available(runner=_runner("h264_nvenc", 0, calls)) is True
    assert calls[-1][0] == "ffmpeg"


def test_nvenc_probe_handles_a_missing_ffmpeg():
    def boom(*args, **kwargs):
        raise FileNotFoundError

    assert nvenc_available(runner=boom) is False
    assert (
        nvenc_available(runner=lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("x", 1)))
        is False
    )


# -------------------------------------------------------------------------- audio on the PTS clock
def _chunk(n=1600, value=1):
    return np.full(n, value, np.int16)


def test_aligner_passes_contiguous_audio_untouched():
    aligner = AudioAligner()
    start, first = aligner.place(10.0, _chunk())
    assert start == 0 and len(first) == 1600
    start, second = aligner.place(10.0 + 1 / 30, _chunk())
    assert start == 1600 and len(second) == 1600
    assert aligner.padded_samples == 0 and aligner.trimmed_samples == 0


def test_aligner_fills_a_skipped_frame_with_silence():
    aligner = AudioAligner()
    aligner.place(0.0, _chunk())
    start, samples = aligner.place(2 / 30, _chunk(value=7))  # frame 1 was skipped: 1600 samples missing
    assert start == 1600 and len(samples) == 3200
    assert not samples[:1600].any() and (samples[1600:] == 7).all()
    assert aligner.padded_samples == 1600


def test_aligner_trims_an_overlap_and_ignores_jitter():
    aligner = AudioAligner()
    aligner.place(0.0, _chunk())
    start, samples = aligner.place(1 / 30 - 0.005, _chunk())  # 5 ms early: below the 20 ms tolerance
    assert start == 1600 and len(samples) == 1600
    start, samples = aligner.place(2 / 30 - 0.05, _chunk())  # 50 ms early: overlap trimmed
    assert start == 3200 and len(samples) < 1600 and aligner.trimmed_samples > 0


def test_aligner_gives_up_on_a_huge_gap():
    aligner = AudioAligner(max_gap_s=1.0)
    aligner.place(0.0, _chunk())
    with pytest.raises(EncoderError, match="timeline-gap"):
        aligner.place(5.0, _chunk())


# ------------------------------------------------------------- encoder restart with a fake process
class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Script:
    """Controls every fake process: how many starts fail, whether the next frame fails."""

    def __init__(self):
        self.start_failures = 0
        self.starts: list[str] = []
        self.processes: list[FakeProcess] = []
        self.nvenc_fails = False


class FakeProcess:
    def __init__(self, settings, script: Script):
        self.settings, self.script = settings, script
        self.alive = False
        self.pid = 1000 + len(script.processes)
        self.video_extradata = b"\x00\x00\x00\x01"
        self.fail_next = False
        self.indexes: list[int] = []
        self.audio: list[list[tuple[int, int]]] = []
        script.processes.append(self)

    def start(self):
        self.script.starts.append(self.settings.codec)
        if self.settings.codec == CODEC_NVENC and self.script.nvenc_fails:
            raise EncoderError("encoder-startup", "ValueError")
        if self.script.start_failures:
            self.script.start_failures -= 1
            raise EncoderError("encoder-startup")
        self.alive = True
        return b"FLVHEADER"

    def encode(self, index, image, items):
        if self.fail_next or not self.alive:
            self.alive = False
            raise EncoderError("encoder-exited")
        self.indexes.append(index)
        self.audio.append([(start, len(samples)) for start, samples in items])
        return [EncodedPacket(KIND_VIDEO, b"v", index, index, 1, index % 60 == 0)]

    def stop(self):
        self.alive = False

    def kill(self):
        self.fail_next = True


def make_encoder(script: Script, clock: Clock, codec=CODEC_X264, **kw) -> Encoder:
    return Encoder(
        EncoderSettings(codec=codec, width=16, height=16),
        factory=lambda settings: FakeProcess(settings, script),
        clock=clock,
        jitter=0.0,
        **kw,
    )


IMG = np.zeros((16, 16, 3), np.uint8)


def test_encoder_maps_pts_to_video_index_and_audio_to_the_same_clock():
    script, clock = Script(), Clock()
    encoder = make_encoder(script, clock).start()
    for pts in (50.0, 50.0 + 1 / 30, 50.0 + 3 / 30):  # the last frame skipped one
        result = encoder.encode(pts, IMG, [(pts, _chunk())])
        assert result is not None and result.generation == 1
    process = script.processes[0]
    assert process.indexes == [0, 1, 3]  # video index follows the PTS, so a skipped frame leaves a gap
    assert process.audio[2] == [(1600 * 2, 3200)]  # audio: 1600 samples of silence plus the new chunk


def test_encoder_video_index_is_strictly_increasing():
    script, clock = Script(), Clock()
    encoder = make_encoder(script, clock).start()
    for pts in (1.0, 1.0, 1.0 + 0.001):  # duplicate or near-duplicate PTS must not repeat an index
        encoder.encode(pts, IMG, [])
    assert script.processes[0].indexes == [0, 1, 2]


def test_encoder_backoff_ladder_is_0_5_1_2_4_8_and_stays_at_8():
    script, clock = Script(), Clock()
    encoder = make_encoder(script, clock).start()
    script.processes[0].kill()
    assert encoder.encode(1.0, IMG, []) is None  # the failed frame is dropped, never retried
    assert encoder.healthy is False and encoder.last_error == "encoder-exited"
    script.start_failures = 99
    expected = [0.5, 1.0, 2.0, 4.0, 8.0, 8.0]
    for delay in expected:
        starts = len(script.starts)
        clock.now += delay - 0.01
        assert encoder.encode(1.0, IMG, []) is None
        assert len(script.starts) == starts, "restarted before the backoff elapsed"
        clock.now += 0.01
        assert encoder.encode(1.0, IMG, []) is None  # tries, fails, doubles the delay
        assert len(script.starts) == starts + 1


def test_encoder_recovers_with_a_new_generation_and_a_fresh_timeline():
    script, clock = Script(), Clock()
    events: list[str] = []
    encoder = make_encoder(script, clock, on_event=events.append).start()
    assert encoder.encode(10.0, IMG, [(10.0, _chunk())]).generation == 1
    script.processes[0].kill()
    assert encoder.encode(10.04, IMG, []) is None
    clock.now += 0.6
    result = encoder.encode(11.0, IMG, [(11.0, _chunk())])
    assert result is not None and result.generation == 2 and result.video_index == 0  # new timeline
    assert script.processes[1].audio[0][0][0] == 0
    assert encoder.healthy and encoder.restarts == 1
    assert events == ["encoder_down", "encoder_up"]


def test_nvenc_start_failure_falls_back_to_x264():
    script, clock = Script(), Clock()
    script.nvenc_fails = True
    events: list[str] = []
    encoder = make_encoder(script, clock, codec=CODEC_NVENC, on_event=events.append).start()
    assert script.starts == [CODEC_NVENC, CODEC_X264]
    assert encoder.codec == CODEC_X264 and encoder.fell_back
    assert "encoder_fallback_x264" in events


def test_encoder_with_no_working_codec_raises_at_start():
    script, clock = Script(), Clock()
    script.start_failures = 5
    with pytest.raises(EncoderError):
        make_encoder(script, clock).start()


def test_closed_encoder_refuses_frames():
    script, clock = Script(), Clock()
    encoder = make_encoder(script, clock).start()
    encoder.close()
    assert encoder.healthy is False
    with pytest.raises(EncoderError, match="encoder-closed"):
        encoder.encode(1.0, IMG, [])


# ----------------------------------------------------------------------- one real worker process
SMALL = EncoderSettings(width=320, height=180, fps=30, video_kbps=500, keyframe_seconds=1.0)


def _frame(value=90):
    return np.full((180, 320, 3), value, np.uint8)


def _audio(start=0, n=1600):
    return [(start, (np.sin(np.arange(n) * 0.05) * 3000).astype(np.int16))]


def test_real_worker_encodes_h264_and_aac_with_a_keyframe_on_the_grid():
    process = EncoderProcess(SMALL)
    try:
        header = process.start()
        assert header.startswith(b"FLV") and process.video_extradata.startswith(b"\x00\x00\x00\x01")
        packets = []
        for i in range(61):
            packets += process.encode(i, _frame(40 + i), _audio(i * 1600))
        video = [p for p in packets if p.kind == KIND_VIDEO]
        audio = [p for p in packets if p.kind == KIND_AUDIO]
        assert len(video) == 61 and len(audio) > 60
        assert [p.pts for p in video if p.keyframe] == [0, 30, 60]  # keyint 30 = 1.0 s x 30 fps, exactly
        assert all(p.pts == p.dts for p in video)  # no B-frames
    finally:
        process.stop()
    assert not process.alive
    process.stop()  # idempotent


def test_real_worker_kill_is_reported_with_a_fixed_code_and_restart_works():
    encoder = Encoder(SMALL, backoff=(0.05,), jitter=0.0).start()
    try:
        assert encoder.encode(0.0, _frame(), [(0.0, _audio()[0][1])]) is not None
        first_pid = encoder.pid
        encoder.kill_worker()
        deadline = time.monotonic() + 10
        result = None
        while time.monotonic() < deadline and result is None:
            result = encoder.encode(1.0, _frame(), [(1.0, _audio()[0][1])])
            if result is None:
                assert encoder.last_error in ("encoder-exited", "encoder-write")  # fixed codes, no libav text
                time.sleep(0.06)
        assert result is not None and result.generation == 2
        assert encoder.pid != first_pid and encoder.restarts == 1
    finally:
        encoder.close()


def test_real_worker_reply_deadline_kills_a_stuck_encode():
    process = EncoderProcess(SMALL, reply_deadline=0.0001)
    try:
        process.start()
        with pytest.raises(EncoderError) as error:
            for i in range(20):
                process.encode(i, _frame(), _audio(i * 1600))
        assert error.value.code in ("encoder-write-timeout", "encoder-write")
        assert not process.alive  # the stuck worker was killed, not left running
    finally:
        process.stop()
