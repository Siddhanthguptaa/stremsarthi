"""!hold / !unhold: roles from platform data, rate limit, dedupe, denial events, no text in logs."""

from __future__ import annotations

import logging

import pytest

from chat.commands import HoldCommands, actor_hash, parse_command
from chat.common import ChatPublisher, ManualClock
from contracts import ChatMessage, HoldSource, Topic
from contracts.config import ChatCommandsConfig
from contracts.fakes import InProcessBus


def msg(
    text: str, mid: str = "m1", author: str = "u1", platform: str = "twitch", **flags: bool
) -> ChatMessage:
    return ChatMessage(
        platform=platform,  # type: ignore[arg-type]
        message_id=mid,
        channel_id="c1",
        author_id=author,
        author_display="specimen",
        text=text,
        received_at=1.0,
        **flags,
    )


@pytest.fixture
def rig() -> tuple[HoldCommands, InProcessBus, ManualClock]:
    clock = ManualClock()
    bus = InProcessBus()
    pub = ChatPublisher(bus, clock=clock)
    return HoldCommands(bus, pub, ChatCommandsConfig(), clock=clock), bus, clock


def holds(bus: InProcessBus) -> list[tuple[bool, str]]:
    return [(e.active, e.actor) for e in bus.of(Topic.HOLD)]


def test_parse_is_exact_case_insensitive_and_trimmed() -> None:
    assert parse_command("  !HOLD ") == "hold" and parse_command("!UnHold") == "unhold"
    for text in ("!hold now", "!holdd", "hold", "!hold !unhold", "please !hold", ""):
        assert parse_command(text) is None


def test_plain_message_is_not_a_command(rig) -> None:  # type: ignore[no-untyped-def]
    cmds, bus, _ = rig
    assert cmds.handle(msg("hello", is_moderator=True)) is None
    assert bus.of(Topic.HOLD) == []


def test_moderator_and_broadcaster_can_hold_viewer_cannot(rig) -> None:  # type: ignore[no-untyped-def]
    cmds, bus, _ = rig
    assert cmds.handle(msg("!hold", "a", "viewer")).status == "denied"  # type: ignore[union-attr]
    assert bus.of(Topic.HOLD) == []
    assert cmds.handle(msg("!hold", "b", "mod", is_moderator=True)).status == "accepted"  # type: ignore[union-attr]
    assert cmds.handle(msg("!hold", "c", "own", is_broadcaster=True)).status == "accepted"  # type: ignore[union-attr]
    event = bus.of(Topic.HOLD)[0]
    assert event.source is HoldSource.CHAT and event.active is True
    assert event.actor == actor_hash("twitch", "mod") and len(event.actor) == 16
    assert "mod" not in event.actor


def test_unhold_is_broadcaster_only_by_default(rig) -> None:  # type: ignore[no-untyped-def]
    cmds, bus, _ = rig
    assert cmds.handle(msg("!unhold", "a", "mod", is_moderator=True)).status == "denied"  # type: ignore[union-attr]
    assert holds(bus) == []
    assert cmds.handle(msg("!unhold", "b", "own", is_broadcaster=True)).status == "accepted"  # type: ignore[union-attr]
    assert holds(bus) == [(False, actor_hash("twitch", "own"))]


def test_unhold_roles_can_be_widened_by_config(rig) -> None:  # type: ignore[no-untyped-def]
    cmds, bus, _ = rig
    cmds.update_config(ChatCommandsConfig(unhold_roles=["broadcaster", "moderator"]))
    assert cmds.handle(msg("!unhold", "a", "mod", is_moderator=True)).status == "accepted"  # type: ignore[union-attr]


def test_role_comes_from_platform_flags_never_from_text(rig) -> None:  # type: ignore[no-untyped-def]
    cmds, bus, _ = rig
    out = cmds.handle(
        msg(
            "!hold",
            "a",
            "viewer",
        )
    )
    assert out is not None and out.status == "denied" and holds(bus) == []


