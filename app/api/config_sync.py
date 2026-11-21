"""Re-sends the streamer's live settings to an engine or chat process that started after they were set.

Why: ``PATCH /api/config`` publishes a ``ConfigChange`` once. A toggle set before ``/api/session/start`` (or
while the engine restarted) was published to nobody, because the spawned engine and chat read only their own
yaml and ``.env``. ``ConfigSync`` watches for "an engine just came up" and "the chat process just started" and
publishes the API's overrides again, as a few small ``ConfigChange`` events, several times with a spacing (a
change is idempotent, and the engine has no acknowledgement field to wait for: ``EngineStatus`` carries none).

Only keys the streamer changed through the API (``ConfigStore.overrides``) are sent, and only live keys. Secrets
and private terms can never be in the overrides (``plan_patch`` refuses them), and are filtered again here.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

from contracts import ConfigChange, Topic

from .bus import BusClient
from .config_store import SECRET_FIELDS, ConfigStore

log = logging.getLogger("streamsarthi.api.config_sync")
OFFSETS_S = (0.0, 2.0, 5.0, 10.0)  # first send at once, then again for a slow start (chat takes a while)
MAX_KEYS_PER_CHANGE = 24
ACTOR = "config-sync"


def changes_for(overrides: dict[str, Any]) -> list[ConfigChange]:
    """Pack ``overrides`` into valid ``ConfigChange`` events. A key the contract refuses is skipped."""
    items = [(k, v) for k, v in overrides.items() if k.split(".")[0] not in SECRET_FIELDS]
    out: list[ConfigChange] = []
    for i in range(0, len(items), MAX_KEYS_PER_CHANGE):
        chunk = dict(items[i : i + MAX_KEYS_PER_CHANGE])
        try:
            out.append(ConfigChange(diff=chunk, actor=ACTOR))
            continue
        except ValueError:
            pass
        for key, value in chunk.items():  # one bad value must not drop its neighbours
            with contextlib.suppress(ValueError):
                out.append(ConfigChange(diff={key: value}, actor=ACTOR))
    return out


class ConfigSync:
    def __init__(
        self,
        store: ConfigStore,
        bus: BusClient,
        engine_state: Callable[[], str | None],
        session_id: Callable[[], str | None],
        chat_running: Callable[[], bool],
        offsets_s: tuple[float, ...] = OFFSETS_S,
        tick_s: float = 0.25,
    ) -> None:
        self.store, self.bus = store, bus
        self._engine_state, self._session_id, self._chat_running = engine_state, session_id, chat_running
        self.offsets_s = offsets_s
        self.tick_s = tick_s
        self._due: list[float] = []
        self._seen_state: str | None = None
        self._seen_session: str | None = None
        self._seen_chat = False
        self.sent = 0  # ConfigChange events published (tests and diagnostics)

    def poll(self, now: float | None = None) -> None:
        """One step: notice start-ups and publish whatever bursts are due. Never raises."""
        now = time.monotonic() if now is None else now
        state, sid, chat = self._engine_state(), self._session_id(), self._chat_running()
        came_up = state is not None and (
            self._seen_state is None or (self._seen_state == "starting" and state != "starting")
        )
        new_session = sid != self._seen_session and state is not None
        chat_up = chat and not self._seen_chat
        if came_up or new_session or chat_up:
            self._due = [now + off for off in self.offsets_s]
        self._seen_state, self._seen_session, self._seen_chat = state, sid, chat
        if self._due and now >= self._due[0]:
            self._due.pop(0)
            self._publish()

    def _publish(self) -> None:
        overrides = self.store.live_overrides()
        if not overrides:
            return
        for change in changes_for(overrides):
            try:
                self.bus.publish(Topic.CONFIG, change)
                self.sent += 1
            except (RuntimeError, ValueError, TypeError):
                log.warning("config re-publish failed")
                return

    async def run(self) -> None:
        while True:
            try:
                self.poll()
            except Exception:  # a sync step must never end the loop
                log.exception("config sync step failed")
            await asyncio.sleep(self.tick_s)
