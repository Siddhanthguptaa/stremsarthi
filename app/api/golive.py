"""Go-live service: ties the session state machine, the child processes, the recorder and the reports.

``begin`` spawns the engine (and chat, when enabled) and starts recording; ``request_stop`` returns at
once and a background task stops the children (SIGTERM, then kill after ``stop_timeout_s``), lets the
bus drain so the engine's last flushed safety events are recorded, closes the record and builds the
report. Reports stay in RAM for ``session_ttl_s`` (6 h), at most ``MAX_REPORTS`` of them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

from .keystore import DestinationKeys
from .orchestrator import Orchestrator, ingest_urls, new_ingest_login
from .recorder import SessionLog, SessionRecorder
from .report import ReportPrivacyError, build_report
from .sessions import SessionManager, SessionRecord

log = logging.getLogger("streamsarthi.api.golive")
MAX_REPORTS = 8
DRAIN_S = 0.25  # time for the last bus events of a stopping engine to reach the recorder


class GoLive:
    def __init__(
        self,
        *,
        sessions: SessionManager,
        orchestrator: Orchestrator,
        recorder: SessionRecorder,
        keys: DestinationKeys,
        ingest_host: str,
        ingest_rtmp_port: int = 1935,
        ingest_srt_port: int = 8890,
        ttl_s: float,
        stop_timeout_s: float,
        engine_settled: Callable[[], bool],
    ) -> None:
        self.sessions = sessions
        self.orch = orchestrator
        self.recorder = recorder
        self.keys = keys
        self.ingest_host = ingest_host
        self.ingest_rtmp_port = ingest_rtmp_port
        self.ingest_srt_port = ingest_srt_port
        self.ttl_s = ttl_s
        self.stop_timeout_s = stop_timeout_s
        self._engine_settled = engine_settled
        self._reports: dict[str, tuple[float, dict[str, Any]]] = {}
        self._finish_task: asyncio.Task[None] | None = None

    # --- start -------------------------------------------------------------------------------
    async def begin(
        self,
        *,
        delay_seconds: float,
        profile: str,
        destinations: dict[str, bool],
        chat_enabled: bool,
        engine_already_running: bool,
        extra_env: Mapping[str, str] | None = None,
    ) -> SessionRecord:
        """Create the session and spawn its processes. Raises ``OSError`` if the engine cannot start."""
        await self.finalize_open()  # a failed or engine_lost session that was never stopped
        user, password = new_ingest_login()
        ingest = ingest_urls(
            self.ingest_host, user, password, self.ingest_rtmp_port, self.ingest_srt_port
        )
        record = self.sessions.start(delay_seconds, profile, destinations, ingest)
        record.ingest_login = (user, password)
        self.recorder.begin(record.session_id, record.started_at, profile, delay_seconds)
        try:
            await self.orch.start_session(
                profile=profile,
                session_id=record.session_id,
                chat_enabled=chat_enabled,
                secret_env=self.keys.child_env(),
                ingest_env={
                    **(extra_env or {}),
                    "INGEST_PUBLISH_USER": user,
                    "INGEST_PUBLISH_PASSWORD": password,
                },
                attach_to_running_engine=engine_already_running,
            )
        except OSError:
            await self.orch.stop_all(2.0)
            self.recorder.end()
            self.sessions.discard()
            raise
        return record

    # --- stop --------------------------------------------------------------------------------
    def request_stop(self) -> None:
        if self._finish_task is None or self._finish_task.done():
            self.sessions.begin_finish()
            self._finish_task = asyncio.create_task(self._finish(), name="golive-finish")

    async def finalize_open(self) -> None:
        """Close a session whose record is still open (stop was never pressed)."""
        if self.recorder.current is not None:
            self.sessions.stop()
            await self._finish(wait_for_engine=False)

    async def _finish(self, wait_for_engine: bool = True) -> None:
        try:
            await self.orch.stop_all(self.stop_timeout_s)
            if wait_for_engine and self.orch.engine is None:
                # An engine started outside the API: give it the stop timeout to settle.
                deadline = time.monotonic() + self.stop_timeout_s
                while not self._engine_settled() and time.monotonic() < deadline:  # noqa: ASYNC110
                    await asyncio.sleep(0.1)
            await asyncio.sleep(DRAIN_S)
            rec = self.recorder.end()
            if rec is not None:
                self._store(rec)
        except Exception:  # never leave the session stuck in "stopping"
            log.exception("session finish failed")
        finally:
            self.sessions.finish()

    async def wait_finished(self, wait_s: float = 5.0) -> None:
        task = self._finish_task
        if task is not None:
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(task), wait_s)

    async def shutdown(self) -> None:
        if self._finish_task is not None and not self._finish_task.done():
            await self.wait_finished(self.stop_timeout_s + 5)
        await self.orch.shutdown()

    # --- reports -----------------------------------------------------------------------------
    def _store(self, rec: SessionLog) -> None:
        try:
            report = build_report(rec)
        except ReportPrivacyError:
            log.error("report for a session failed the privacy check and was not stored")
            return
        self._sweep()
        self._reports[rec.session_id] = (time.monotonic() + self.ttl_s, report)
        while len(self._reports) > MAX_REPORTS:
            self._reports.pop(next(iter(self._reports)))

    def _sweep(self) -> None:
        now = time.monotonic()
        for sid in [s for s, (exp, _) in self._reports.items() if exp <= now]:
            del self._reports[sid]

    def report(self, session_id: str) -> dict[str, Any] | None:
        """The finished report, or a partial one while the session is live. ``None`` if unknown."""
        cur = self.recorder.current
        if cur is not None and cur.session_id == session_id:
            try:
                return build_report(cur)
            except ReportPrivacyError:
                return None
        self._sweep()
        hit = self._reports.get(session_id)
        return None if hit is None else hit[1]

    def known_sessions(self) -> list[str]:
        self._sweep()
        ids = list(self._reports)
        cur = self.recorder.current
        if cur is not None:
            ids.append(cur.session_id)
        return ids

    def delete_data(self, session_id: str) -> bool:
        removed = self._reports.pop(session_id, None) is not None
        files = self.recorder.delete_files(session_id)
        return removed or files
