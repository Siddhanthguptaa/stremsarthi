"""Go-live orchestration (T6.1): start, supervise and stop the engine and chat child processes.

* The **engine** process (``python -m engine.main --profile <p>``) builds relay, vision, audio and
  guard itself through their ``module:build`` entry points (``Config.runtime.modules``); the API
  never imports those modules. It is required: if it exits while a session runs, the session is
  lost (no silent restart: a restarted engine would start with an empty frame pipeline).
* The **chat** process (``python -m chat.main``) is optional and is only started when
  ``Config.chat.enabled``. If it exits it is restarted with a back-off, up to ``max_restarts``
  times, then reported as ``failed``; the stream is never affected.

Privacy and secrets: child stdout/stderr go to the null device (the API never reads, stores or
relays child output), the command line carries no secret, and the stream keys and ingest login are
passed through the child's *environment* only. ``snapshot()`` returns names, states and exit codes,
never an environment, an argument vector or a pid-adjacent secret.

Every ``spawn`` goes through an injectable factory, so tests can supply a harmless interpreter
command instead of the real modules.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import secrets
import signal
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

log = logging.getLogger("streamsarthi.api.orchestrator")
RAW_PREVIEW_ENV = "DEMO_RAW_PREVIEW"

ProcState = Literal["not_started", "running", "exited", "failed", "stopped"]
IS_WINDOWS = sys.platform == "win32"


class Process(Protocol):
    """The slice of ``asyncio.subprocess.Process`` the supervisor uses."""

    returncode: int | None

    async def wait(self) -> int: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


SpawnFn = Callable[..., Awaitable[Process]]  # (argv, env, *, capture=False) -> Process

# Fixed status codes the chat process prints. Its output is otherwise discarded: only these exact
# tokens are ever picked out of it (never a line, never a message), and they are shown in readiness.
CHAT_STATUS_CODES = (
    "groq_key_missing",
    "yt_no_credentials",
    "yt_login_required",
    "twitch_no_credentials",
    "twitch_login_required",
    "twitch_no_channel",
)
_CODE_RE = re.compile(r"\b(" + "|".join(CHAT_STATUS_CODES) + r")\b")
# Exit codes of ``python -m chat.main`` (0 also means "chat disabled in its own config").
CHAT_EXIT_REASONS = {
    0: "exited_ok",
    1: "config_error",
    2: "no_connector",
    3: "bus_error",
    4: "connectors_failed",
}
CHAT_RESTARTABLE = frozenset({3, -9, -15})  # bus trouble or a killed process; the rest will not fix itself


async def default_spawn(argv: Sequence[str], env: Mapping[str, str], *, capture: bool = False) -> Process:
    kwargs: dict[str, Any] = {}
    if not IS_WINDOWS:
        kwargs["start_new_session"] = True  # so a stop reaches the engine's own children too
    return await asyncio.create_subprocess_exec(
        *argv,
        env=dict(env),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.STDOUT if capture else asyncio.subprocess.DEVNULL,
        **kwargs,
    )  # type: ignore[return-value]


def _signal_group(proc: Process, sig: int) -> None:
    pid = getattr(proc, "pid", None)
    if not IS_WINDOWS and pid:
        try:
            os.killpg(os.getpgid(pid), sig)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    (proc.terminate if sig == signal.SIGTERM else proc.kill)()


@dataclass(slots=True)
class ManagedProcess:
    name: str
    argv: list[str]
    required: bool
    max_restarts: int = 0
    proc: Process | None = None
    state: ProcState = "not_started"
    exit_code: int | None = None
    restarts: int = 0
    started_mono: float | None = None
    next_restart_mono: float | None = None
    stopping: bool = False
    reason: str | None = None  # fixed code: "spawn_failed", "exited", "gave_up", or a chat exit reason
    capture: bool = False  # scan output for the whitelisted status codes (chat only)
    codes: set[str] = field(default_factory=set)
    reader: asyncio.Task[None] | None = field(default=None, repr=False)
    _env: dict[str, str] = field(default_factory=dict, repr=False)

    def public(self, now: float) -> dict[str, Any]:
        up = (
            None
            if self.started_mono is None or self.state != "running"
            else round(now - self.started_mono, 1)
        )
        return {
            "name": self.name,
            "required": self.required,
            "state": self.state,
            "exit_code": self.exit_code,
            "restarts": self.restarts,
            "uptime_s": up,
            "reason": self.reason,
            "codes": sorted(self.codes),
        }


def new_ingest_login() -> tuple[str, str]:
    """Per-session random publisher login. Characters are ``[0-9a-f]`` (the relay allows ``[A-Za-z0-9_-]``)."""
    return "ss" + secrets.token_hex(3), secrets.token_hex(12)


def ingest_urls(
    host: str, user: str, password: str, rtmp_port: int = 1935, srt_port: int = 8890
) -> dict[str, str]:
    """What OBS or a phone app publishes to (not the YouTube or Twitch key). Mirrors ``relay.security``."""
    return {
        "rtmp_url": f"rtmp://{host}:{rtmp_port}/camera?user={user}&pass={password}",
        "srt_url": (
            f"srt://{host}:{srt_port}?streamid=publish:camera:{user}:{password}"
            "&pkt_size=1316&mode=caller&latency=120000"
        ),
    }


class Orchestrator:
    def __init__(
        self,
        *,
        enabled: bool,
        engine_cmd: Sequence[str],
        chat_cmd: Sequence[str],
        stop_timeout_s: float = 10.0,
        spawn: SpawnFn | None = None,
        on_engine_exit: Callable[[int | None], None] | None = None,
        restart_backoff_s: float = 2.0,
        max_chat_restarts: int = 3,
        base_env: Mapping[str, str] | None = None,
        chat_env: Mapping[str, str] | None = None,
    ) -> None:
        self._chat_env = dict(chat_env or {})
        self.enabled = enabled
        self._engine_cmd = list(engine_cmd)
        self._chat_cmd = list(chat_cmd)
        self.stop_timeout_s = stop_timeout_s
        self._spawn = spawn or default_spawn
        self._on_engine_exit = on_engine_exit
        self._backoff = restart_backoff_s
        self._max_chat_restarts = max_chat_restarts
        self._base_env = base_env
        self.engine: ManagedProcess | None = None
        self.chat: ManagedProcess | None = None
        self.attached = False  # an engine that was already running (scripts/run_engine) is used as is
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    # --- lifecycle ---------------------------------------------------------------------------
    async def start_watching(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._watch(), name="orchestrator-watch")

    async def shutdown(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.stop_all()

    def _env(self, extra: Mapping[str, str]) -> dict[str, str]:
        env = dict(os.environ if self._base_env is None else self._base_env)
        # The raw preview opens an unmasked read path in the relay: it is never inherited from the API's own
        # environment by accident, only passed explicitly (see routes/session.py).
        env.pop(RAW_PREVIEW_ENV, None)
        env.update(extra)
        return env

    async def start_session(
        self,
        *,
        profile: str,
        session_id: str,
        chat_enabled: bool,
        secret_env: Mapping[str, str],
        ingest_env: Mapping[str, str],
        attach_to_running_engine: bool = False,
    ) -> None:
        """Spawn the engine (unless one is already running) and, if enabled, chat.

        Raises ``OSError`` when the engine cannot be started; the caller turns that into a 503."""
        async with self._lock:
            self.engine = self.chat = None
            self.attached = False
            if not self.enabled:
                return
            base = {"STREAMSARTHI_SESSION_ID": session_id, "STREAMSARTHI_PROFILE": profile}
            if attach_to_running_engine:
                self.attached = True
            else:
                argv = [a.replace("{profile}", profile) for a in self._engine_cmd]
                self.engine = ManagedProcess("engine", argv, required=True)
                self.engine._env = self._env({**base, **secret_env, **ingest_env})
                await self._launch(self.engine)
                if self.engine.state == "failed":
                    raise OSError("engine could not be started")
            if chat_enabled:
                self.chat = ManagedProcess(
                    "chat",
                    [a.replace("{profile}", profile) for a in self._chat_cmd],
                    required=False,
                    max_restarts=self._max_chat_restarts,
                )
                self.chat.capture = True
                # The same bus endpoints as the API; chat reads its own keys from .env.
                self.chat._env = self._env({**base, **self._chat_env})
                await self._launch(self.chat)

    async def _launch(self, mp: ManagedProcess) -> None:
        try:
            mp.proc = await self._spawn(mp.argv, mp._env, capture=mp.capture)
        except (OSError, ValueError) as exc:
            mp.state, mp.reason, mp.proc = "failed", "spawn_failed", None
            log.warning("%s could not be started (%s)", mp.name, type(exc).__name__)
            return
        mp.state, mp.reason, mp.exit_code = "running", None, None
        stream = getattr(mp.proc, "stdout", None)
        if mp.capture and stream is not None:
            mp.reader = asyncio.create_task(self._scan(mp, stream), name=f"{mp.name}-codes")
        mp.started_mono = time.monotonic()
        mp.next_restart_mono = None
        log.info("%s started", mp.name)

    @staticmethod
    async def _scan(mp: ManagedProcess, stream: Any) -> None:
        """Drain the child's output and keep only the whitelisted status codes."""
        try:
            while True:
                chunk = await stream.read(8192)
                if not chunk:
                    return
                for code in _CODE_RE.findall(chunk.decode("utf-8", errors="ignore")):
                    mp.codes.add(code)
        except (asyncio.CancelledError, OSError, ValueError):
            return

    # --- supervision -------------------------------------------------------------------------
    def _poll(self, mp: ManagedProcess, now: float) -> None:
        if mp.state != "running" or mp.proc is None or mp.proc.returncode is None:
            return
        mp.exit_code = mp.proc.returncode
        mp.proc = None
        if mp.stopping:
            mp.state = "stopped"
            return
        mp.state, mp.reason = "exited", "exited"
        log.warning("%s exited unexpectedly (code %s)", mp.name, mp.exit_code)
        if mp.name == "engine":
            if self._on_engine_exit is not None:
                self._on_engine_exit(mp.exit_code)
            return
        mp.reason = CHAT_EXIT_REASONS.get(mp.exit_code if mp.exit_code is not None else -1, "exited")
        if mp.exit_code == 0:
            return  # clean exit (for example chat disabled in its own config): not an error, no restart
        if mp.exit_code in CHAT_RESTARTABLE and mp.restarts < mp.max_restarts:
            mp.next_restart_mono = now + self._backoff * (2**mp.restarts)
        else:
            mp.state = "failed"

    async def tick(self) -> None:
        """One supervision step (the watch loop calls this; tests call it directly)."""
        now = time.monotonic()
        for mp in (self.engine, self.chat):
            if mp is None:
                continue
            self._poll(mp, now)
            if mp.state == "exited" and mp.next_restart_mono is not None and now >= mp.next_restart_mono:
                mp.restarts += 1
                await self._launch(mp)

    async def _watch(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # supervision must not die on one bad step
                log.exception("orchestrator tick failed")
            await asyncio.sleep(0.5)

    # --- stop --------------------------------------------------------------------------------
    async def _stop_one(self, mp: ManagedProcess, grace_s: float) -> None:
        mp.stopping = True
        mp.next_restart_mono = None
        proc = mp.proc
        if proc is None:
            if mp.state in ("running", "exited"):
                mp.state = "stopped"
            return
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                _signal_group(proc, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), grace_s)
            except TimeoutError:
                log.warning("%s did not stop in %.0f s; killing it", mp.name, grace_s)
                with contextlib.suppress(ProcessLookupError, OSError):
                    _signal_group(proc, getattr(signal, "SIGKILL", signal.SIGTERM))
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(proc.wait(), 5.0)
        mp.exit_code = proc.returncode
        mp.proc = None
        mp.state = "stopped"
        if mp.reader is not None:
            mp.reader.cancel()

    async def stop_all(self, grace_s: float | None = None) -> None:
        """Chat first (it only reads), then the engine (graceful flush of open safety events)."""
        t = self.stop_timeout_s if grace_s is None else grace_s
        for mp in (self.chat, self.engine):
            if mp is not None:
                await self._stop_one(mp, t)

    # --- read --------------------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "enabled": self.enabled,
            "attached": self.attached,
            "engine": self.engine.public(now) if self.engine else None,
            "chat": self.chat.public(now) if self.chat else None,
        }

    def engine_running(self) -> bool:
        return self.engine is not None and self.engine.state == "running"
