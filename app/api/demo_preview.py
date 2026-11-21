"""Demo preview: low-rate MJPEG views of the raw ingest and of the protected output (spec: demo page).

Each stream is one ``ffmpeg`` child (stdout MJPEG pipe, no audio, <= 8 fps, <= 640 px wide) that starts on
the first client and is killed (whole process group, SIGKILL) when the last client leaves, the session ends
or the API shuts down. At most ``max_clients`` (``DEMO_MAX_CLIENTS``) clients share one ffmpeg.

Privacy: frames live only in RAM (the latest one per stream), are never written, hashed or logged. ffmpeg's
stderr goes to the null device. The ``protected`` stream is the anonymous loopback ``shielded`` path (no
credentials). The ``raw`` stream is the anonymous loopback ``camera`` read that the relay allows only when the
engine was started with ``DEMO_RAW_PREVIEW=1`` (the orchestrator sets that, never with ``--lan``). No login is
involved, so no secret can reach the command line.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import signal
import sys
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

Stream = Literal["raw", "protected"]
STREAMS: tuple[Stream, ...] = ("raw", "protected")
DEFAULT_MAX_CLIENTS = 4
MAX_CLIENTS_LIMIT = 8
MAX_FPS = 8
MAX_WIDTH = 640
JPEG_QSCALE = "7"  # mjpeg qscale 7 is roughly JPEG quality 70
BOUNDARY = "frame"
MEDIA_TYPE = f"multipart/x-mixed-replace; boundary={BOUNDARY}"
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
    "X-Accel-Buffering": "no",
}
SOI, EOI = b"\xff\xd8", b"\xff\xd9"
MAX_BUFFER = 4 * 1024 * 1024  # a frame this large is a broken stream, not a picture
IS_WINDOWS = sys.platform == "win32"
LIVE_STATES = frozenset({"starting", "waiting_ingest", "live"})

SpawnFn = Callable[..., Any]  # (argv, stdin_data|None) -> awaitable asyncio-style Process


class PreviewError(Exception):
    """``code`` is the API error code; ``status`` the HTTP status."""

    def __init__(self, status: int, code: str, detail: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.status, self.code, self.detail, self.headers = status, code, detail, headers


@dataclass(frozen=True, slots=True)
class SessionView:
    """What the hub needs to know about the session, read fresh on every call."""

    state: str  # a SessionState value
    host: str = "127.0.0.1"
    rtmp_port: int = 1935


def ffmpeg_argv(prefix: Sequence[str], stream: Stream, view: SessionView) -> tuple[list[str], bytes | None]:
    """The ffmpeg command line and the bytes to write to its stdin (the credentialed raw URL only)."""
    out = [
        "-an",
        "-vf",
        f"fps={MAX_FPS},scale='min({MAX_WIDTH},iw)':-2",
        "-c:v",
        "mjpeg",
        "-q:v",
        JPEG_QSCALE,
        "-f",
        "image2pipe",
        "pipe:1",
    ]
    # A reader can join between video keyframes. Probe beyond the relay's two-second GOP
    # so FFmpeg has time to discover video even when audio is the first track it receives.
    low_latency = [
        "-flags",
        "low_delay",
        "-probesize",
        "1048576",
        "-analyzeduration",
        "3000000",
    ]
    head = [*prefix, "-hide_banner", "-loglevel", "error", "-nostats"]
    if stream == "protected":
        url = f"rtmp://{view.host}:{view.rtmp_port}/shielded"  # anonymous loopback read: no secret in it
        return [*head, "-nostdin", "-fflags", "nobuffer", *low_latency, "-i", url, *out], None
    url = f"rtmp://{view.host}:{view.rtmp_port}/camera"  # anonymous local read (relay: DEMO_RAW_PREVIEW=1)
    return [*head, "-nostdin", "-fflags", "nobuffer", *low_latency, "-i", url, *out], None


async def default_spawn(argv: Sequence[str], stdin_data: bytes | None) -> Any:
    kwargs: dict[str, Any] = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True  # own process group: one killpg takes every helper with it
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,  # never read, never stored: it can name the URL
        **kwargs,
    )


def kill_group(proc: Any) -> None:
    """SIGKILL the child's whole process group (never raises)."""
    pid = getattr(proc, "pid", None)
    if not IS_WINDOWS and pid:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.kill()


