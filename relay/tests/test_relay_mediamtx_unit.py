"""MediaMTX supervisor, config and log classifier without a real MediaMTX process.

Ported in part from IRL Shield tests/m2/test_mediamtx_diagnostics.py.
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from io import BytesIO
from pathlib import Path

import pytest
import yaml

from relay import mediamtx
from relay.mediamtx import (
    BACKOFF_SECONDS,
    BinaryVerificationError,
    LogClassifier,
    MediaMTXSupervisor,
    PortInUseError,
    check_ports_free,
    render_config,
    verified_binary,
)
from relay.security import Credentials, RelayAddress
from relay.tests.support_media import free_address

SECRET = "SYNTHETIC-PRIVATE-PASSWORD"


# --------------------------------------------------------------------------- binary pin
def test_default_backoff_is_the_specified_sequence():
    assert BACKOFF_SECONDS == (0.5, 1.0, 2.0, 4.0, 8.0)


def test_refuses_a_binary_with_the_wrong_sha256(tmp_path):
    (tmp_path / "mediamtx").write_bytes(b"not the pinned binary")
    with pytest.raises(BinaryVerificationError) as error:
        verified_binary(tmp_path)
    assert error.value.code == "binary_hash"


def test_missing_binary_points_at_fetch_tools(tmp_path):
    with pytest.raises(BinaryVerificationError, match="fetch_tools.sh"):
        verified_binary(tmp_path)


def test_accepts_a_binary_matching_the_pin(tmp_path):
    import hashlib

    payload = b"synthetic binary"
    (tmp_path / "mediamtx").write_bytes(payload)
    assert verified_binary(tmp_path, hashlib.sha256(payload).hexdigest()) == tmp_path / "mediamtx"


def test_supervisor_constructor_verifies_the_binary_unless_given_one(tmp_path, monkeypatch):
    monkeypatch.setattr(mediamtx, "TOOL_DIR", tmp_path)
    monkeypatch.setattr(mediamtx, "verified_binary", lambda: verified_binary(tmp_path))
    with pytest.raises(BinaryVerificationError):
        MediaMTXSupervisor(RelayAddress())


# --------------------------------------------------------------------------- config
def _config(address=None, **kwargs):
    address = address or RelayAddress()
    phone, internal = Credentials.generate(), Credentials.generate()
    return yaml.safe_load(render_config(address, phone, internal, **kwargs)), phone, internal


def test_rendered_config_disables_everything_we_do_not_use():
    config, _, _ = _config(srt_latency_ms=350)
    for key in ("hls", "webrtc", "moq", "metrics", "pprof", "playback"):
        assert config[key] is False, key
    assert config["pathDefaults"]["record"] is False
    assert config["apiAddress"] == "127.0.0.1:9997" and config["api"] is True
    assert config["rtspAddress"] == "127.0.0.1:8554" and config["rtspTransports"] == ["tcp"]
    assert config["rtmpAddress"] == "127.0.0.1:1935"
    assert config["srtAddress"] == "127.0.0.1:8890" and config["srtMinLatency"] == "350ms"
    assert set(config["paths"]) == {"camera", "shielded"}
    assert config["paths"]["camera"]["overridePublisher"] is True
    assert config["paths"]["shielded"]["overridePublisher"] is False
    assert config["authMethod"] == "internal"


def test_rendered_config_binds_publishers_to_the_requested_lan_address():
    config, _, _ = _config(RelayAddress("192.168.1.20", rtmp_port=1936, srt_port=8891))
    assert config["rtmpAddress"] == "192.168.1.20:1936"
    assert config["srtAddress"] == "192.168.1.20:8891"
    assert config["rtspAddress"].startswith("127.0.0.1:"), "internal RTSP is always loopback"
    assert config["apiAddress"].startswith("127.0.0.1:"), "API is always loopback"


def test_rendered_config_has_no_unreplaced_placeholders_and_carries_the_users():
    text = render_config(RelayAddress(), *[Credentials.generate() for _ in range(2)])
    assert "$" not in text
    users = yaml.safe_load(text)["authInternalUsers"]
    assert len(users) == 4


@pytest.mark.parametrize("latency", [0, 19, 2001, 1.5, "120"])
def test_srt_latency_is_validated(latency):
    with pytest.raises(ValueError):
        _config(srt_latency_ms=latency)


def test_template_file_holds_no_key_material():
    text = mediamtx.CONFIG_TEMPLATE.read_text()
    assert "pass:" not in text and "user:" not in text, "logins enter only through the users placeholder"


# --------------------------------------------------------------------------- fake process
class FakeProc:
    pid = 4242

    def __init__(self, args, env, alive=True):
        self.args, self.env = args, env
        self.stdin = BytesIO()
        self._stdin_closed = False
        self.stdout = BytesIO(b"")
        self.returncode = None if alive else 1
        self.config = b""

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class RecordingPopen:
    """Records every spawn; each child's stdin writes are captured, and children can be killed."""

    def __init__(self, die_at_start=False):
        self.procs: list[FakeProc] = []
        self.die_at_start = die_at_start
        self.kwargs: list[dict] = []

    def __call__(self, args, **kwargs):
        proc = FakeProc(args, kwargs.get("env", {}), alive=not self.die_at_start)
        original = proc.stdin

        class Capture:
            closed = False

            def write(self, data):
                proc.config += data
                return original.write(data)

            def close(self):
                self.closed = True

        proc.stdin = Capture()
        self.procs.append(proc)
        self.kwargs.append(kwargs)
        return proc