def test_rate_limit_per_actor_and_kind_and_window_expires(rig) -> None:  # type: ignore[no-untyped-def]
    cmds, bus, clock = rig
    assert cmds.handle(msg("!hold", "a", "mod", is_moderator=True)).status == "accepted"  # type: ignore[union-attr]
    assert cmds.handle(msg("!hold", "b", "mod", is_moderator=True)).status == "rate_limited"  # type: ignore[union-attr]
    # another actor is independent
    assert cmds.handle(msg("!hold", "c", "mod2", is_moderator=True)).status == "accepted"  # type: ignore[union-attr]
    # unhold right after hold by the broadcaster is not blocked by the hold window
    assert cmds.handle(msg("!hold", "d", "own", is_broadcaster=True)).status == "accepted"  # type: ignore[union-attr]
    assert cmds.handle(msg("!unhold", "e", "own", is_broadcaster=True)).status == "accepted"  # type: ignore[union-attr]
    clock.advance(2.01)
    assert cmds.handle(msg("!hold", "f", "mod", is_moderator=True)).status == "accepted"  # type: ignore[union-attr]
    assert len(bus.of(Topic.HOLD)) == 5


def test_duplicate_message_id_is_ignored(rig) -> None:  # type: ignore[no-untyped-def]
    cmds, bus, clock = rig
    assert cmds.handle(msg("!hold", "same", "mod", is_moderator=True)).status == "accepted"  # type: ignore[union-attr]
    clock.advance(10)
    assert cmds.handle(msg("!hold", "same", "mod", is_moderator=True)).status == "duplicate"  # type: ignore[union-attr]
    assert len(bus.of(Topic.HOLD)) == 1
    # same id on the other platform is a different message
    assert (
        cmds.handle(msg("!hold", "same", "mod", platform="youtube", is_moderator=True)).status == "accepted"
    )  # type: ignore[union-attr]


def test_denied_event_is_reported_once_per_window(rig) -> None:  # type: ignore[no-untyped-def]
    cmds, bus, clock = rig
    for i in range(5):
        assert cmds.handle(msg("!hold", f"m{i}", "troll")).status == "denied"  # type: ignore[union-attr]
    events = [e for e in bus.of(Topic.SAFETY_EVENT) if e.detail_code == "cmd_denied"]
    assert len(events) == 1
    clock.advance(2.5)
    cmds.handle(msg("!hold", "later", "troll"))
    assert len([e for e in bus.of(Topic.SAFETY_EVENT) if e.detail_code == "cmd_denied"]) == 2
    assert all(e.source == "chat.commands" for e in bus.of(Topic.SAFETY_EVENT))


def test_disabled_commands_do_nothing_but_stay_silent(rig) -> None:  # type: ignore[no-untyped-def]
    cmds, bus, _ = rig
    cmds.update_config(ChatCommandsConfig(enabled=False))
    out = cmds.handle(msg("!hold", "a", "own", is_broadcaster=True))
    assert out is not None and out.status == "disabled" and out.silent
    assert bus.of(Topic.HOLD) == []


def test_bus_failure_does_not_raise() -> None:
    class Boom:
        def publish(self, topic, event):  # type: ignore[no-untyped-def]
            raise RuntimeError("bus down")

        def subscribe(self, topics):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    cmds = HoldCommands(Boom(), None, ChatCommandsConfig(), clock=ManualClock())  # type: ignore[arg-type]
    out = cmds.handle(msg("!hold", "a", "own", is_broadcaster=True))
    assert out is not None and out.status == "error"


def test_logs_never_contain_ids_or_names(rig, caplog) -> None:  # type: ignore[no-untyped-def]
    cmds, _, _ = rig
    with caplog.at_level(logging.DEBUG):
        cmds.handle(msg("!hold", "secret-msg-id", "secret-author-id"))
        cmds.handle(msg("!hold", "secret-msg-id-2", "secret-author-id", is_moderator=True))
    assert caplog.records
    text = caplog.text
    assert "secret-author-id" not in text and "secret-msg-id" not in text and "specimen" not in text


def test_denial_events_are_capped_across_actors(rig) -> None:  # type: ignore[no-untyped-def]
    cmds, bus, clock = rig
    for i in range(1000):  # a flood of different viewers typing !hold
        assert cmds.handle(msg("!hold", f"m{i}", f"troll{i}")).status == "denied"  # type: ignore[union-attr]
    events = [e for e in bus.of(Topic.SAFETY_EVENT) if e.detail_code == "cmd_denied"]
    assert len(events) == 1 and cmds.denied_suppressed == 999
    clock.advance(2.5)
    cmds.handle(msg("!hold", "later", "troll_x"))
    assert len([e for e in bus.of(Topic.SAFETY_EVENT) if e.detail_code == "cmd_denied"]) == 2
