"""Shared plumbing of the vision bus feeds (allowlist, config): one subscription, one daemon thread.

Works with the engine ``LocalBus`` subscription (blocking ``get(timeout)``) and with the contracts fake bus
(``get_nowait``). The subscription is registered in ``start()``, so nothing published afterwards is lost.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from contracts import Topic

POLL_S = 0.05


class BusFeed:
    """Subclasses set ``topic``, ``name`` and implement ``handle(event)`` (must not raise)."""

    topic: Topic
    name = "vision-feed"

    def __init__(self, bus: Any, log: logging.Logger) -> None:
        self._bus = bus
        self._log = log
        self._sub: Any = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._seen_dropped = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        try:
            self._sub = self._bus.subscribe([self.topic])
        except Exception:  # noqa: BLE001 - a bus without subscribe(): the feed cannot work
            self._log.error("%s disabled: the bus has no usable subscribe()", self.name)
            self._sub = None
            self._safe(self.on_failure)
            return
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._sub is not None:
            try:
                self._sub.close()
            except Exception:  # noqa: BLE001 - closing must not fail the host
                self._log.debug("%s subscription close failed", self.name, exc_info=True)
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _next(self) -> Any:
        sub = self._sub
        if hasattr(sub, "get"):  # engine LocalBus subscription
            return sub.get(timeout=POLL_S)
        try:  # contracts.fakes.InProcessBus subscription
            return sub.get_nowait()
        except asyncio.QueueEmpty:
            self._stop.wait(POLL_S)
            return None

    def _safe(self, fn: Any) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a hook must never kill the feed
            self._log.error("%s hook failed: %s", self.name, type(exc).__name__)

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    item = self._next()
                    dropped = getattr(self._sub, "dropped", None)
                    if isinstance(dropped, int) and dropped > self._seen_dropped:
                        self._seen_dropped = dropped
                        self._log.error("%s: the bus dropped events: resync needed", self.name)
                        self._safe(self.on_drop)
                    if item is None:
                        continue
                    _topic, event = item
                    self.handle(event)
                except Exception as exc:  # noqa: BLE001 - one bad event or read must not kill the thread
                    self._log.warning("%s loop error: %s", self.name, type(exc).__name__)
                    self._stop.wait(POLL_S)
        finally:
            if not self._stop.is_set():  # the thread ended without being asked to: fail closed
                self._log.error("%s thread exited unexpectedly", self.name)
                self._safe(self.on_failure)

    def on_failure(self) -> None:
        """The feed cannot (or can no longer) deliver: subclasses fall back to their safe state."""

    def on_drop(self) -> None:
        """Events were dropped by the bus: subclasses stop trusting what they hold."""

    def handle(self, event: Any) -> None:  # pragma: no cover - overridden
        raise NotImplementedError