class _Run:
    """One live ffmpeg and the newest frame it produced."""

    def __init__(self, stream: Stream) -> None:
        self.stream = stream
        self.proc: Any = None
        self.frame: bytes | None = None
        self.seq = 0
        self.leases: list[Lease] = []
        self.ended = False
        self.cond = asyncio.Condition()
        self.first = asyncio.Event()
        self.reader: asyncio.Task[None] | None = None

    @property
    def clients(self) -> int:
        return len(self.leases)

    async def publish(self, jpeg: bytes) -> None:
        async with self.cond:
            self.frame, self.seq = jpeg, self.seq + 1
            self.cond.notify_all()
        self.first.set()

    async def finish(self) -> None:
        self.ended = True
        self.first.set()
        async with self.cond:
            self.cond.notify_all()


class Lease:
    """One client's slot on a stream. ``release`` is idempotent and safe from any cleanup path.

    ``progress`` is refreshed by the serving generator at least once a second while the client keeps up;
    a lease that has not moved for ``stall_s`` belongs to a dead or stuck connection and is evicted."""

    def __init__(self, hub: PreviewHub, run: _Run, client: str) -> None:
        self.hub, self.run, self.client = hub, run, client
        self.done = False
        self.serving = False
        self.overflow = False  # granted above the cap (one per client address, see ``_has_room``)
        self.progress = time.monotonic()

    def touch(self) -> None:
        self.progress = time.monotonic()

    def release(self) -> None:
        if not self.done:
            self.done = True
            self.hub.release(self)


