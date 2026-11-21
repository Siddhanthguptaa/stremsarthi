"""``!hold`` / ``!unhold`` chat commands: platform roles in, ``HoldCommand`` events out.

* ``!hold``: broadcaster or moderator (``commands.hold_roles``). ``!unhold``: broadcaster only by default
  (``commands.unhold_roles``). Anyone else is ignored and reported once per window (shared by all actors; the rest are counted) as
  ``SafetyEvent(detail_code="cmd_denied")``.
* Dedupe by ``(platform, message_id)`` so a redelivered message cannot toggle twice.
* Rate limit: one accepted command per actor per ``rate_limit_s``. The window is kept per command kind,
  so an ``!unhold`` never blocks a following ``!hold`` (the safe direction is never delayed).
* ``handle`` is synchronous and does no I/O, so a hold bypasses every queue (spec 5.5).
* A recognised token always returns an outcome (the message is silent: not shown, not queued, not
  classified). Nothing here logs message text.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal

from chat.common import ChatPublisher, Clock, SystemClock, hash_id, log_event
from contracts import ChatMessage, EventBus, HoldCommand, HoldSource, Topic
from contracts.config import ChatCommandsConfig

from .roles import Command, actor_hash, parse_command, roles_of

_LOG = logging.getLogger("chat.commands")
DENIED_CODE = "cmd_denied"
_DEDUPE_CAP = 4096
_LIMIT_CAP = 2048

Status = Literal["accepted", "denied", "rate_limited", "duplicate", "disabled", "error"]


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    command: Command
    status: Status
    actor: str

    @property
    def silent(self) -> bool:
        """A command message is never shown on the overlay and never queued."""
        return True


class HoldCommands:
    def __init__(
        self,
        bus: EventBus,
        publisher: ChatPublisher | None = None,
        config: ChatCommandsConfig | None = None,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._bus = bus
        self._publisher = publisher
        self._cfg = config or ChatCommandsConfig()
        self._clock: Clock = clock or SystemClock()
        self._seen: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._accepted_at: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._denied_window_start: float | None = None
        self.denied_suppressed = 0  # denials not reported as an event (same window)

    def update_config(self, config: ChatCommandsConfig) -> None:
        self._cfg = config

    @staticmethod
    def is_command(message: ChatMessage) -> bool:
        return parse_command(message.text) is not None

    def handle(self, message: ChatMessage) -> CommandOutcome | None:
        """Return ``None`` when the message is not a command, else what happened to it."""
        command = parse_command(message.text)
        if command is None:
            return None
        actor = actor_hash(message.platform, message.author_id)
        mid = hash_id(message.message_id)
        if not self._cfg.enabled:
            return self._done(command, "disabled", actor, mid)
        key = (message.platform, message.message_id)
        if key in self._seen:
            return self._done(command, "duplicate", actor, mid)
        self._remember(key)
        now = self._clock.monotonic()
        allowed = self._cfg.hold_roles if command == "hold" else self._cfg.unhold_roles
        if not roles_of(message).intersection(allowed):
            return self._deny(command, actor, mid, now)
        limit_key = (actor, command)
        last = self._accepted_at.get(limit_key)
        if last is not None and now - last < self._cfg.rate_limit_s:
            return self._done(command, "rate_limited", actor, mid)
        try:
            self._bus.publish(Topic.HOLD, HoldCommand(HoldSource.CHAT, command == "hold", actor))
        except Exception as exc:  # never let a bus fault crash the chat process
            log_event(_LOG, "hold_publish_failed", level=logging.ERROR, error=type(exc).__name__)
            return self._done(command, "error", actor, mid)
        self._accepted_at[limit_key] = now
        self._accepted_at.move_to_end(limit_key)
        while len(self._accepted_at) > _LIMIT_CAP:
            self._accepted_at.popitem(last=False)
        return self._done(command, "accepted", actor, mid)

    # ---- internals ----------------------------------------------------------------------------
    def _remember(self, key: tuple[str, str]) -> None:
        self._seen[key] = None
        while len(self._seen) > _DEDUPE_CAP:
            self._seen.popitem(last=False)

    def _deny(self, command: Command, actor: str, mid: str, now: float) -> CommandOutcome:
        # One event per window across ALL actors (a flood of viewers typing !hold must not flood the bus); the
        # rest are only counted in ``denied_suppressed``.
        if self._denied_window_start is None or now - self._denied_window_start >= self._cfg.rate_limit_s:
            self._denied_window_start = now
            if self._publisher is not None:
                self._publisher.safety(DENIED_CODE, source="chat.commands")
        else:
            self.denied_suppressed += 1
        return self._done(command, "denied", actor, mid)

    @staticmethod
    def _done(command: Command, status: Status, actor: str, mid: str) -> CommandOutcome:
        level = logging.WARNING if status in ("denied", "error") else logging.INFO
        log_event(_LOG, "chat_command", level=level, mid=mid, cmd=command, status=status)
        return CommandOutcome(command, status, actor)
