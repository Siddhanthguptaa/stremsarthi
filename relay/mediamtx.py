"""Owned Linux MediaMTX process: hash-pinned binary, stdin config, log classifier, restart backoff.

Adapted from IRL Shield ``relay/mediamtx.py`` (MIT, Copyright (c) 2026 Elan Nash; see
``relay/LICENSE``). Kept: the stdin-only config, the fixed-code log classifier and the rule that
no raw log line is ever retained. Changed: version and SHA-256 bumped to v1.21.4, the ``/proc``
socket audit is replaced by a check through MediaMTX's own API (``/v3/paths/list``), a
supervisor restarts the process with backoff 0.5, 1, 2, 4, 8 s, the archive writer is gone, and
the config comes from ``relay/config/mediamtx.yml.tmpl`` (our paths, recording off, everything
unused disabled).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Mapping
from pathlib import Path
from string import Template
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from relay.security import (
    PATH_CAMERA,
    PATH_SHIELDED,
    Credentials,
    RelayAddress,
    mediamtx_users,
)
from relay.srt import validate_exposure, validate_passphrase

log = logging.getLogger("relay.mediamtx")

VERSION = "v1.21.4"
# SHA-256 of mediamtx_v1.21.4_linux_amd64.tar.gz, as published in the release's checksums.sha256.
ARCHIVE_SHA256 = "be0c05d9c8af8338116977e7f1c3fcb409fbf882075e5ab075cf8baad76a59d2"
# SHA-256 of the extracted `mediamtx` binary (computed after the archive hash matched).
BINARY_SHA256 = "e64868f1cfedc3d4d3d009021216c8d1b5d4dcac94562e07ce1665a04f76c72c"
TOOL_DIR = Path(__file__).resolve().parents[1] / ".tmp" / "tools" / ("mediamtx-" + VERSION)
CONFIG_TEMPLATE = Path(__file__).resolve().parent / "config" / "mediamtx.yml.tmpl"

BACKOFF_SECONDS: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0, 8.0)
HEALTHY_RESET_SECONDS = 10.0


class MediaMTXError(RuntimeError):
    """Fixed-code failures only; never carries a URL, key or raw log line."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


class BinaryVerificationError(MediaMTXError):
    pass


class PortInUseError(MediaMTXError):
    pass


def verified_binary(directory: Path | str = TOOL_DIR, expected_sha256: str = BINARY_SHA256) -> Path:
    """Return the binary only if its SHA-256 matches the pin. Run ``scripts/fetch_tools.sh`` first."""
    path = Path(directory) / "mediamtx"
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise BinaryVerificationError(
            "binary_missing", f"MediaMTX {VERSION} not found at {path}; run scripts/fetch_tools.sh"
        ) from None
    if digest != expected_sha256:
        raise BinaryVerificationError("binary_hash", "MediaMTX binary verification failed (SHA-256 mismatch)")
    return path


def render_config(
    address: RelayAddress,
    phone: Credentials,
    internal: Credentials,
    *,
    srt_latency_ms: int = 120,
    srt_passphrase: str | None = None,
    demo_raw_preview: bool = False,
) -> str:
    """Render the stdin config. Contains credentials, so it is returned, never written to disk.

    ``srt_passphrase`` (10..79 characters) makes the ``camera`` path accept only AES-encrypted SRT
    publishers (``srtPublishPassphrase``); RTMP publishers are not affected.
    """
    if type(srt_latency_ms) is not int or not 20 <= srt_latency_ms <= 2000:
        raise ValueError("srt_latency_ms must be an int in 20..2000")
    passphrase_line = ""
    if srt_passphrase is not None:
        # JSON string syntax is valid YAML and survives a leading "-" in the value.
        passphrase_line = f"    srtPublishPassphrase: {json.dumps(validate_passphrase(srt_passphrase))}"
    template = Template(CONFIG_TEMPLATE.read_text(encoding="utf-8"))
    return template.substitute(
        users_json=json.dumps(mediamtx_users(address, phone, internal, demo_raw_preview=demo_raw_preview)),
        api_port=address.api_port,
        rtsp_port=address.rtsp_port,
        bind_host=address.host,
        rtmp_port=address.rtmp_port,
        srt_port=address.srt_port,
        srt_latency_ms=srt_latency_ms,
        camera_passphrase_line=passphrase_line,
    )


