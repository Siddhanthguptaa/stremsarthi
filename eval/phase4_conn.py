"""Gate 4 connector legs: the real YouTube and Twitch connectors, the real ``ChatPipeline`` and the real actions,
against ``eval.mock_platforms`` over loopback, with a ManualClock (virtual time) for backoff and quota pacing.

Every check is recorded as ``{"name", "value", "op", "target", "note"}``; a boolean check is ``1.0 >= 1.0``.
Nothing here prints or stores message text: the log scan counts leaks only.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiohttp

from .data_gen.chat.record import Msg
from .data_gen.chat.suite import load
from .mock_platforms import SCOPES, TwitchMock, YouTubeMock
from .phase4 import chat_config, chat_message, install_capture, scan_logs

N_YT = 250  # more than one page of 200
N_TW = 160


class Checks:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def ok(self, name: str, cond: bool, note: str = "") -> None:
        self.items.append(
            {"name": name, "value": 1.0 if cond else 0.0, "op": ">=", "target": 1.0, "note": note}
        )

    def num(
        self, name: str, value: float | None, op: str, target: float, unit: str = "", note: str = ""
    ) -> None:
        self.items.append(
            {"name": name, "value": value, "op": op, "target": target, "unit": unit, "note": note}
        )


async def until(cond: Any, timeout: float = 20.0, step: float = 0.01) -> bool:  # noqa: ASYNC109
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(step)
    return bool(cond())


async def settle(pipe: Any, timeout: float = 30.0) -> None:  # noqa: ASYNC109
    await pipe.idle(timeout)
    tasks = getattr(pipe.classifier, "_tasks", set())
    await until(lambda: not tasks, timeout)
    await pipe.idle(timeout)


class CallGate:
    """Lets the harness read two counters that are updated at different moments of one API call.

    The quota governor charges a call when it starts (``YouTubeClient.call``), the mock server counts it when the
    request arrives. With the poller running on the virtual clock there is nearly always one list call between the
    two moments, so comparing the counters mid-run reads 5 units apart (measured: 1911 vs 1906, one list in
    flight, on every run). ``quiescent()`` stops new calls from starting, waits for the in-flight ones to finish,
    and only then lets the caller read. Equality is asserted exactly at that point: no tolerance, so a governor
    that really mischarges still fails."""

    def __init__(self, client: Any) -> None:
        self._orig = client.call
        self._open = asyncio.Event()
        self._open.set()
        self._idle = asyncio.Event()
        self._idle.set()
        self.inflight = 0
        client.call = self._call

    async def _call(self, *args: Any, **kwargs: Any) -> Any:
        await self._open.wait()
        self.inflight += 1  # no await between the gate check and here: the count cannot be missed
        self._idle.clear()
        try:
            return await self._orig(*args, **kwargs)
        finally:
            self.inflight -= 1
            if self.inflight == 0:
                self._idle.set()

    @asynccontextmanager
    async def quiescent(self, timeout: float = 15.0) -> AsyncIterator[None]:  # noqa: ASYNC109
        self._open.clear()
        try:
            await asyncio.wait_for(self._idle.wait(), timeout)
            yield
        finally:
            self._open.set()


async def units_at_rest(gate: CallGate, governor: Any, mock: Any) -> tuple[int, int]:
    """(governor units, server units) read while no API call is in flight."""
    async with gate.quiescent():
        return int(governor.used_total), int(mock.units_used)


def subset(msgs: list[Msg], n: int) -> list[Msg]:
    step = max(1, len(msgs) // n)
    return msgs[::step][:n]


def outcome_sets(bus: Any, by_id: dict[str, Msg]) -> dict[str, Any]:
    from contracts import ChatAction, Topic  # noqa: PLC0415

    final: dict[str, Any] = {}
    for v in bus.of(Topic.CHAT_VERDICT):
        final[v.message_id] = v
    deleted = {i for i, v in final.items() if v.action is ChatAction.DELETE and v.platform_result == "done"}
    timed = {i for i, v in final.items() if v.action is ChatAction.TIMEOUT and v.platform_result == "done"}
    return {"final": final, "deleted": deleted, "timed": timed}


# =============================================================================================================== YouTube
async def youtube_leg(msgs: list[Msg], private: dict[str, Any], defect: str) -> dict[str, Any]:
    from chat.common import ManualClock, Secret  # noqa: PLC0415
    from chat.pipeline import ChatPipeline  # noqa: PLC0415
    from chat.youtube import (
        IdMap,
        QuotaGovernor,
        YouTubeActions,
        YouTubeAuth,
        YouTubeClient,
        YouTubePoller,
        YouTubeSession,
        make_http,
    )  # noqa: PLC0415
    from chat.youtube.auth import YouTubeTokens  # noqa: PLC0415
    from chat.youtube.governor import seconds_until_pacific_midnight  # noqa: PLC0415
    from contracts.config import YoutubeChatConfig  # noqa: PLC0415
    from contracts.fakes import InProcessBus  # noqa: PLC0415

    cap = install_capture()
    chk = Checks()
    clock = ManualClock()
    mock = YouTubeMock(now=clock.wall)
    await mock.start()
    statuses: list[str] = []
    sample = subset([m for m in msgs if not m.staff and m.label != "doxxing_staff"], N_YT)
    by_mock: dict[str, Msg] = {}
    for m in sample:
        by_mock[mock.add_message(m.text, "UC_" + m.author, "viewer")] = m
    numbers: dict[str, Any] = {"messages_sent": len(sample)}
    try:
        async with aiohttp.ClientSession() as aio:
            http = make_http(aio, loopback_origin=mock.origin)
            auth = YouTubeAuth(mock.client_id, Secret("specimen-secret"), http, clock=clock)
            tokens = YouTubeTokens(
                Secret(mock.issue_access_token()), Secret(mock.refresh_token), clock.monotonic() + 3600
            )
            gov = QuotaGovernor(clock=clock, on_event=statuses.append)
            client = YouTubeClient(http, YouTubeSession(auth, tokens, clock=clock), gov)
            bus = InProcessBus()
            cfg = chat_config(private, llm=False).model_copy(
                update={"youtube": YoutubeChatConfig(allow_timeout=False)}
            )  # the default: a ban costs 200 of the 2,000 action units
            ids = IdMap()
            holder: dict[str, Any] = {}
            gate = CallGate(client)  # before the poller and the actions start calling
            actions = YouTubeActions(
                client,
                get_live_chat_id=lambda: holder["poller"].live_chat_id,
                ids=ids,
                allow_timeout=False,
                clock=clock,
                on_status=statuses.append,
            )
            pipe = ChatPipeline(bus, cfg, youtube=actions, clock=clock)  # type: ignore[arg-type]
            poller = YouTubePoller(client, pipe.on_message, ids=ids, clock=clock, on_status=statuses.append)
            holder["poller"] = poller
            pipe.start()
            task = asyncio.create_task(poller.run())
            got = await until(lambda: poller.delivered >= len(sample), 30.0)
            chk.ok(
                "YouTube: broadcast found and every message delivered once",
                got and poller.live_chat_id == mock.live_chat_id and poller.delivered == len(sample),
                f"{poller.delivered}/{len(sample)}",
            )
            await settle(pipe)
            chk.ok("YouTube: pagination used (more than one list call)", mock.calls_by_kind["list"] >= 2)
            chk.num("YouTube: duplicates seen by the pipeline", float(pipe.counts["duplicates"]), "==", 0.0)
            chk.num(
                "YouTube: messages received by the pipeline",
                float(pipe.counts["received"]),
                "==",
                float(len(sample)),
            )

            # quota arithmetic (plan: 3 h of stream => 8,000 read units / 5 per list => 6.75 s between lists)
            g0 = QuotaGovernor(clock=ManualClock(), planned_stream_hours=3.0)
            g0.start_stream()
            chk.num("YouTube: planned 3 h poll interval", g0.read_interval(), "<=", 6.85, "s")
            chk.num("YouTube: planned 3 h poll interval (lower)", g0.read_interval(), ">=", 6.65, "s")
            gaps = [s for s in clock.sleeps if 5.0 < s < 20.0]
            if gaps:
                chk.num(
                    "YouTube: worst poll spacing deviation from 6.75 s",
                    max(abs(s - 6.75) for s in gaps),
                    "<=",
                    0.1,
                    "s",
                )
            else:
                chk.ok("YouTube: poll spacing observed", False, "no poll sleeps recorded")

            # delete / timeout round trip, as the pipeline decided
            res = outcome_sets(bus, by_mock)
            would_ban = {i for i, v in res["final"].items() if v.reason.endswith(":dg_timeout_off_delete")}
            chk.ok(
                "YouTube: every DELETE verdict reached the platform, nothing else was deleted",
                set(mock.deleted) == res["deleted"],
                f"verdict deletes {len(res['deleted'])}, platform deletes {len(mock.deleted)}",
            )
            chk.ok(
                "YouTube: timeouts off (default): TIMEOUT verdicts became deletes, no ban was sent",
                len(would_ban) >= 1 and not mock.bans and would_ban <= set(mock.deleted),
                f"{len(would_ban)} downgraded",
            )
            chk.ok(
                "YouTube: the round trip is not vacuous",
                len(mock.deleted) >= 20,
                f"{len(mock.deleted)} deletes",
            )
            numbers["clean_messages_deleted"] = sum(by_mock[i].label == "clean" for i in mock.deleted)
            gov_units, server_units = await units_at_rest(gate, gov, mock)
            chk.num(
                "YouTube: governor units equal the server's units",
                float(gov_units),
                "==",
                float(server_units),
                note="read with no API call in flight (CallGate): exact, no tolerance",
            )

            # direct action round trips (the adapter alone, on a fresh day's budget: the sample above spent most of it)
            from chat.common import ActionResult  # noqa: PLC0415

            numbers["action_units_spent_by_the_sample"] = gov.used_action
            gov = client.governor = QuotaGovernor(clock=clock, on_event=statuses.append)

            mid = mock.add_message("delete me", "UC_TARGET1")
            m1 = chat_message(Msg("delete me", "clean", "x", id=mid, author="UCTARGET1", platform="youtube"))
            r = await actions.delete(m1)
            chk.ok("YouTube: delete round trip", r == ActionResult("done", "ok") and mid in mock.deleted)
            r = await actions.delete(
                chat_message(Msg("gone", "clean", "x", id="NOPE_999", author="UCX", platform="youtube"))
            )
            chk.ok(
                "YouTube: deleting an unknown message reports 'gone'",
                r.status == "failed" and r.reason == "gone",
                f"{r}",
            )
            actions.allow_timeout = True
            nb = len(mock.bans)
            r = await actions.timeout(m1, 600)
            chk.ok(
                "YouTube: timeout round trip (temporary ban, 600 s)",
                r.status == "done" and len(mock.bans) == nb + 1 and mock.bans[-1]["seconds"] == 600,
            )
            chk.num(
                "YouTube: ban units charged (200 each)",
                float(mock.units_by_kind["ban"]),
                "==",
                200.0 * len(mock.bans),
            )
            mock.can_moderate = False
            m2 = chat_message(
                Msg("x", "clean", "x", id=mock.add_message("x", "UC_T2"), author="UCT2", platform="youtube")
            )
            r1 = await actions.delete(m2)
            calls = len(mock.calls)
            r2 = await actions.delete(m2)
            mock.can_moderate = True  # restored for the failure tests below
            chk.ok(
                "YouTube: missing permission stops actions after one try",
                r1.reason == "permissions" and r2.reason == "actions_stopped" and len(mock.calls) == calls,
                f"{r1.reason}, {r2.reason}",
            )
            # failure injection: 503 (retry), expired token (refresh), quota exceeded (pause until the reset)
            n_text = lambda: sum(1 for it in mock.items if it["snippet"]["type"] == "textMessageEvent")  # noqa: E731
            mock.inject("list", 503, "backendError", times=2)
            for i in range(5):
                mock.add_message(f"hello chat number {i} ok", f"UC_RETRY{i}")
            await until(lambda: poller.delivered >= n_text(), 20.0)
            chk.ok(
                "YouTube: 503s retried with backoff, no message lost or duplicated",
                poller.delivered == n_text() and poller.state == "running",
                f"{poller.delivered}/{n_text()}",
            )
            tr = mock.token_requests
            mock.expire_access_token()
            for i in range(3):
                mock.add_message(f"another ordinary line {i} bhai", f"UC_REFRESH{i}")
            await until(lambda: poller.delivered >= n_text(), 20.0)
            chk.ok(
                "YouTube: expired token refreshed once, polling continued",
                mock.token_requests > tr and poller.delivered == n_text(),
                f"+{mock.token_requests - tr} token calls",
            )
            n_list = mock.calls_by_kind["list"]
            mock.inject("list", 403, "quotaExceeded", times=1)
            await until(lambda: not gov.reads_enabled, 20.0)
            t_exhausted = clock.wall()
            reset_in = seconds_until_pacific_midnight(t_exhausted)
            resumed = await until(
                lambda: any(c.kind == "list" and c.status == 200 and c.at > t_exhausted for c in mock.calls),
                30.0,
            )  # the paused loop sleeps in virtual slices
            first_after = min(
                (c.at for c in mock.calls if c.kind == "list" and c.status == 200 and c.at > t_exhausted),
                default=None,
            )
            chk.ok(
                "YouTube: quotaExceeded pauses reads until the Pacific-midnight reset, then polling resumes",
                resumed and first_after is not None and first_after - t_exhausted >= reset_in - 1.0,
                f"resumed after {None if first_after is None else round(first_after - t_exhausted)} s, reset in {round(reset_in)} s",
            )
            _ = n_list
            await poller.stop()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)

            numbers.update(mock.quota_snapshot)
            numbers["governor_used"] = gov.used_total
            numbers["verdict_deletes"] = len(res["deleted"])
            numbers["platform_deletes"] = len(mock.deleted)
            numbers["bans"] = len(mock.bans)
            numbers["note"] = (
                "with allow_timeout=true the 6 severe-abuse bans of this sample alone cost 1,200 of the 2,000 action units"
            )
            await pipe.stop()
    finally:
        await mock.stop()
    secrets = [m.text for m in sample if m.label != "clean"]
    scan = scan_logs(cap, secrets)
    chk.num(
        "YouTube: log records containing flagged text",
        float(scan["leaks"]),
        "==",
        0.0,
        note=f"{scan['records']} records scanned",
    )
    return {"leg": "youtube", "checks": chk.items, "numbers": numbers, "log_scan": scan}


def _fallback_deleted(res: dict[str, Any], mock: Any) -> set[str]:
    """A TIMEOUT verdict that the platform could not honour falls back to a delete; those deletes are legitimate."""
    from contracts import ChatAction  # noqa: PLC0415

    return {i for i, v in res["final"].items() if v.action is ChatAction.DELETE and i in set(mock.deleted)}


def _author_label(channel: str, by_mock: dict[str, Msg]) -> str:
    for m in by_mock.values():
        if "UC_" + m.author == channel:
            return m.label
    return "unknown"


# =============================================================================================================== Twitch
async def twitch_leg(msgs: list[Msg], private: dict[str, Any], defect: str) -> dict[str, Any]:
    from chat.common import ManualClock  # noqa: PLC0415
    from chat.pipeline import ChatPipeline  # noqa: PLC0415
    from chat.twitch import ChatEvents, DeviceAuth, EventSubTransport, TwitchActions, UserSession, make_http  # noqa: PLC0415
    from contracts import Topic  # noqa: PLC0415
    from contracts.fakes import InProcessBus  # noqa: PLC0415

    cap = install_capture()
    chk = Checks()
    clock = ManualClock()
    mock = TwitchMock(keepalive_s=1)
    await mock.start()
    mock.approve()
    sample = subset([m for m in msgs if not m.staff and m.label != "doxxing_staff"], N_TW)
    third = len(sample) // 3
    parts = [sample[:third], sample[third : 2 * third], sample[2 * third :]]
    numbers: dict[str, Any] = {"messages_sent": len(sample)}
    sent_notifications = 0
    try:
        async with aiohttp.ClientSession() as aio:
            http = make_http(aio, loopback_origin=mock.origin)

            async def instant(seconds: float, cancel: asyncio.Event) -> None:
                if cancel.is_set():
                    raise asyncio.CancelledError
                await clock.sleep(seconds)

            auth = DeviceAuth(mock.client_id, http, clock=clock.monotonic, wait=instant)
            tokens = await auth.login(lambda prompt: None, asyncio.Event())
            session = UserSession(auth, tokens)
            await session.access_token()
            chk.ok("Twitch: device-code login and token validation", session.user_id == mock.user_id)
            bus = InProcessBus()
            cfg = chat_config(private, llm=False)
            actions = TwitchActions(http, session, mock.broadcaster_id, clock=clock)
            pipe = ChatPipeline(bus, cfg, twitch=actions, clock=clock)  # type: ignore[arg-type]
            sink = pipe.publisher.status_sink("twitch")
            events = ChatEvents(mock.broadcaster_id, pipe.submit, sink, mock=True)
            transport = EventSubTransport(
                events, session, http, aio, broadcaster_id=mock.broadcaster_id, url=mock.ws_url, mock=True,
                keepalive_grace_s=0.4, welcome_timeout_s=3.0, retire_grace_s=0.2, clock=clock, on_status=sink,
            )  # fmt: skip
            pipe.start()
            task = asyncio.create_task(transport.run())
            chk.ok(
                "Twitch: connected, subscribed and ready",
                await until(lambda: events.phase == "ready", 10.0),
                f"phase {events.phase}",
            )

            async def deliver(batch: list[Msg], repeat: int = 1) -> None:
                nonlocal sent_notifications
                for m in batch:
                    await mock.send_chat(m.text, m.author, "viewer", message_id=m.id, repeat=repeat)
                    sent_notifications += repeat

            await deliver(parts[0])
            await until(lambda: pipe.counts["received"] >= len(parts[0]), 20.0)
            await settle(pipe)
            chk.ok(
                "Twitch: first batch delivered once each",
                pipe.counts["received"] == len(parts[0]),
                f"{pipe.counts['received']}/{len(parts[0])}",
            )

            # abrupt drop: reconnect, no duplicates, no HOLD asserted, status codes reported
            holds_before = len(bus.of(Topic.HOLD))
            dropped = mock.drop()
            reconnected = await until(
                lambda: transport.sessions_started >= 2 and events.phase == "ready", 15.0
            )
            chk.ok(
                "Twitch: abrupt socket drop, reconnect and resubscribe",
                dropped >= 1 and reconnected,
                f"sessions {transport.sessions_started}",
            )
            codes = [e.detail_code for e in bus.of(Topic.SAFETY_EVENT)]
            chk.ok(
                "Twitch: twitch_disconnected then twitch_reconnected reported",
                "twitch_disconnected" in codes
                and "twitch_reconnected" in codes
                and codes.index("twitch_disconnected") < codes.index("twitch_reconnected"),
            )
            chk.num(
                "Twitch: HOLD commands asserted by a connection drop",
                float(len(bus.of(Topic.HOLD)) - holds_before),
                "==",
                0.0,
            )
            await deliver(parts[1], repeat=2)  # Twitch may redeliver: each message arrives twice
            await until(lambda: pipe.counts["received"] >= len(parts[0]) + len(parts[1]), 20.0)

            # session_reconnect handover: no outage reported, messages keep flowing
            disc = codes.count("twitch_disconnected")
            conn = mock.connections
            await mock.send_reconnect()
            await until(lambda: mock.connections > conn and events.phase == "ready", 10.0)
            await asyncio.sleep(0.6)  # the old socket is retired after its grace
            await deliver(parts[2])
            await until(lambda: pipe.counts["received"] >= len(sample), 20.0)
            await settle(pipe)
            codes = [e.detail_code for e in bus.of(Topic.SAFETY_EVENT)]
            chk.ok(
                "Twitch: session_reconnect handover without an outage",
                mock.connections == conn + 1 and codes.count("twitch_disconnected") == disc,
                f"connections +{mock.connections - conn}",
            )
            chk.num(
                "Twitch: messages received by the pipeline (each once)",
                float(pipe.counts["received"]),
                "==",
                float(len(sample)),
            )
            chk.num(
                "Twitch: duplicate notifications that got past the connector",
                float(pipe.counts["duplicates"]),
                "==",
                0.0,
                note=f"{sent_notifications} notifications sent for {len(sample)} messages",
            )

            res = outcome_sets(bus, {m.id: m for m in sample})
            chk.ok(
                "Twitch: every DELETE verdict, and every banned message, reached Helix as a delete; nothing else was deleted",
                set(mock.deleted) == res["deleted"] | res["timed"],
                f"verdict deletes {len(res['deleted'])}, bans {len(res['timed'])}, platform deletes {len(mock.deleted)}",
            )
            by_id = {m.id: m for m in sample}
            chk.ok(
                "Twitch: every TIMEOUT verdict became a ban with a duration",
                {b["user_id"] for b in mock.bans} == {by_id[i].author for i in res["timed"]}
                and all(b["duration"] for b in mock.bans),
                f"{len(mock.bans)} bans",
            )
            numbers["clean_messages_deleted"] = sum(by_id[i].label == "clean" for i in mock.deleted)
            chk.ok(
                "Twitch: the round trip is not vacuous",
                len(mock.deleted) >= 15 and len(mock.bans) >= 1,
                f"{len(mock.deleted)} deletes, {len(mock.bans)} bans",
            )

            # !hold: only the broadcaster and moderators
            clock.advance(30.0)
            holds = lambda: [(e.active) for e in bus.of(Topic.HOLD)]  # noqa: E731

            async def say(text: str, who: str, badges: tuple[str, ...], mid: str, repeat: int = 1) -> None:
                await mock.send_chat(text, who, who, badges=badges, message_id=mid, repeat=repeat)
                await asyncio.sleep(0.15)

            base = len(holds())
            await say("!hold", "7001", (), "cmd_viewer1")
            chk.num("Twitch: !hold from a viewer", float(len(holds()) - base), "==", 0.0)
            await say("!hold", "7002", ("moderator",), "cmd_mod1", repeat=2)
            chk.ok(
                "Twitch: !hold from a moderator accepted once (redelivery ignored)",
                holds()[base:] == [True],
                f"{holds()[base:]}",
            )
            clock.advance(10.0)
            await say("!unhold", "7002", ("moderator",), "cmd_mod2")
            chk.ok("Twitch: !unhold from a moderator refused", holds()[base:] == [True])
            clock.advance(10.0)
            await say("!unhold", "1001", ("broadcaster",), "cmd_bc1")
            chk.ok("Twitch: !unhold from the broadcaster accepted", holds()[base:] == [True, False])
            clock.advance(10.0)
            await say("!hold", "1001", ("broadcaster",), "cmd_bc2")
            chk.ok("Twitch: !hold from the broadcaster accepted", holds()[base:] == [True, False, True])
            chk.num(
                "Twitch: command text reaching the overlay",
                float(sum(m.text.startswith("!") for m in bus.of(Topic.CHAT_ALLOWED))),
                "==",
                0.0,
            )

            # a scope lost after validation: Helix answers 401, the message must stay hidden (never ALLOW)
            mock.lose_scope("moderator:manage:chat_messages")
            victim = next(m for m in msgs if m.label == "scam" and m.kind == "giveaway" and m.id not in by_id)
            vm = Msg(
                victim.text, victim.label, victim.kind, id="late_scam_1", author="u9999", platform="twitch"
            )
            await mock.send_chat(vm.text, vm.author, "viewer", message_id=vm.id)
            await until(lambda: any(v.message_id == vm.id for v in bus.of(Topic.CHAT_VERDICT)), 10.0)
            await settle(pipe)
            last = [v for v in bus.of(Topic.CHAT_VERDICT) if v.message_id == vm.id][-1]
            shown = any(m.message_id == vm.id for m in bus.of(Topic.CHAT_ALLOWED))
            chk.ok(
                "Twitch: delete refused (missing scope) downgrades to a moderator hold, never ALLOW",
                last.action.value in ("hold_for_mod", "hide_from_streamer")
                and not shown
                and vm.id not in mock.deleted,
                f"final action {last.action.value}, reason {last.reason[:40]}",
            )
            numbers.update(
                {
                    "connections": mock.connections, "subscriptions": mock.subscriptions, "notifications_sent": sent_notifications,
                    "verdict_deletes": len(res["deleted"]), "platform_deletes": len(mock.deleted), "bans": len(mock.bans),
                    "helix_calls": dict(Counter(f"{m} {p} {s}" for m, p, s in mock.helix_calls)),
                }
            )  # fmt: skip
            await transport.stop()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
            await pipe.stop()
    finally:
        await mock.stop()
    secrets = [m.text for m in sample if m.label != "clean"]
    scan = scan_logs(cap, secrets)
    chk.num(
        "Twitch: log records containing flagged text",
        float(scan["leaks"]),
        "==",
        0.0,
        note=f"{scan['records']} records scanned",
    )
    _ = SCOPES
    return {"leg": "twitch", "checks": chk.items, "numbers": numbers, "log_scan": scan}


async def run(leg: str, data: Any, defect: str) -> dict[str, Any]:
    manifest, msgs = load(data)
    if leg == "youtube":
        return await youtube_leg(msgs, manifest["streamer_private"], defect)
    return await twitch_leg(msgs, manifest["streamer_private"], defect)
