"""Destinations: YouTube, Twitch and the local MediaMTX tap, each muxed and retried on its own.

The encoder (``relay/encoder.py``) encodes once. Every destination here owns a thread, a bounded packet
queue, an FLV muxer and (for YouTube and Twitch) one verified ``TLSForwarder``. A destination that is
slow, rejected or down is retried with backoff and never touches the encoder or the other
destinations. ``HoldCommand(AUTO_EGRESS)`` is published only when EVERY destination is down.

Modelled on IRL Shield ``relay/stream.py`` (MIT, Copyright (c) 2026 Elan Nash; see ``relay/LICENSE``):
the FLV muxer templated from the encoder's header, packet cloning and the loopback-to-TLS design.
Changes: no two-destination cap, no Twitch-only host, no archive, no broadcaster lock; per-destination
backoff; stream keys are ``SecretStr`` and appear only in the URL string built at connect time.
"""

from __future__ import annotations

import logging
import os
import random
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from pydantic import SecretStr

from contracts.enums import Category, HoldSource, Modality, Topic
from contracts.events import HoldCommand, SafetyEvent, StageTiming
from relay.encoder import (
    AUDIO_RATE,
    EncodedPacket,
    EncodeResult,
    StreamInfo,
)
from relay.muxer import ProcessMuxer
from relay.tls_forwarder import TLSForwarder

log = logging.getLogger("relay.destinations")

HOLD_ACTOR = "relay_egress"
STAGE_EGRESS_WRITE = "egress_write"
DEST_BACKOFF = (0.5, 1.0, 2.0, 4.0, 5.0)  # a YouTube stream that is not live yet is retried every 5 s
KIND_YOUTUBE, KIND_TWITCH, KIND_TAP = "youtube", "twitch", "mediamtx_tap"
_DEFAULTS = {
    KIND_YOUTUBE: ("a.rtmps.youtube.com", "live2", "YOUTUBE_STREAM_KEY"),
    KIND_TWITCH: ("live.twitch.tv", "app", "TWITCH_STREAM_KEY"),
}
_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-.]{3,127}")
_HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,252}(?::[0-9]{1,5})?")


# ------------------------------------------------------------------------------------------ specs
@dataclass(frozen=True, repr=False)
class DestinationSpec:
    """One output. Holds a ``SecretStr`` key; ``repr`` and ``str`` never show it."""

    name: str
    kind: str
    host: str = ""
    port: int = 443
    app: str = ""
    key: SecretStr | None = None
    direct_url: SecretStr | None = None  # the tap: a loopback MediaMTX URL that contains the relay's login
    tls: bool = True

    def __repr__(self) -> str:
        return f"DestinationSpec(name={self.name!r}, kind={self.kind!r}, host={self.host!r}, tls={self.tls})"

    __str__ = __repr__

    def url(self, forward_port: int | None) -> str:
        """The URL the muxer opens. It contains the key: build it at connect time, never log or keep it."""
        if self.direct_url is not None:
            return self.direct_url.get_secret_value()
        if self.key is None or forward_port is None:
            raise ValueError("destination has no key or forwarder")
        return f"rtmp://127.0.0.1:{forward_port}/{self.app}/{self.key.get_secret_value()}"


def parse_peer(url_host: str, default_port: int = 443) -> tuple[str, int]:
    """``host`` or ``host:port`` to ``(host, port)``. Anything with a path, user info or query is refused."""
    if not isinstance(url_host, str) or not _HOST_RE.fullmatch(url_host):
        raise ValueError("invalid destination host")
    host, _, port = url_host.partition(":")
    number = int(port) if port else default_port
    if not 1 <= number <= 65535:
        raise ValueError("invalid destination port")
    return host, number


def valid_key(value: str) -> bool:
    return bool(_KEY_RE.fullmatch(value))


@dataclass(frozen=True, slots=True)
class BuiltDestinations:
    specs: tuple[DestinationSpec, ...]
    skipped: tuple[str, ...]  # fixed codes such as ``youtube:no_key``