# --------------------------------------------------------------------------- log classifier
_CONN_RE = re.compile(r"\[(RTMP|RTSP|SRT)\] \[(conn|session) ([A-Za-z0-9.:_-]{1,80})\]")
_PATH_RE = re.compile(rf"(publishing to|reading from) path '({PATH_CAMERA}|{PATH_SHIELDED})'")
_CLOSE_CODES: tuple[tuple[str, str], ...] = (
    ("i/o timeout", "io-timeout"),
    ("closing existing publisher", "publisher-replaced"),
    ("reader is too slow", "slow-reader"),
    ("write queue is full", "write-queue-full"),
    ("authentication", "authentication"),
    ("already publishing", "publisher-collision"),
    ("terminated", "terminated"),
    ("stream unpublished", "unpublished"),
    ("EOF", "eof"),
)


class LogClassifier:
    """Turn MediaMTX log lines into fixed event codes. Never retains or returns a raw line."""

    def __init__(self, on_event: Callable[[dict[str, Any]], None] | None = None):
        self._on_event = on_event
        self._roles: dict[tuple[str, str], tuple[str, str]] = {}  # conn -> (role, path)
        self._lock = threading.Lock()
        self.counts: Counter[str] = Counter()
        self.events: deque[dict[str, Any]] = deque(maxlen=64)
        self.diagnostic_drops = 0
        self._oversized = False

    def feed(self, raw: bytes) -> list[dict[str, Any]]:
        if len(raw) > 8192 or self._oversized:
            self._oversized = not raw.endswith(b"\n")
            self.diagnostic_drops += 1
            return []
        line = raw.decode("utf-8", errors="replace")
        out: list[dict[str, Any]] = []
        conn = _CONN_RE.search(line)
        identity = (conn.group(1), conn.group(3)) if conn else None
        protocol = conn.group(1) if conn else None

        def emit(code: str, path: str | None = None, **extra: Any) -> None:
            event = {"code": code, "protocol": protocol, "path": path, "at": time.monotonic(), **extra}
            out.append(event)

        closing = "closed: " in line
        if "address already in use" in line:
            emit("port-in-use")
        if conn:
            path_match = _PATH_RE.search(line)
            if path_match:
                role = "publisher" if path_match.group(1) == "publishing to" else "reader"
                if len(self._roles) >= 128:
                    self._roles.pop(next(iter(self._roles)))
                self._roles[identity] = (role, path_match.group(2))  # type: ignore[index]
                emit(f"{role}-connected", path_match.group(2))
            if closing:
                reason = line.split("closed: ", 1)[1]
                code = next((fixed for needle, fixed in _CLOSE_CODES if needle in reason), "peer-close-other")
                role, path = self._roles.pop(identity, (None, None))  # type: ignore[arg-type]
                emit("publisher-disconnected" if role == "publisher" else "conn-closed", path, reason=code)
        if not closing and re.search(r"authenticat", line):
            emit("auth-failure")  # logged once as a warning when the attempt happens
        elif "reader is too slow" in line:
            emit("slow-reader")
        elif "write queue is full" in line:
            emit("write-queue-full")
        elif protocol == "SRT" and re.search(r"(?i)\b(packets? (lost|loss)|too late|dropped)\b", line):
            emit("srt-loss")
        elif re.search(r"(?:^|\s)WAR\s", line) and not out:
            emit("other-warning")
        elif re.search(r"(?:^|\s)ERR\s", line) and not out:
            emit("other-error")
        with self._lock:
            for event in out:
                self.counts[event["code"]] += 1
                if len(self.events) == self.events.maxlen:
                    self.diagnostic_drops += 1
                self.events.append(event)
        if self._on_event is not None:
            for event in out:
                try:
                    self._on_event(event)
                except Exception:  # a callback must never kill the log reader
                    log.debug("event callback failed", exc_info=True)
        return out