def _supervisor(popen, **kwargs):
    kwargs.setdefault("ready_check", lambda: True)
    kwargs.setdefault("check_ports", False)
    return MediaMTXSupervisor(RelayAddress(), binary="/synthetic/mediamtx", popen=popen, **kwargs)


def test_config_goes_over_stdin_and_nothing_secret_is_in_argv_env_or_on_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("MTX_AUTHINTERNALUSERS", "ambient-override")
    popen = RecordingPopen()
    before = set(tmp_path.iterdir())
    supervisor = _supervisor(popen).start()
    try:
        proc = popen.procs[0]
        assert proc.args == ["/synthetic/mediamtx", "/dev/stdin"]
        config = proc.config.decode()
        assert supervisor.phone.password in config and supervisor.internal.password in config
        for secret in (supervisor.phone.password, supervisor.internal.password):
            assert secret not in " ".join(proc.args)
            assert secret not in json.dumps(proc.env)
        assert not any(k.startswith("MTX_") for k in proc.env), (
            "ambient MTX_* overrides must not reach the child"
        )
        assert popen.kwargs[0]["stdin"] == subprocess.PIPE
    finally:
        supervisor.stop()
    assert set(tmp_path.iterdir()) == before, "no file may be written while the supervisor runs"


def test_restarts_with_the_backoff_sequence_after_the_process_dies():
    popen = RecordingPopen()
    supervisor = _supervisor(popen, backoff=(0.02, 0.04, 0.08)).start()
    try:
        for expected_count in (1, 2, 3, 4):
            popen.procs[-1].returncode = -9  # simulate a crash of the newest child
            deadline = time.monotonic() + 3
            while supervisor.restart_count < expected_count and time.monotonic() < deadline:
                time.sleep(0.01)
            assert supervisor.restart_count == expected_count
        assert supervisor.restart_delays == [0.02, 0.04, 0.08, 0.08], (
            "backoff climbs then holds at the last step"
        )
        assert len(popen.procs) == 5
        assert supervisor.classifier.counts["mediamtx-restarted"] == 4
    finally:
        supervisor.stop()


def test_each_restart_gets_a_fresh_stdin_config_with_the_same_credentials():
    popen = RecordingPopen()
    supervisor = _supervisor(popen, backoff=(0.01,)).start()
    try:
        popen.procs[-1].returncode = 1
        deadline = time.monotonic() + 3
        while supervisor.restart_count < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert popen.procs[0].config == popen.procs[1].config != b""
    finally:
        supervisor.stop()


def test_startup_failure_raises_a_fixed_code_without_diagnostics():
    popen = RecordingPopen(die_at_start=True)
    supervisor = _supervisor(popen, ready_check=lambda: False)
    with pytest.raises(mediamtx.MediaMTXError) as error:
        supervisor.start()
    assert error.value.code == "startup_failed"
    assert SECRET not in str(error.value)


def test_start_times_out_when_the_api_never_answers():
    popen = RecordingPopen()
    supervisor = _supervisor(popen, ready_check=lambda: False, ready_timeout=0.2)
    with pytest.raises(mediamtx.MediaMTXError) as error:
        supervisor.start()
    assert error.value.code == "startup_timeout"
    assert popen.procs[0].returncode is not None, "the child must be reaped on failure"


def test_supervisor_cannot_be_reused():
    supervisor = _supervisor(RecordingPopen()).start()
    try:
        with pytest.raises(RuntimeError):
            supervisor.start()
    finally:
        supervisor.stop()


# --------------------------------------------------------------------------- ports
def test_port_in_use_fails_with_a_clear_message():
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        other = free_address()
        address = RelayAddress(
            "127.0.0.1",
            rtmp_port=port,
            srt_port=other.srt_port,
            rtsp_port=other.rtsp_port,
            api_port=other.api_port,
        )
        with pytest.raises(PortInUseError) as error:
            check_ports_free(address)
        assert str(port) in str(error.value) and "already in use" in str(error.value)
    finally:
        blocker.close()


def test_udp_port_in_use_is_detected_for_srt():
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", 0))
    port = blocker.getsockname()[1]
    try:
        free = free_address()
        address = RelayAddress(
            "127.0.0.1",
            rtmp_port=free.rtmp_port,
            srt_port=port,
            rtsp_port=free.rtsp_port,
            api_port=free.api_port,
        )
        with pytest.raises(PortInUseError, match="UDP"):
            check_ports_free(address)
    finally:
        blocker.close()