def build_destinations(
    config: Any, *, tap_url: str | None = None, environ: Mapping[str, str] | None = None
) -> BuiltDestinations:
    """Specs for every enabled ``Config.destinations`` entry. A destination without a key is skipped.

    Keys come from ``Config.youtube_stream_key`` / ``twitch_stream_key`` (``SecretStr``) and fall back
    to ``YOUTUBE_STREAM_KEY`` / ``TWITCH_STREAM_KEY`` in the environment. There is no destination cap.
    """
    env = environ if environ is not None else {}
    secrets = {KIND_YOUTUBE: config.youtube_stream_key, KIND_TWITCH: config.twitch_stream_key}
    specs: list[DestinationSpec] = []
    skipped: list[str] = []
    counts: dict[str, int] = {}
    for entry in config.destinations:
        if not entry.enabled:
            continue
        counts[entry.kind] = counts.get(entry.kind, 0) + 1
        name = entry.kind if counts[entry.kind] == 1 else f"{entry.kind}{counts[entry.kind]}"
        if entry.kind == KIND_TAP:
            if tap_url:
                specs.append(
                    DestinationSpec(
                        name, KIND_TAP, "127.0.0.1", 0, "shielded", None, SecretStr(tap_url), False
                    )
                )
            else:
                skipped.append(f"{name}:no_tap")
            continue
        default_host, app, env_name = _DEFAULTS[entry.kind]
        key = secrets.get(entry.kind)
        value = key.get_secret_value() if key is not None else env.get(env_name, "")
        if not value:
            skipped.append(f"{name}:no_key")
            continue
        if not valid_key(value):
            skipped.append(f"{name}:bad_key")
            continue
        host, port = parse_peer(entry.url_host or default_host)
        specs.append(DestinationSpec(name, entry.kind, host, port, app, SecretStr(value), None, True))
    return BuiltDestinations(tuple(specs), tuple(skipped))


def _debug_traceback() -> None:
    """Local debugging only (``STREAMSARTHI_RELAY_DEBUG=1``): libav text may contain a stream key."""
    if os.environ.get("STREAMSARTHI_RELAY_DEBUG"):
        import traceback

        traceback.print_exc()


Opener = Callable[[str, StreamInfo], Any]


def default_opener(url: str, info: StreamInfo) -> Any:
    """Connect in a killable worker process: libav holds the GIL while it connects (see ``relay/muxer.py``)."""
    return ProcessMuxer(url, info)


# --------------------------------------------------------------------------------------- publisher
@dataclass(slots=True)
class _Entry:
    generation: int
    info: StreamInfo
    packet: EncodedPacket
    seq: int
    pts: float
    handoff: float


