"""Egress integration: the real encoder process, the real TLS forwarder and a local RTMPS sink.

The "YouTube" is a second MediaMTX that serves RTMPS with a certificate from a CA created in this test
(the forwarder is pointed at that CA through its ``cafile`` parameter, and a run without the CA proves a
bad certificate is refused). The stream is checked with ffprobe. Synthetic frames only; no raw frame is
kept (one 30-frame loop is reused), so memory stays low. NVENC tests need the GPU and run under the GPU
lock: ``flock /tmp/streamsarthi-gpu.lock env STREAMSARTHI_GPU_LOCK_HELD=1 ... pytest -m gpu``."""

from __future__ import annotations

import datetime
import ipaddress
import json
import resource
import statistics
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import SecretStr

from contracts.config import Config
from contracts.enums import HoldSource, Topic
from contracts.fakes.bus import InProcessBus
from relay.encoder import CODEC_NVENC, cpu_seconds, nvenc_available
from relay.mediamtx import BinaryVerificationError, verified_binary
from relay.runtime import build, build_egress_sink
from relay.security import PATH_SHIELDED
from relay.tests.support_media import free_port, have_ffmpeg, wait_for

KEY = "abcd-efgh-ijkl-mnop-qrst"
REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")


# ------------------------------------------------------------------------------------- the sink
def _write_pki(directory: Path) -> dict[str, Path]:
    def name(common: str) -> x509.Name:
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])

    now = datetime.datetime.now(datetime.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(name("Stream Sarthi test CA"))
        .issuer_name(name("Stream Sarthi test CA"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(name("localhost"))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    paths = {"ca": directory / "ca.pem", "cert": directory / "server.crt", "key": directory / "server.key"}
    paths["ca"].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths["cert"].write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    paths["key"].write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return paths


class RtmpsSink:
    """A MediaMTX that accepts RTMPS publishers on ``rtmps_port`` and serves them back over RTSP."""

    def __init__(self, directory: Path):
        self.pki = _write_pki(directory)
        self.rtmps_port, self.rtsp_port = free_port(), free_port()
        config = directory / "sink.yml"
        config.write_text(
            "\n".join(
                [
                    "logLevel: info",
                    "api: no",
                    "metrics: no",
                    "pprof: no",
                    "playback: no",
                    "hls: no",
                    "webrtc: no",
                    "moq: no",
                    "srt: no",
                    "rtsp: yes",
                    f"rtspAddress: 127.0.0.1:{self.rtsp_port}",
                    "rtspTransports: [tcp]",
                    "rtmp: yes",
                    'rtmpEncryption: "strict"',
                    f"rtmpsAddress: 127.0.0.1:{self.rtmps_port}",
                    f"rtmpServerKey: {self.pki['key']}",
                    f"rtmpServerCert: {self.pki['cert']}",
                    "paths:",
                    "  all_others:",
                ]
            )
        )
        self.log: list[str] = []
        self.process = subprocess.Popen(
            [str(verified_binary()), str(config)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=directory,  # MediaMTX writes auto-generated certificates into its working directory
        )
        threading.Thread(target=self._read, daemon=True).start()
        assert wait_for(lambda: any("RTMPS" in line or "RTMP" in line for line in self.log), 8), self.log

    def _read(self) -> None:
        assert self.process.stdout is not None
        for raw in self.process.stdout:
            self.log.append(raw.decode(errors="replace").rstrip())

    @property
    def cafile(self) -> str:
        return str(self.pki["ca"])

    def rtmps_url(self) -> str:
        return f"rtmps://localhost:{self.rtmps_port}/live2/{KEY}"

    def rtsp_url(self) -> str:
        return f"rtsp://127.0.0.1:{self.rtsp_port}/live2/{KEY}"

    def stop(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(5)
        except subprocess.TimeoutExpired:
            self.process.kill()


@pytest.fixture(scope="module")
def rtmps_sink(tmp_path_factory):
    try:
        verified_binary()
    except BinaryVerificationError as error:
        pytest.skip(f"MediaMTX binary unavailable: {error}")
    sink = RtmpsSink(tmp_path_factory.mktemp("rtmps"))
    try:
        yield sink
    finally:
        sink.stop()


# ------------------------------------------------------------------------------ the source
def make_frames(count: int = 30) -> list[np.ndarray]:
    """A loop of busy-but-realistic synthetic frames (gradient, moving boxes, text, light noise)."""
    rng = np.random.default_rng(7)
    ramp = np.linspace(20, 200, 1280, dtype=np.uint8)
    base = np.dstack([np.tile(ramp, (720, 1))] * 3)
    frames = []
    for i in range(count):
        image = base.copy()
        x = 40 + i * 30
        cv2.rectangle(image, (x, 120), (x + 260, 330), (30, 180, 240), -1)
        cv2.rectangle(image, (1000 - x // 2, 400), (1200 - x // 2, 600), (230, 90, 40), -1)
        cv2.putText(
            image, f"SYNTHETIC SPECIMEN {i:03d}", (60, 520), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 3
        )
        image += rng.integers(0, 6, image.shape, dtype=np.uint8)
        frames.append(image)
    return frames


FRAMES = make_frames()


def run_source(sink, seconds: float, *, start: int = 0, on_frame=None) -> int:
    """Feed the sink at exactly 30 fps with a continuous sine (the audio slice of frame i is 1600 samples)."""
    began = time.perf_counter()
    count = int(seconds * 30)
    for i in range(start, start + count):
        phase = np.arange(i * 1600, (i + 1) * 1600)
        samples = (np.sin(phase * 2 * np.pi * 440 / 48000) * 6000).astype(np.int16)
        sink.write(i / 30, FRAMES[i % len(FRAMES)], samples)
        if on_frame is not None:
            on_frame(i)
        delay = began + (i - start + 1) / 30 - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
    return count


def ffprobe_json(url: str, *, seconds: int = 7, extra: tuple[str, ...] = ()) -> dict[str, Any]:
    command = [
        "ffprobe", "-v", "error", "-of", "json", "-show_streams", "-show_packets",
        "-show_entries", "packet=stream_index,pts_time,dts_time,flags:stream=index,codec_name,codec_type,width,height,"
        "r_frame_rate,avg_frame_rate,pix_fmt,profile,sample_rate,channels,color_space",
        "-read_intervals", f"%+{seconds}", *extra, url,
    ]  # fmt: skip
    done = subprocess.run(command, capture_output=True, text=True, timeout=seconds + 40)
    assert done.returncode == 0, done.stderr[-400:]
    return json.loads(done.stdout)


def keyframe_times(probe: dict[str, Any]) -> list[float]:
    video = next(s["index"] for s in probe["streams"] if s["codec_type"] == "video")
    return [
        float(p["pts_time"]) for p in probe["packets"] if p["stream_index"] == video and "K" in p["flags"]
    ]


def assert_valid_stream(probe: dict[str, Any], *, label: str) -> None:
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    assert video["codec_name"] == "h264", label
    assert (video["width"], video["height"]) == (1280, 720), label
    assert video["pix_fmt"] == "yuv420p" and video["profile"] in ("High", "Main"), label
    numerator, _, denominator = video["r_frame_rate"].partition("/")
    assert int(numerator) / int(denominator or 1) == pytest.approx(30.0, abs=0.5), label
    assert audio["codec_name"] == "aac" and int(audio["sample_rate"]) == 48000, label
    assert int(audio["channels"]) == 2, label
    times = keyframe_times(probe)
    assert len(times) >= 3, f"{label}: expected keyframes in 7 s, saw {times}"
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert all(abs(g - 2.0) <= 0.1 for g in gaps), f"{label}: keyframe gaps {gaps}"


def timing_summary(bus: InProcessBus, stage: str) -> dict[str, float]:
    values = [(t.t_end - t.t_start) * 1000 for t in bus.of(Topic.TIMING) if t.stage == stage]
    if not values:
        return {}
    values.sort()
    return {
        "n": len(values), "median_ms": round(statistics.median(values), 2),
        "p95_ms": round(values[int(0.95 * (len(values) - 1))], 2), "max_ms": round(values[-1], 2),
    }  # fmt: skip


def youtube_config(sink: RtmpsSink, **extra: Any) -> Config:
    return Config(
        _env_file=None,
        youtube_stream_key=SecretStr(KEY),
        destinations=[
            {"kind": "youtube", "url_host": f"localhost:{sink.rtmps_port}"},
            {"kind": "mediamtx_tap"},
        ],
        **extra,
    )


@pytest.fixture
def tap(mtx):
    """The relay's own MediaMTX: its ``shielded`` path is the tap (anonymous loopback read)."""
    return mtx


def tap_url(mtx) -> str:
    return mtx.address.rtmp(PATH_SHIELDED, mtx.internal)


# ------------------------------------------------------------------------------------- the tests
def test_stream_reaches_the_rtmps_sink_and_the_tap_with_the_right_format(rtmps_sink, tap, capfd, caplog):
    bus = InProcessBus()
    before = {p.name for p in REPO.joinpath("relay").iterdir()}
    sink = build_egress_sink(
        youtube_config(rtmps_sink), bus, tap_url=tap_url(tap), environ={}, cafile=rtmps_sink.cafile
    )
    results: dict[str, Any] = {}

    def probe_later() -> None:
        time.sleep(5)
        results["youtube"] = ffprobe_json(rtmps_sink.rtmps_url(), extra=("-ca_file", rtmps_sink.cafile))
        results["tap"] = ffprobe_json(tap.tap_url(), extra=("-rtsp_transport", "tcp"))

    prober = threading.Thread(target=probe_later)
    try:
        prober.start()
        run_source(sink, 22)
        prober.join(60)
        stats = sink.stats()
    finally:
        sink.close()
        prober.join(5)
    assert set(results) == {"youtube", "tap"}, "ffprobe did not finish"
    assert_valid_stream(results["youtube"], label="rtmps sink")
    assert_valid_stream(results["tap"], label="tap")
    # an idle machine drops nothing; a shared laptop may drop a few (the single slot keeps audio in sync)
    assert stats["dropped"] <= 0.01 * stats["submitted"] and stats["encoder_down_drops"] == 0
    for name in ("youtube", "mediamtx_tap"):
        assert stats["destinations"][name]["state"] == "up" and stats["destinations"][name]["failures"] == 0
    # store nothing: no file appeared in the package and no libav or log line carries the key
    assert {p.name for p in REPO.joinpath("relay").iterdir()} == before
    captured = capfd.readouterr()
    assert KEY not in caplog.text + captured.out + captured.err + repr(stats) + repr(bus.history)


def test_a_bad_certificate_is_refused_and_the_only_destination_down_asserts_the_hold(rtmps_sink):
    config = Config(
        _env_file=None,
        youtube_stream_key=SecretStr(KEY),
        destinations=[{"kind": "youtube", "url_host": f"localhost:{rtmps_sink.rtmps_port}"}],
        relay={"ingest_hold_after_s": 0.3},
    )
    bus = InProcessBus()
    sink = build_egress_sink(config, bus, tap_url=None, environ={})  # no cafile: the test CA is not trusted
    try:
        run_source(sink, 6)
        publisher = sink.egress.destinations.publishers[0]
        assert (
            publisher.state == "down" and publisher.forwarder.stats["last_error"] == "tls-verification-failed"
        )
        assert publisher.video_packets_out == 0  # not one media byte went past a failed verification
        holds = [(h.source, h.active) for h in bus.of(Topic.HOLD)]
        assert holds == [(HoldSource.AUTO_EGRESS, True)]
    finally:
        sink.close()


def test_encoder_kill_is_replaced_within_the_backoff_and_the_tap_keeps_flowing(tap):
    config = Config(_env_file=None, destinations=[{"kind": "mediamtx_tap"}])
    bus = InProcessBus()
    sink = build_egress_sink(config, bus, tap_url=tap_url(tap), environ={})
    seen: dict[str, Any] = {}
    try:

        def on_frame(i: int) -> None:
            if i == 90:
                seen["pid"] = sink.egress.encoder.pid
                seen["killed_at"] = time.monotonic()
                sink.egress.encoder.kill_worker()
            if "killed_at" in seen and "down_at" not in seen and not sink.healthy:
                seen["down_at"] = time.monotonic()
            if "down_at" in seen and "up_at" not in seen and sink.healthy:
                seen["up_at"] = time.monotonic()

        run_source(sink, 12, on_frame=on_frame)
        stats = sink.stats()
    finally:
        sink.close()
    assert "down_at" in seen and "up_at" in seen, seen
    assert seen["up_at"] - seen["killed_at"] < 3.0, "the encoder was not replaced within the backoff"
    assert stats["generation"] == 2 and stats["restarts"] == 1 and sink.egress.encoder.pid != seen["pid"]
    tap_stats = stats["destinations"]["mediamtx_tap"]
    assert tap_stats["state"] == "up" and tap_stats["connects"] >= 2  # a new stream after the restart
    codes = [e.detail_code for e in bus.of(Topic.SAFETY_EVENT) if e.source == "relay.encoder"]
    assert codes == ["encoder_down", "encoder_up"]


def test_the_runtime_build_returns_a_working_sink_and_tap(tmp_path):
    try:
        verified_binary()
    except BinaryVerificationError as error:
        pytest.skip(f"MediaMTX binary unavailable: {error}")
    config = Config(
        _env_file=None,
        ingest_port_rtmp=free_port(),
        ingest_port_srt=free_port(),
        relay={"mediamtx_api_port": free_port()},
        destinations=[
            {"kind": "youtube"},
            {"kind": "mediamtx_tap"},
        ],  # youtube has no key: skipped, not fatal
    )
    rtsp = free_port()
    bus = InProcessBus()
    endpoints = build(config, bus, environ={"INGEST_PORT_RTSP": str(rtsp)})
    try:
        sink = endpoints.sink
        assert sink.healthy and [p.name for p in sink.egress.destinations.publishers] == ["mediamtx_tap"]
        out: dict[str, Any] = {}

        def probe() -> None:
            time.sleep(3)
            out["tap"] = ffprobe_json(
                f"rtsp://127.0.0.1:{rtsp}/{PATH_SHIELDED}", seconds=5, extra=("-rtsp_transport", "tcp")
            )

        thread = threading.Thread(target=probe)
        thread.start()
        run_source(sink, 11)
        thread.join(40)
    finally:
        endpoints.sink.close()
        endpoints.source.close()
    video = next(s for s in out["tap"]["streams"] if s["codec_type"] == "video")
    assert (video["codec_name"], video["width"], video["height"]) == ("h264", 1280, 720)
    stages = {t.stage for t in bus.of(Topic.TIMING)}
    assert {"encode", "egress_write"} <= stages


@pytest.mark.slow
def test_sixty_seconds_to_two_outputs_with_zero_dropped_frames_and_encode_cpu(rtmps_sink, tap, capsys):
    """The T1.2 soak: 60 s of 720p30 into the RTMPS sink and the tap, nothing dropped, x264 CPU measured."""
    bus = InProcessBus()
    sink = build_egress_sink(
        youtube_config(rtmps_sink), bus, tap_url=tap_url(tap), environ={}, cafile=rtmps_sink.cafile
    )
    assert sink.egress.encoder.codec == "libx264"
    run_source(sink, 2)  # warm-up: process start, first connects
    time.sleep(0.2)
    base = sink.stats()
    cpu0, wall0, parent0 = cpu_seconds(sink.egress.encoder.pid), time.perf_counter(), time.process_time()
    try:
        submitted = run_source(sink, 60, start=60)
        wall = time.perf_counter() - wall0
        child_cpu = cpu_seconds(sink.egress.encoder.pid) - cpu0
        parent_cpu = time.process_time() - parent0
        time.sleep(0.5)
        stats = sink.stats()
    finally:
        sink.close()
    dropped = stats["dropped"] - base["dropped"]
    report = {
        "encoder": "x264 veryfast", "frames": submitted, "dropped": dropped,
        "encoder_cores": round(child_cpu / wall, 3), "relay_parent_cores_incl_source_loop": round(parent_cpu / wall, 3),
        "encode_ms": timing_summary(bus, "encode"), "egress_write_ms": timing_summary(bus, "egress_write"),
        "rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
    }  # fmt: skip
    with capsys.disabled():
        print("\nT1.2 x264 soak:", json.dumps(report))
    assert dropped == 0 and stats["encoder_down_drops"] == 0
    for name in ("youtube", "mediamtx_tap"):
        destination = stats["destinations"][name]
        assert destination["failures"] == 0 and destination["overflows"] == 0 and destination["state"] == "up"
        assert destination["video_packets_out"] >= stats["written"] - 5  # every encoded frame was muxed
    assert report["encoder_cores"] <= 1.5, report
    assert report["rss_mb"] < 1500


@pytest.mark.gpu
def test_nvenc_is_selected_on_auto_and_produces_the_same_stream_format(tap, capsys):
    if not nvenc_available():
        pytest.skip("h264_nvenc is not available here (no GPU or no NVENC)")
    config = Config(_env_file=None, gpu={"enabled": True}, destinations=[{"kind": "mediamtx_tap"}])
    bus = InProcessBus()
    sink = build_egress_sink(config, bus, tap_url=tap_url(tap), environ={})
    assert sink.egress.encoder.codec == CODEC_NVENC and not sink.egress.encoder.fell_back
    out: dict[str, Any] = {}
    thread = threading.Thread(
        target=lambda: (
            time.sleep(4),
            out.update(tap=ffprobe_json(tap.tap_url(), extra=("-rtsp_transport", "tcp"))),
        )
    )
    run_source(sink, 3)
    time.sleep(0.2)
    base = sink.stats()
    cpu0, wall0 = cpu_seconds(sink.egress.encoder.pid), time.perf_counter()
    try:
        thread.start()
        submitted = run_source(sink, 20, start=90)
        wall = time.perf_counter() - wall0
        child_cpu = cpu_seconds(sink.egress.encoder.pid) - cpu0
        thread.join(40)
        stats = sink.stats()
    finally:
        sink.close()
    report = {
        "encoder": "h264_nvenc p4", "frames": submitted, "dropped": stats["dropped"] - base["dropped"],
        "encoder_cores": round(child_cpu / wall, 3), "encode_ms": timing_summary(bus, "encode"),
    }  # fmt: skip
    with capsys.disabled():
        print("\nT1.2 nvenc:", json.dumps(report))
    assert_valid_stream(out["tap"], label="nvenc tap")
    assert report["dropped"] == 0
