"""Go-live service: ties the session state machine, the child processes, the recorder and the reports.

``begin`` spawns the engine (and chat, when enabled) and starts recording; ``request_stop`` returns at
once and a background task stops the children (SIGTERM, then kill after ``stop_timeout_s``), lets the
bus drain so the engine's last flushed safety events are recorded, closes the record and builds the
report. Reports stay in RAM for ``session_ttl_s`` (6 h), at most ``MAX_REPORTS`` of them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .keystore import DestinationKeys
from .orchestrator import Orchestrator, ingest_urls, new_ingest_login
from .pairing import public_host, srt_passphrase
from .recorder import SESSION_ID_RE, SessionLog, SessionRecorder
from .report import SCHEMA_VERSION, ReportPrivacyError, build_report, validate_report
from .sessions import SessionManager, SessionRecord

log = logging.getLogger("streamsarthi.api.golive")
MAX_REPORTS = 8
DRAIN_S = 0.25  # time for the last bus events of a stopping engine to reach the recorder
REPORT_FILE = (
    "report.json"  # next to events.jsonl in <sessions_dir>/<session id>/; privacy-validated, mode 0600
)
MAX_REPORT_BYTES = 8_000_000
MAX_DISK_REPORTS = 50


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
        fixed_login: tuple[str, str] | None = None,
    ) -> SessionRecord:
        """Create the session and spawn its processes. Raises ``OSError`` if the engine cannot start."""
        await self.finalize_open()  # a failed or engine_lost session that was never stopped
        # A fixed login (INGEST_PUBLISH_USER/PASSWORD, checked by the caller) makes OBS setup one-time;
        # otherwise a new random login per session. Either way pairing reads it from this record.
        user, password = fixed_login or new_ingest_login()
        try:
            dial = public_host(self.ingest_host)  # 0.0.0.0 is a bind address: show an address OBS can dial
        except ValueError:
            dial = (
                "127.0.0.1"  # no LAN address found: OBS on this PC still works (set PAIRING_HOST for a phone)
            )
        ingest = ingest_urls(
            dial, user, password, self.ingest_rtmp_port, self.ingest_srt_port, srt_passphrase()
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
        # API stop (Ctrl-C) while a session is still open: stop the children, close the record and persist
        # its report, so the safety report survives the restart.
        try:
            cur = self.recorder.current
            if (
                cur is not None
            ):  # first a partial copy now: stopping the engine can outlast a launcher's grace
                with contextlib.suppress(ReportPrivacyError):
                    self._write_report(build_report(cur))
            await self.finalize_open()
        except Exception:  # never block the shutdown
            log.exception("could not close the open session at shutdown")
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
        self._write_report(report)

    # --- reports on disk (category, time, confidence, box only: the report was privacy-validated) ----
    def _report_path(self, session_id: str) -> Path | None:
        base = self.recorder.sessions_dir
        if base is None or not SESSION_ID_RE.match(session_id):
            return None
        return base / session_id / REPORT_FILE

    def _write_report(self, report: dict[str, Any]) -> None:
        path = self._report_path(report["session_id"])
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(report, fh, separators=(",", ":"), ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            log.warning("the report could not be saved to disk; it stays in RAM only")

    def _read_report(self, session_id: str) -> dict[str, Any] | None:
        path = self._report_path(session_id)
        if path is None:
            return None
        try:
            if not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or data.get("schema_version") != SCHEMA_VERSION
                or data.get("session_id") != session_id
            ):
                return None
            validate_report(data)  # a file edited by hand must still be free of images and raw PII
            return data
        except (OSError, ValueError, ReportPrivacyError):
            log.warning("a saved report could not be read")
            return None

    def _disk_sessions(self) -> list[str]:
        base = self.recorder.sessions_dir
        if base is None:
            return []
        try:
            found = [
                (p.stat().st_mtime, p.parent.name)
                for p in base.glob(f"*/{REPORT_FILE}")
                if SESSION_ID_RE.match(p.parent.name)
            ]
        except OSError:
            return []
        found.sort()
        return [sid for _, sid in found[-MAX_DISK_REPORTS:]]

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
        return self._read_report(session_id) if hit is None else hit[1]

    def known_sessions(self) -> list[str]:
        self._sweep()
        ids = list(self._reports)
        ids = [*(s for s in self._disk_sessions() if s not in self._reports), *ids]  # past runs, oldest first
        cur = self.recorder.current
        if cur is not None:
            ids.append(cur.session_id)
        return ids

    def delete_data(self, session_id: str) -> bool:
        removed = self._reports.pop(session_id, None) is not None
        files = self.recorder.delete_files(session_id)
        return removed or files