class DestinationPublisher:
    """One destination: bounded queue, own thread, own muxer, own retry. Never blocks ``offer()``."""

    def __init__(
        self,
        spec: DestinationSpec,
        *,
        fps: int = 30,
        opener: Opener = default_opener,
        forwarder: TLSForwarder | None = None,
        backoff: Sequence[float] = DEST_BACKOFF,
        jitter: float = 0.2,
        clock: Callable[[], float] = time.monotonic,
        queue_seconds: float = 6.0,
    ):
        self.spec, self.fps = spec, fps
        self.forwarder = forwarder
        self._opener, self._backoff, self._jitter, self._clock = opener, tuple(backoff), jitter, clock
        self._limit = max(60, int(queue_seconds * fps * 3))
        self._queue: deque[_Entry] = deque()
        self._cond = threading.Condition()
        self._stop = False
        self._overflow = False
        self._attempt = 0
        self._retry_at = 0.0
        self._current: Any = None  # the live muxer, so abort() can kill a stuck worker
        self.state = "idle"  # idle | connecting | up | down
        self._up_announced = False
        self.down_since: float | None = None
        self.last_error: str | None = None
        self.busy_since: float | None = None
        self.on_state: Callable[[DestinationPublisher, str, str | None], None] | None = None
        self.on_timing: Callable[[str, int, float, float, float], None] | None = None
        self.is_primary: Callable[[], bool] = lambda: True
        self.packets_out = 0
        self.video_packets_out = 0
        self.connects = 0
        self.failures = 0
        self.overflows = 0
        self.thread = threading.Thread(target=self._run, name=f"relay-dest-{spec.name}", daemon=True)

    # -- public
    @property
    def name(self) -> str:
        return self.spec.name

    def start(self) -> DestinationPublisher:
        self.thread.start()
        return self

    def offer(self, entries: Sequence[_Entry]) -> None:
        with self._cond:
            if self._stop:
                return
            if len(self._queue) + len(entries) > self._limit:
                self._queue.clear()
                self._overflow = True
                self.overflows += 1
            self._queue.extend(entries)
            self._cond.notify()

    def abort(self) -> None:
        """Unblock a stalled write: cut the loopback-to-TLS connection and kill the muxer worker."""
        if self.forwarder is not None:
            self.forwarder.reset_connections()
        killer = getattr(self._current, "kill", None)
        if killer is not None:
            try:
                killer()
            except Exception:  # noqa: BLE001
                pass

    def stats(self) -> dict[str, Any]:
        with self._cond:
            depth = len(self._queue)
        return {
            "state": self.state, "last_error": self.last_error, "packets_out": self.packets_out,
            "video_packets_out": self.video_packets_out, "connects": self.connects, "failures": self.failures, "overflows": self.overflows,
            "queue": depth,
        }  # fmt: skip

    def close(self) -> None:
        with self._cond:
            self._stop = True
            self._queue.clear()
            self._cond.notify_all()
        self.abort()
        if self.thread.is_alive():
            self.thread.join(timeout=10)
        if self.forwarder is not None:
            try:
                self.forwarder.close()
            except RuntimeError:
                log.warning("tls forwarder for %s did not stop", self.spec.name)

    # -- thread
    def _set_state(self, state: str, code: str | None = None) -> None:
        previous = self.state
        if previous == state or (state == "connecting" and previous == "down"):
            return  # a retry keeps reporting "down" until it works
        self.state = state
        announce = False
        if state == "down":
            self.down_since = self._clock()
            announce, self._up_announced = True, False
        elif state == "up":
            self.down_since = None
            announce, self._up_announced = not self._up_announced, True
        callback = self.on_state
        if announce and callback is not None:
            try:
                callback(self, state, code)
            except Exception:  # noqa: BLE001
                log.debug("destination state callback failed", exc_info=True)

    def _fail(self, code: str) -> None:
        self.failures += 1
        self.last_error = code
        self._attempt += 1
        delay = self._backoff[min(self._attempt - 1, len(self._backoff) - 1)]
        self._retry_at = self._clock() + delay * (1.0 + random.uniform(0.0, self._jitter))
        self._set_state("down", code)

    def _drop(self, muxer: Any) -> None:
        self._current = None
        if muxer is not None:
            try:
                muxer.close()
            except Exception:  # noqa: BLE001
                pass

    def _latest_keyframe(self, entry: _Entry) -> _Entry:
        """After a (slow) connect, start from the newest keyframe of this generation, not an old one."""
        with self._cond:
            chosen = -1
            for index, queued in enumerate(self._queue):
                if (
                    queued.generation == entry.generation
                    and queued.packet.is_video
                    and queued.packet.keyframe
                ):
                    chosen = index
            if chosen < 0:
                return entry
            for _ in range(chosen):
                self._queue.popleft()
            return self._queue.popleft()

    def _run(self) -> None:
        muxer: Any = None
        generation = -1
        offsets: tuple[int, int] = (0, 0)
        try:
            while True:
                with self._cond:
                    while not self._queue and not self._stop and not self._overflow:
                        self._cond.wait(0.25)
                    if self._stop:
                        return
                    overflow, self._overflow = self._overflow, False
                    entry = self._queue.popleft() if self._queue else None
                if overflow:
                    self._drop(muxer)
                    muxer = None
                    self._fail("queue-overflow")
                if entry is None:
                    continue
                if muxer is not None and entry.generation != generation:
                    self._drop(muxer)  # a new encoder generation is a new stream: reconnect, not a failure
                    muxer = None
                    if self.state == "up":
                        self._set_state("connecting")
                if muxer is None:
                    if self._clock() < self._retry_at or not (
                        entry.packet.is_video and entry.packet.keyframe
                    ):
                        continue
                    muxer = self._connect(entry)
                    if muxer is None:
                        continue
                    generation = entry.generation
                    entry = self._latest_keyframe(entry)
                    key_dts = entry.packet.dts
                    offsets = (key_dts, round(key_dts * AUDIO_RATE / self.fps))
                if not self._write(muxer, entry, offsets):
                    self._drop(muxer)
                    muxer = None
        finally:
            self._drop(muxer)

    def _connect(self, entry: _Entry) -> Any:
        self._set_state("connecting")
        self.busy_since = self._clock()
        try:
            url = self.spec.url(None if self.forwarder is None else self.forwarder.port)
            muxer = self._opener(url, entry.info)
        except Exception as error:  # noqa: BLE001 - libav text can contain the URL; keep only a fixed code
            self.busy_since = None
            log.warning("destination %s connect failed: %s", self.spec.name, type(error).__name__)
            _debug_traceback()
            self._fail("connect-failed")
            return None
        self.busy_since = None
        self._current = muxer
        self.connects += 1
        return muxer

    def _write(self, muxer: Any, entry: _Entry, offsets: tuple[int, int]) -> bool:
        packet = entry.packet
        offset = offsets[0] if packet.is_video else offsets[1]
        if packet.dts - offset < 0:
            return True  # audio that precedes the first keyframe of this connection
        shifted = replace(packet, pts=packet.pts - offset, dts=packet.dts - offset)
        self.busy_since = self._clock()
        try:
            muxer.write(shifted)
        except Exception as error:  # noqa: BLE001
            self.busy_since = None
            log.warning("destination %s write failed: %s", self.spec.name, type(error).__name__)
            _debug_traceback()
            self._fail("mux-failed")
            return False
        self.busy_since = None
        self.packets_out += 1
        if packet.is_video:
            self.video_packets_out += 1
        if self.state != "up":
            self._attempt = 0
            self.last_error = None
            self._set_state("up")
        if packet.is_video and self.on_timing is not None and self.is_primary():
            self.on_timing(STAGE_EGRESS_WRITE, entry.seq, entry.pts, entry.handoff, self._clock())
        return True