# --------------------------------------------------------------------------- log classifier
def test_classifier_keeps_only_fixed_codes_never_raw_lines_or_secrets():
    events = []
    classifier = LogClassifier(events.append)
    lines = (
        b"2026/11/20 14:03:45 INF [RTSP] [session abc] is reading from path 'camera'\n"
        + (
            b"2026/11/20 14:03:45 WAR [RTSP] [session abc] reader is too slow, discarding 70 frames "
            + SECRET.encode()
            + b" rtmp://private.invalid/camera\n"
        )
        * 70
        + b"2026/11/20 14:03:45 WAR [RTMP] [conn def] write queue is full\n"
        + b"2026/11/20 14:03:45 WAR unknown upstream warning "
        + SECRET.encode()
        + b"\n"
        + b"2026/11/20 14:03:45 INF [RTSP] [session abc] closed: EOF\n"
    )
    for line in lines.splitlines(keepends=True):
        classifier.feed(line)
    assert classifier.counts["slow-reader"] == 70
    assert classifier.counts["write-queue-full"] == 1
    assert classifier.counts["other-warning"] == 1
    assert classifier.counts["reader-connected"] == 1
    blob = json.dumps(list(classifier.events), default=str) + json.dumps(events, default=str)
    for leaked in (SECRET, "private.invalid", "abc"):
        assert leaked not in blob
    assert len(classifier.events) == 64, "the event ring is bounded"


def test_classifier_recognises_publisher_lifecycle_and_auth_failures():
    classifier = LogClassifier()
    feed = lambda s: classifier.feed(s.encode() + b"\n")  # noqa: E731
    feed("2026/11/20 14:00:00 INF [RTMP] [conn 127.0.0.1:50000] opened")
    feed("2026/11/20 14:00:01 INF [RTMP] [conn 127.0.0.1:50000] is publishing to path 'camera', 2 tracks")
    feed("2026/11/20 14:00:09 INF [RTMP] [conn 127.0.0.1:50000] closed: stream unpublished")
    feed(
        "2026/11/20 14:01:00 WAR [RTMP] [conn 127.0.0.1:50001] failed to authenticate: authentication failed"
    )
    feed(
        "2026/11/20 14:01:02 INF [RTMP] [conn 127.0.0.1:50001] closed: failed to authenticate: authentication failed"
    )
    feed("2026/11/20 14:02:00 ERR failed to listen: listen tcp 127.0.0.1:1935: bind: address already in use")
    assert classifier.counts["publisher-connected"] == 1
    assert classifier.counts["publisher-disconnected"] == 1
    assert classifier.counts["auth-failure"] == 1, "one failure is one event, not two"
    assert classifier.counts["port-in-use"] == 1
    disconnect = next(e for e in classifier.events if e["code"] == "publisher-disconnected")
    assert disconnect["path"] == "camera" and disconnect["reason"] == "unpublished"


def test_classifier_flags_srt_loss_lines():
    classifier = LogClassifier()
    classifier.feed(b"2026/11/20 14:00:00 WAR [SRT] [conn 127.0.0.1:50000] 12 packets lost\n")
    assert classifier.counts["srt-loss"] == 1


def test_classifier_drops_oversized_lines_and_counts_them():
    classifier = LogClassifier()
    classifier.feed(b"WAR " + b"x" * 9000)  # no newline: still inside the oversized line
    classifier.feed(b"tail of the long line\n")
    classifier.feed(b"2026/11/20 WAR [RTSP] [session abc] write queue is full\n")
    assert classifier.diagnostic_drops == 2
    assert classifier.counts == {"write-queue-full": 1}


def test_a_failing_event_callback_does_not_break_the_reader():
    def boom(event):
        raise RuntimeError("callback bug")

    classifier = LogClassifier(boom)
    classifier.feed(b"2026/11/20 WAR [RTMP] [conn x] write queue is full\n")
    assert classifier.counts["write-queue-full"] == 1


def test_log_reader_thread_feeds_the_classifier_from_the_child_stdout():
    popen = RecordingPopen()
    supervisor = _supervisor(popen)
    supervisor._popen = lambda *a, **k: _with_stdout(
        popen(*a, **k), b"WAR [RTMP] [conn q] write queue is full\n"
    )
    supervisor.start()
    try:
        deadline = time.monotonic() + 2
        while not supervisor.classifier.counts and time.monotonic() < deadline:
            time.sleep(0.01)
        assert supervisor.classifier.counts["write-queue-full"] == 1
    finally:
        supervisor.stop()


def _with_stdout(proc, data):
    proc.stdout = BytesIO(data)
    return proc


def test_no_proc_socket_audit_remains():
    source = Path(mediamtx.__file__).read_text()
    assert "/proc/" not in source and "linux_bound_sockets" not in source


def test_no_archive_code_in_the_relay_package():
    relay_dir = Path(mediamtx.__file__).parent
    banned = ("archive", "seal", "bridge")
    assert not [p.name for p in relay_dir.glob("*.py") if p.stem in banned]
    for path in relay_dir.glob("*.py"):
        assert "Archive" not in path.read_text(), path.name


def test_thread_leak_free_after_stop():
    before = {t.name for t in threading.enumerate()}
    supervisor = _supervisor(RecordingPopen()).start()
    supervisor.stop()
    time.sleep(0.1)
    assert {t.name for t in threading.enumerate()} - before == set()
