"""Mod queue (spec case 13): TTL expiry, cap eviction, approve re-publishes on chat.allowed, platform actions."""

from __future__ import annotations

import asyncio
from typing import Any

from chat.common import ActionResult, ChatPublisher, ManualClock
from chat.modqueue import ModQueue
from chat.testing.pipeline_kit import Stub, mod_action, msg
from contracts import Category, ChatAction, ChatVerdict, Topic
from contracts.config import ModQueueConfig
from contracts.fakes import InProcessBus


def verdict(i: int, action: ChatAction = ChatAction.HOLD_FOR_MOD, platform: str = "twitch") -> ChatVerdict:
    return ChatVerdict(
        platform,  # type: ignore[arg-type]
        f"m{i}",
        action,
        (Category.CHAT_ABUSE,),
        0.9,
        "rules_abuse",
        "rules",
        1.0,
        timeout_seconds=600 if action is ChatAction.TIMEOUT else None,
    )


def make(max_items: int = 500, ttl_s: int = 1800, backend: Stub | None = None):  # type: ignore[no-untyped-def]
    bus, clock = InProcessBus(), ManualClock()
    released: list[str] = []
    events: list[str] = []
    stub = backend or Stub()
    q = ModQueue(
        bus,
        stub,
        lambda m: (released.append(m.message_id), True)[1],
        ChatPublisher(bus, clock=clock),
        ModQueueConfig(max_items=max_items, ttl_s=ttl_s),
        clock=clock,
        on_event=events.append,
    )
    return q, bus, clock, stub, released, events


def add(q: ModQueue, i: int, **kw: Any) -> None:
    q.add(msg("SPECIMEN held text", i), verdict(i), **kw)


def test_add_publishes_item_with_text_for_moderators() -> None:
    q, bus, *_ = make()
    add(q, 1)
    [item] = bus.of(Topic.CHAT_MODQUEUE)
    assert item.state == "pending" and item.message.text == "SPECIMEN held text"
    assert "SPECIMEN" not in repr(q) and "SPECIMEN" not in repr(item)  # reprs never carry text


def test_ttl_expiry_removes_items() -> None:
    q, _, clock, *_ = make(ttl_s=60)
    add(q, 1)
    clock.advance(59)
    add(q, 2)
    assert len(q.snapshot()) == 2
    clock.advance(2)  # m1 is 61 s old, m2 is 2 s old
    assert [i.message.message_id for i in q.snapshot()] == ["m2"]
    assert q.get("twitch", "m1") is None and q.expired == 1


def test_cap_evicts_oldest_and_reports_once_per_eviction() -> None:
    q, _, _, _, _, events = make(max_items=10)
    for i in range(1, 12):
        add(q, i)
    ids = [i.message.message_id for i in q.snapshot()]
    assert len(ids) == 10 and ids[0] == "m2" and "m1" not in ids  # oldest evicted
    assert q.evicted == 1 and events == ["modqueue_full"]


async def test_cap_prefers_evicting_resolved_items_over_pending() -> None:
    q, *_ = make(max_items=10)
    for i in range(1, 11):
        add(q, i)
    assert (await q.handle(mod_action(5, "dismiss"))).status == "ok"
    add(q, 11)
    ids = [i.message.message_id for i in q.snapshot()]
    assert "m5" not in ids and "m1" in ids and len(ids) == 10


async def test_approve_releases_once_and_publishes_allow_verdict() -> None:
    q, bus, _, _, released, _ = make()
    add(q, 1)
    out = await q.handle(mod_action(1, "approve"))
    assert out.status == "ok" and out.state == "approved" and released == ["m1"]
    assert q.get("twitch", "m1").state == "approved"  # type: ignore[union-attr]
    follow = bus.of(Topic.CHAT_VERDICT)[-1]
    assert follow.action is ChatAction.ALLOW and follow.reason == "mod_approved"
    assert bus.of(Topic.CHAT_MODQUEUE)[-1].state == "approved"
    # a second decision on a closed item is refused and releases nothing more
    assert (await q.handle(mod_action(1, "approve"))).status == "not_pending" and released == ["m1"]


async def test_delete_calls_platform_and_closes() -> None:
    q, bus, _, stub, *_ = make()
    add(q, 1)
    out = await q.handle(mod_action(1, "delete"))
    assert out.status == "ok" and out.state == "deleted" and stub.calls == [("delete", "m1")]
    follow = bus.of(Topic.CHAT_VERDICT)[-1]
    assert follow.action is ChatAction.DELETE and follow.platform_result == "done"


async def test_failed_delete_keeps_item_pending_and_reports_platform_result() -> None:
    q, bus, _, stub, *_ = make(backend=Stub(delete=ActionResult("failed", "http_500")))
    add(q, 1)
    out = await q.handle(mod_action(1, "delete"))
    assert out.status == "failed" and out.state == "pending" and out.detail == "http_500"
    assert q.get("twitch", "m1").state == "pending"  # type: ignore[union-attr]
    assert bus.of(Topic.CHAT_VERDICT)[-1].platform_result == "failed"
    assert (await q.handle(mod_action(1, "dismiss"))).status == "ok"  # moderator can still close it


async def test_unsupported_platform_action_reported() -> None:
    q, bus, *_ = make(backend=Stub(delete=ActionResult("unsupported", "no_connector")))
    add(q, 1)
    out = await q.handle(mod_action(1, "delete"))
    assert out.status == "unsupported" and bus.of(Topic.CHAT_VERDICT)[-1].platform_result == "unsupported"


async def test_timeout_passes_seconds_and_closes() -> None:
    q, bus, _, stub, *_ = make()
    add(q, 1)
    out = await q.handle(mod_action(1, "timeout", seconds=120))
    assert out.state == "timed_out" and stub.calls == [("timeout", "m1:120")]
    follow = bus.of(Topic.CHAT_VERDICT)[-1]
    assert follow.action is ChatAction.TIMEOUT and follow.timeout_seconds == 120


async def test_dismiss_closes_without_release_or_platform_call() -> None:
    q, _, _, stub, released, _ = make()
    add(q, 1)
    assert (await q.handle(mod_action(1, "dismiss"))).state == "dismissed"
    assert released == [] and stub.calls == []


async def test_unknown_item_and_concurrent_duplicate_action() -> None:
    stub = Stub()
    stub.gate = asyncio.Event()
    q, *_ = make(backend=stub)
    assert (await q.handle(mod_action(9, "delete"))).status == "unknown"
    add(q, 1)
    first = asyncio.ensure_future(q.handle(mod_action(1, "delete")))
    await asyncio.sleep(0.01)
    assert (await q.handle(mod_action(1, "delete"))).status == "busy"
    stub.gate.set()
    assert (await first).state == "deleted" and stub.calls == [("delete", "m1")]  # only one platform call


async def test_expired_item_cannot_be_acted_on() -> None:
    q, _, clock, stub, released, _ = make(ttl_s=60)
    add(q, 1)
    clock.advance(61)
    assert (await q.handle(mod_action(1, "approve"))).status == "unknown" and released == []