# ------------------------------------------------------------------------------------------- the set
class DestinationSet:
    """All destinations, the bus events for their state and the all-down ``AUTO_EGRESS`` hold."""

    def __init__(
        self,
        publishers: Sequence[DestinationPublisher],
        *,
        bus: Any | None = None,
        session_id: str | None = None,
        hold_after_s: float = 1.0,
        stall_after_s: float = 6.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.publishers = tuple(publishers)
        self._bus, self._clock = bus, clock
        self._session = session_id or uuid.uuid4().hex
        self._hold_after, self._stall_after = hold_after_s, stall_after_s
        self.hold_active = False
        self.holds = 0
        self._primary: DestinationPublisher | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None
        for publisher in self.publishers:
            publisher.on_state = self._state_changed
            publisher.on_timing = self.publish_timing
            publisher.is_primary = lambda p=publisher: self.primary() is p

    # -- lifecycle
    def start(self) -> DestinationSet:
        for publisher in self.publishers:
            publisher.start()
        self._monitor = threading.Thread(target=self._watch, name="relay-dest-monitor", daemon=True)
        self._monitor.start()
        return self

    def close(self) -> None:
        self._stop.set()
        if self._monitor is not None:
            self._monitor.join(timeout=3)
        for publisher in self.publishers:
            publisher.close()

    # -- data path
    def offer(self, result: EncodeResult, info: StreamInfo, handoff: float) -> None:
        entries = [
            _Entry(result.generation, info, packet, result.video_index, result.pts, handoff)
            for packet in result.packets
        ]
        if not entries:
            return
        for publisher in self.publishers:
            publisher.offer(entries)

    def primary(self) -> DestinationPublisher | None:
        """The destination whose ``egress_write`` is the stage timing: sticky while it stays up (one per frame)."""
        with self._lock:
            current = self._primary
            if current is not None and current.state == "up":
                return current
            self._primary = next((p for p in self.publishers if p.state == "up"), None)
            return self._primary

    @property
    def healthy_count(self) -> int:
        return sum(1 for p in self.publishers if p.state == "up")

    def stats(self) -> dict[str, Any]:
        return {p.name: p.stats() for p in self.publishers} | {"hold_active": self.hold_active}

    # -- bus
    def _publish(self, topic: Topic, event: Any) -> None:
        if self._bus is None:
            return
        try:
            self._bus.publish(topic, event)
        except Exception:  # noqa: BLE001
            log.debug("bus publish failed", exc_info=True)

    def publish_timing(self, stage: str, seq: int, pts: float | None, start: float, end: float) -> None:
        self._publish(Topic.TIMING, StageTiming(stage, seq, pts, start, max(end, start)))

    def publish_safety(self, source: str, category: Category, action: str, code: str) -> None:
        self._publish(
            Topic.SAFETY_EVENT,
            SafetyEvent(
                event_id=uuid.uuid4().hex, session_id=self._session, wall_ts=datetime.now(UTC).isoformat(),
                pts=None, modality=Modality.SYSTEM, category=category, action=action, confidence=1.0,
                bbox=None, duration_ms=None, source=source, detail_code=code,
            ),
        )  # fmt: skip

    def _state_changed(self, publisher: DestinationPublisher, state: str, code: str | None) -> None:
        source = f"relay.egress.{publisher.name}"
        code = "destination_up" if state == "up" else "destination_down"
        self.publish_safety(source, Category.SYSTEM_STATUS, "warned", code)
        self.evaluate()

    # -- hold
    def evaluate(self) -> None:
        """Assert ``AUTO_EGRESS`` when every destination has been down for ``hold_after_s``; release on the first up."""
        if not self.publishers:
            return
        now = self._clock()
        with self._lock:
            any_up = any(p.state == "up" for p in self.publishers)
            all_down = all(p.state == "down" and p.down_since is not None for p in self.publishers)
            if (
                all_down
                and not self.hold_active
                and now - max(p.down_since or now for p in self.publishers) >= self._hold_after
            ):
                self.hold_active = True
                self.holds += 1
                assert_hold = True
            elif any_up and self.hold_active:
                self.hold_active = False
                assert_hold = False
            else:
                return
        self._publish(Topic.HOLD, HoldCommand(HoldSource.AUTO_EGRESS, assert_hold, HOLD_ACTOR))
        if assert_hold:
            self.publish_safety("relay.egress", Category.HOLD, "held", "egress_down")

    def _watch(self) -> None:
        while not self._stop.wait(0.2):
            self.evaluate()
            now = self._clock()
            for publisher in self.publishers:
                started = publisher.busy_since
                if started is not None and now - started > self._stall_after:
                    publisher.abort()  # a stalled TLS destination is cut; the write then fails and retries