class PreviewHub:
    def __init__(
        self,
        view_fn: Callable[[], SessionView],
        ffmpeg_cmd: Sequence[str] = ("ffmpeg",),
        first_frame_timeout_s: float = 8.0,
        spawn: SpawnFn = default_spawn,
        max_clients: int = DEFAULT_MAX_CLIENTS,
        stall_s: float = 2.5,
    ) -> None:
        self.view_fn = view_fn
        self.ffmpeg_cmd = list(ffmpeg_cmd)
        self.first_frame_timeout_s = first_frame_timeout_s
        self.max_clients = max(1, int(max_clients))
        self.stall_s = stall_s
        self._spawn = spawn
        self._runs: dict[Stream, _Run] = {}
        self._locks: dict[Stream, asyncio.Lock] = {}
        self._reapers: set[asyncio.Task[Any]] = set()
        self._watchdog: asyncio.Task[None] | None = None

    # --- state ---------------------------------------------------------------------------------
    def clients(self) -> dict[str, int]:
        return {s: (self._runs[s].clients if s in self._runs else 0) for s in STREAMS}

    def view(self) -> SessionView:
        return self.view_fn()

    def check_session(self, stream: Stream) -> SessionView:
        """409 ``no_session`` / 503 ``stream_unavailable`` unless the stream can exist right now."""
        view = self.view_fn()
        if view.state in ("idle", "ended", "failed", "stopping", "engine_lost"):
            raise PreviewError(409, "no_session", "no live session: start one first")
        if view.state != "live":
            raise PreviewError(503, "stream_unavailable", "the stream is not live yet")
        return view

    def still_wanted(self) -> bool:
        return self.view_fn().state in LIVE_STATES

    # --- lifecycle -----------------------------------------------------------------------------
    def _has_room(self, run: _Run, client: str) -> bool:
        """Free slot, or a client that already holds one may overshoot the cap by one per client address
        (a token refresh opens the new stream before the old one is closed)."""
        if run.clients < self.max_clients:
            return True
        mine = [lease for lease in run.leases if lease.client == client]
        return bool(mine) and not any(lease.overflow for lease in mine)

    async def acquire(self, stream: Stream, client: str = "") -> Lease:
        """Take a client slot (starting ffmpeg for the first one) and wait for the first frame."""
        view = self.check_session(stream)
        lock = self._locks.setdefault(stream, asyncio.Lock())
        async with lock:
            run = self._runs.get(stream)
            if run is not None and run.ended:
                self._drop(run)
                run = None
            if run is not None:
                self._evict_stale(run)  # a dead connection must not keep a viewer out
                if not self._has_room(run, client):
                    raise PreviewError(
                        503,
                        "preview_clients_full",
                        f"at most {self.max_clients} viewers per stream",
                        headers={"Retry-After": "2"},
                    )
            if run is None:
                run = await self._start(stream, view)
            lease = Lease(self, run, client)
            lease.overflow = run.clients >= self.max_clients
            run.leases.append(lease)
        self._ensure_watchdog()
        try:
            await asyncio.wait_for(run.first.wait(), self.first_frame_timeout_s)
        except TimeoutError:
            lease.release()
            raise PreviewError(503, "stream_unavailable", "no picture from the stream") from None
        except BaseException:
            lease.release()
            raise
        if run.frame is None:  # ffmpeg ended before a single frame
            lease.release()
            raise PreviewError(503, "stream_unavailable", "the stream could not be opened")
        lease.serving = True
        lease.touch()
        return lease

    def _evict_stale(self, run: _Run) -> None:
        now = time.monotonic()
        for lease in list(run.leases):
            if lease.serving and now - lease.progress > self.stall_s:
                lease.release()

    def _ensure_watchdog(self) -> None:
        if self._watchdog is None or self._watchdog.done():
            self._watchdog = asyncio.ensure_future(self._watch())

    async def _watch(self) -> None:
        """Frees the slots of connections whose writes have stopped (uvicorn may not deliver the disconnect)."""
        while self._runs:
            await asyncio.sleep(min(0.5, self.stall_s / 2))
            for run in list(self._runs.values()):
                self._evict_stale(run)

    async def _start(self, stream: Stream, view: SessionView) -> _Run:
        argv, stdin_data = ffmpeg_argv(self.ffmpeg_cmd, stream, view)
        run = _Run(stream)
        try:
            run.proc = await self._spawn(argv, stdin_data)
        except (OSError, ValueError):
            raise PreviewError(503, "stream_unavailable", "the preview helper could not start") from None
        self._runs[stream] = run
        if stdin_data is not None and run.proc.stdin is not None:
            try:
                run.proc.stdin.write(stdin_data)
                await run.proc.stdin.drain()
                run.proc.stdin.close()
            except (OSError, ConnectionError, RuntimeError):
                pass  # ffmpeg already gone: the reader reports it as an ended stream
        run.reader = asyncio.create_task(self._read(run), name=f"demo-preview-{stream}")
        return run

    async def _read(self, run: _Run) -> None:
        """Split ffmpeg's stdout into JPEGs (SOI..EOI; 0xFF is always stuffed inside entropy data)."""
        buf = bytearray()
        try:
            while True:
                chunk = await run.proc.stdout.read(65536)
                if not chunk:
                    break
                buf += chunk
                while True:
                    start = buf.find(SOI)
                    if start < 0:
                        buf.clear()
                        break
                    end = buf.find(EOI, start + 2)
                    if end < 0:
                        if start:
                            del buf[:start]
                        break
                    await run.publish(bytes(buf[start : end + 2]))
                    del buf[: end + 2]
                if len(buf) > MAX_BUFFER:
                    break
        except (OSError, ValueError, asyncio.LimitOverrunError):
            pass
        finally:
            buf.clear()
            kill_group(run.proc)  # EOF means ffmpeg is done or broken: make sure nothing is left behind
            await run.finish()
            self._reap(run)

    def _reap(self, run: _Run) -> None:
        task = asyncio.ensure_future(self._wait(run))
        self._reapers.add(task)
        task.add_done_callback(self._reapers.discard)

    @staticmethod
    async def _wait(run: _Run) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(run.proc.wait(), 5.0)

    def _drop(self, run: _Run) -> None:
        if self._runs.get(run.stream) is run:
            del self._runs[run.stream]

    def release(self, lease: Lease) -> None:
        """Give a slot back (``Lease.release`` calls this once). The last one out kills ffmpeg."""
        run = lease.run
        if lease in run.leases:
            run.leases.remove(lease)
        if not run.leases:
            self._stop(run)

    def _stop(self, run: _Run) -> None:
        self._drop(run)
        run.ended = True
        if run.proc is not None:
            kill_group(run.proc)
            self._reap(run)
        if run.reader is not None:
            run.reader.cancel()
        run.first.set()

    async def shutdown(self) -> None:
        for run in list(self._runs.values()):
            self._stop(run)
        if self._watchdog is not None:
            self._watchdog.cancel()
        if self._reapers:
            await asyncio.gather(*list(self._reapers), return_exceptions=True)

    # --- serving -------------------------------------------------------------------------------
    @staticmethod
    def _part(jpeg: bytes) -> bytes:
        return (
            f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode()
            + jpeg
            + b"\r\n"
        )

    async def frames(self, lease: Lease) -> AsyncIterator[bytes]:
        """Multipart parts for one client until ffmpeg ends, the session ends or the client leaves.

        With no new frame for a second the last one is sent again: a write to a dead socket is what makes
        the server notice a client that went away without a clean disconnect."""
        run, seen = lease.run, 0
        try:
            while not run.ended and not lease.done and self.still_wanted():
                lease.touch()
                async with run.cond:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(
                            run.cond.wait_for(lambda last=seen: run.seq != last or run.ended), 1.0
                        )
                    jpeg, seq = run.frame, run.seq
                if jpeg is None or run.ended or lease.done:
                    continue
                seen = seq  # a repeated frame (seq unchanged) is the keepalive
                yield self._part(jpeg)
        finally:
            lease.release()


def split_cmd(value: str) -> list[str]:
    return shlex.split(value, posix=not IS_WINDOWS) or ["ffmpeg"]