# --------------------------------------------------------------------------- supervisor
def _port_free(host: str, port: int, kind: int) -> bool:
    with socket.socket(socket.AF_INET, kind) as sock:
        if kind == socket.SOCK_STREAM:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def check_ports_free(address: RelayAddress) -> None:
    """Fail with a clear message when an ingest port is taken (spec section 7)."""
    checks = (
        (address.host, address.rtmp_port, socket.SOCK_STREAM, "TCP", "RTMP ingest"),
        (address.host, address.srt_port, socket.SOCK_DGRAM, "UDP", "SRT ingest"),
        ("127.0.0.1", address.rtsp_port, socket.SOCK_STREAM, "TCP", "internal RTSP"),
        ("127.0.0.1", address.api_port, socket.SOCK_STREAM, "TCP", "MediaMTX API"),
    )
    for host, port, kind, proto, name in checks:
        if not _port_free(host, port, kind):
            raise PortInUseError(
                "port_in_use",
                f"{proto} port {port} ({name}) on {host} is already in use. Stop the other process "
                "(another MediaMTX or an old Stream Sarthi) or change the port in the configuration.",
            )


class MediaMTXSupervisor:
    """Spawn MediaMTX, feed it the config over stdin, classify its logs and restart it on exit.

    Credentials are generated per run and held in memory only. ``start()`` blocks until the API
    answers ``/v3/paths/list`` (replacing IRL Shield's ``/proc`` socket audit) or raises.
    """

    def __init__(
        self,
        address: RelayAddress | None = None,
        *,
        phone: Credentials | None = None,
        internal: Credentials | None = None,
        srt_latency_ms: int = 120,
        srt_passphrase: str | None = None,
        demo_raw_preview: bool = False,
        binary: Path | str | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        backoff: tuple[float, ...] = BACKOFF_SECONDS,
        popen: Callable[..., Any] = subprocess.Popen,
        ready_check: Callable[[], bool] | None = None,
        ready_timeout: float = 8.0,
        check_ports: bool = True,
    ):
        self.address = address or RelayAddress()
        self.phone = phone or Credentials.generate()
        self.internal = internal or Credentials.generate()
        self.srt_latency_ms = srt_latency_ms
        self.srt_passphrase = srt_passphrase
        self.demo_raw_preview = demo_raw_preview
        self.binary = Path(binary) if binary is not None else verified_binary()
        self.classifier = LogClassifier(on_event)
        self._backoff = tuple(backoff)
        self._popen = popen
        self._ready_check = ready_check or self._api_ready
        self._ready_timeout = ready_timeout
        self._check_ports = check_ports
        self.process: Any = None
        self.restart_count = 0
        self.restart_delays: list[float] = []
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None
        self._log_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._started_at = 0.0
        self._attempt = 0
        # Refuse an unprotected phone-reachable bind, then validate the config renders, before spawning.
        validate_exposure(self.address, self.phone, srt_passphrase)
        render_config(
            self.address,
            self.phone,
            self.internal,
            srt_latency_ms=srt_latency_ms,
            srt_passphrase=srt_passphrase,
            demo_raw_preview=demo_raw_preview,
        )

    # --- URLs for in-process users (memory only; never log them) ---
    def reader_url(self) -> str:
        """URL the PyAV reader opens (internal login). RTMP, not RTSP: see ``ingest.default_container_options``."""
        return self.address.rtmp(PATH_CAMERA, self.internal)

    def publish_url(self, protocol: str) -> str:
        """URL an OBS or phone publisher uses (phone credentials). ``rtmp`` or ``srt``."""
        if protocol == "rtmp":
            return self.address.rtmp(PATH_CAMERA, self.phone)
        if protocol == "srt":
            return self.address.srt(
                "publish",
                PATH_CAMERA,
                self.phone,
                latency_ms=self.srt_latency_ms,
                passphrase=self.srt_passphrase,
            )
        raise ValueError("protocol must be 'rtmp' or 'srt'")

    def tap_url(self) -> str:
        """Anonymous loopback RTSP URL of the `shielded` tap (eval gates)."""
        return self.address.rtsp(PATH_SHIELDED)

    @property
    def pid(self) -> int | None:
        proc = self.process
        return getattr(proc, "pid", None) if proc is not None else None

    # --- API (replaces the /proc socket audit) ---
    def paths(self) -> dict[str, dict[str, Any]] | None:
        """Paths known to MediaMTX keyed by name, or None when the API does not answer."""
        try:
            with urlopen(self.address.api_base() + "/v3/paths/list", timeout=1.0) as response:  # noqa: S310
                items = json.loads(response.read()).get("items", [])
        except (URLError, OSError, ValueError):
            return None
        return {item["name"]: item for item in items if isinstance(item, Mapping) and "name" in item}

    def publisher_ready(self, path: str = PATH_CAMERA) -> bool:
        paths = self.paths()
        return bool(paths and paths.get(path, {}).get("ready"))

    def _api_ready(self) -> bool:
        return self.paths() is not None

    # --- lifecycle ---
    def start(self) -> MediaMTXSupervisor:
        if self._monitor is not None:
            raise RuntimeError("MediaMTXSupervisor cannot be reused")
        if self._check_ports:
            check_ports_free(self.address)
        try:
            self._spawn_and_wait()
        except BaseException:
            self._terminate()
            raise
        self._monitor = threading.Thread(target=self._supervise, name="relay-mediamtx-monitor", daemon=True)
        self._monitor.start()
        return self

    def __enter__(self) -> MediaMTXSupervisor:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def _spawn(self) -> None:
        # Never inherit ambient MTX_* overrides: they can carry credentials and rewrite our config.
        env = {k: v for k, v in os.environ.items() if not k.startswith("MTX_")}
        config = render_config(
            self.address,
            self.phone,
            self.internal,
            srt_latency_ms=self.srt_latency_ms,
            srt_passphrase=self.srt_passphrase,
            demo_raw_preview=self.demo_raw_preview,
        ).encode("utf-8")
        proc = self._popen(
            [str(self.binary), "/dev/stdin"],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.process = proc
        self._log_thread = threading.Thread(
            target=self._read_logs, args=(proc,), name="relay-mediamtx-logs", daemon=True
        )
        self._log_thread.start()
        try:
            proc.stdin.write(config)
            proc.stdin.close()
        except OSError:
            pass  # the child exited before reading; the readiness wait reports it
        self._started_at = time.monotonic()

    def _read_logs(self, proc: Any) -> None:
        stream = proc.stdout
        while True:
            try:
                raw = stream.readline(8193)
            except (OSError, ValueError):
                return
            if not raw:
                return
            self.classifier.feed(raw)

    def _spawn_and_wait(self) -> None:
        self._spawn()
        deadline = time.monotonic() + self._ready_timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                if self._log_thread is not None:
                    self._log_thread.join(timeout=1.0)
                if self.classifier.counts.get("port-in-use"):
                    raise PortInUseError(
                        "port_in_use", "MediaMTX could not bind an ingest port (already in use)"
                    )
                raise MediaMTXError(
                    "startup_failed", "MediaMTX exited during startup (diagnostics suppressed)"
                )
            if self._ready_check():
                return
            time.sleep(0.05)
        raise MediaMTXError("startup_timeout", "MediaMTX did not become ready in time")

    def _supervise(self) -> None:
        while not self._stop.wait(0.1):
            proc = self.process
            if proc is None or proc.poll() is None:
                if proc is not None and time.monotonic() - self._started_at > HEALTHY_RESET_SECONDS:
                    self._attempt = 0
                continue
            delay = self._backoff[min(self._attempt, len(self._backoff) - 1)]
            self._attempt += 1
            self.restart_delays.append(delay)
            self._emit("mediamtx-exit")
            if self._stop.wait(delay):
                return
            try:
                self._spawn_and_wait()
            except MediaMTXError as error:
                self._emit("mediamtx-restart-failed", reason=error.code)
                self._terminate()
                continue
            self.restart_count += 1
            self._emit("mediamtx-restarted")

    def _emit(self, code: str, **extra: Any) -> None:
        event = {"code": code, "protocol": None, "path": None, "at": time.monotonic(), **extra}
        with self.classifier._lock:
            self.classifier.counts[code] += 1
            self.classifier.events.append(event)
        if self.classifier._on_event is not None:
            try:
                self.classifier._on_event(event)
            except Exception:
                log.debug("event callback failed", exc_info=True)

    def _terminate(self) -> None:
        proc = self.process
        if proc is None:
            return
        try:
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:
            pass
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        if self._log_thread is not None:
            self._log_thread.join(timeout=2)
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except OSError:
            pass

    def stop(self) -> None:
        self._stop.set()
        if self._monitor is not None and self._monitor is not threading.current_thread():
            self._monitor.join(timeout=15)
        with self._lock:
            self._terminate()
