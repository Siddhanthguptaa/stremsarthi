"""Async connector base: one supervised session loop with reconnect and exponential backoff.

A subclass implements ``_run_session`` (connect, read until it breaks) and raises:

* ``RetryableError(code)``  : back off and start a new session.
* ``FatalConnectorError``   : stop for good (bad credentials, revoked consent).
* ``FinishedError``         : the platform session ended normally (the stream's chat closed).

Any other exception is retried too, and only the exception class name is recorded, because an
exception message could carry chat text. A chat crash must never stop the stream, so ``run`` never
raises except on cancellation.
"""

from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable

from .backoff import Backoff
from .clock import Clock, SystemClock
from .errors import ChatError
from .logsafe import log_event

StatusCallback = Callable[[str], None]


class RetryableError(ChatError):
    pass


class FatalConnectorError(ChatError):
    pass


class FinishedError(ChatError):
    pass


def _class_code(exc: BaseException) -> str:
    return "unexpected_" + re.sub(r"[^a-z0-9]+", "_", type(exc).__name__.lower())[:40]


class Connector(ABC):
    def __init__(
        self,
        name: str,
        *,
        clock: Clock | None = None,
        backoff: Backoff | None = None,
        on_status: StatusCallback | None = None,
        healthy_after_s: float = 30.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self.name = name
        self.clock: Clock = clock or SystemClock()
        self.backoff = backoff or Backoff()
        self._on_status = on_status
        self._healthy_after_s = healthy_after_s
        self._logger = logger or logging.getLogger(f"chat.{name}")
        self._stop = asyncio.Event()
        self._session_task: asyncio.Future[None] | None = None
        self._healthy = False
        self.state = "idle"
        self.sessions_started = 0
        self.last_error_code = ""

    # ---- to implement -------------------------------------------------------------------------
    @abstractmethod
    async def _run_session(self) -> None:
        """Connect and read until the session breaks. Raise one of the errors above to end it."""

    # ---- helpers for subclasses ---------------------------------------------------------------
    def mark_healthy(self) -> None:
        """Call once a session is really delivering data: the next failure restarts the backoff."""
        self._healthy = True

    def emit_status(self, code: str) -> None:
        if self._on_status is None:
            return
        try:
            self._on_status(code)
        except Exception as exc:  # a status sink must never break the connector
            log_event(self._logger, "status_sink_failed", level=logging.WARNING, error=_class_code(exc))

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    # ---- lifecycle ----------------------------------------------------------------------------
    async def run(self) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            self.state = "running"
            self._healthy = False
            self.sessions_started += 1
            started = self.clock.monotonic()
            self._session_task = asyncio.ensure_future(self._run_session())
            try:
                await self._session_task
                self.state = "finished"
                return
            except asyncio.CancelledError:
                if self._stop.is_set():
                    break
                raise
            except FatalConnectorError as exc:
                self.last_error_code = exc.code
                self.state = "failed"
                log_event(self._logger, "connector_fatal", level=logging.ERROR, code=exc.code)
                self.emit_status(exc.code)
                return
            except FinishedError as exc:
                self.last_error_code = exc.code
                self.state = "finished"
                log_event(self._logger, "connector_finished", code=exc.code)
                self.emit_status(exc.code)
                return
            except ChatError as exc:
                code = exc.code
            except Exception as exc:
                code = _class_code(exc)
            finally:
                self._session_task = None
            self.last_error_code = code
            if self._healthy or self.clock.monotonic() - started >= self._healthy_after_s:
                self.backoff.reset()
            delay = self.backoff.next()
            self.state = "backoff"
            log_event(
                self._logger, "connector_retry", level=logging.WARNING, code=code, delay_s=round(delay, 2)
            )
            await self._sleep_or_stop(delay)
        self.state = "stopped"

    async def stop(self) -> None:
        self._stop.set()
        task = self._session_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _sleep_or_stop(self, delay: float) -> None:
        sleeper = asyncio.ensure_future(self.clock.sleep(delay))
        stopper = asyncio.ensure_future(self._stop.wait())
        try:
            await asyncio.wait({sleeper, stopper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            sleeper.cancel()
            stopper.cancel()
            await asyncio.gather(sleeper, stopper, return_exceptions=True)
