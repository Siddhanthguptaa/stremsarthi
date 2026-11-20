"""YouTube poller against the fake API: normalisation, pacing, paging, duplicates, failures, quota."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from chat.common import Backoff
from chat.testing.waiting import until
from chat.youtube import YouTubePoller, find_live_chat_id
from contracts import ChatMessage

LIVE_PUBLISHED = "2023-11-14T22:13:00Z"  # within the ManualClock's "now" (2023-11-14T22:13:20Z)


def make_poller(yt: SimpleNamespace, **kwargs: object) -> tuple[YouTubePoller, list[ChatMessage], list[str]]:
    got: list[ChatMessage] = []
    statuses: list[str] = []
    kwargs.setdefault("live_chat_id", yt.fake.live_chat_id)
    kwargs.setdefault("backoff", Backoff(base=1, cap=60, jitter=0.0))
    poller = YouTubePoller(yt.client, got.append, clock=yt.clock, on_status=statuses.append, **kwargs)  # type: ignore[arg-type]
    return poller, got, statuses


async def run_until(poller: YouTubePoller, predicate: Callable[[], bool]) -> None:
    task = asyncio.ensure_future(poller.run())
    try:
        await until(predicate)
    finally:
        await poller.stop()
        await asyncio.wait_for(task, 2)


async def test_normalises_messages_roles_and_skips_system_events(yt: SimpleNamespace) -> None:
    fake = yt.fake
    fake.add_text("namaste", display="Asha", author="UCasha", published_at=LIVE_PUBLISHED, message_id="m1")
    fake.add_text("mod here", moderator=True, author="UCmod", published_at=LIVE_PUBLISHED, message_id="m2")
    fake.add_text("owner here", owner=True, author="UCown", published_at=LIVE_PUBLISHED, message_id="m3")
    fake.add_text(
        "thanks for the stream", kind="superChatEvent", published_at=LIVE_PUBLISHED, message_id="m4"
    )
    fake.add_event("messageDeletedEvent")
    fake.add_event("userBannedEvent")
    poller, got, _ = make_poller(yt)
    await run_until(poller, lambda: len(got) >= 4)
    by_id = {m.message_id: m for m in got}
    assert set(by_id) == {"m1", "m2", "m3", "m4"}  # the two system events are not messages
    assert (
        by_id["m1"].text == "namaste"
        and by_id["m1"].author_display == "Asha"
        and by_id["m1"].author_id == "UCasha"
    )
    assert by_id["m1"].platform == "youtube" and by_id["m1"].channel_id == fake.live_chat_id
    assert by_id["m2"].is_moderator and not by_id["m2"].is_broadcaster
    assert by_id["m3"].is_broadcaster
    assert by_id["m4"].text == "thanks for the stream"  # Super Chat treated as a message, amount ignored
    assert "namaste" not in repr(by_id["m1"])  # repr never prints text


async def test_honours_polling_interval_and_governor_pacing(yt: SimpleNamespace) -> None:
    yt.fake.polling_interval_ms = 12_000  # the server asks for more than the governor would
    yt.fake.add_text("hi", published_at=LIVE_PUBLISHED)
    poller, _, _ = make_poller(yt)
    await run_until(poller, lambda: poller.polls >= 4)
    waits = yt.clock.sleeps[:3]
    assert all(w == pytest.approx(12.0) for w in waits)

    yt2_fake_interval = 1000  # now the governor (6.75 s) is the larger one
    yt.fake.polling_interval_ms = yt2_fake_interval
    before = len(yt.clock.sleeps)
    poller2, _, _ = make_poller(yt)
    await run_until(poller2, lambda: poller2.polls >= 3)
    assert all(6.5 <= w <= 7.5 for w in yt.clock.sleeps[before : before + 2])


async def test_page_token_is_used_and_no_duplicates(yt: SimpleNamespace) -> None:
    fake = yt.fake
    ids = [fake.add_text(f"msg {i}", published_at=LIVE_PUBLISHED) for i in range(3)]
    poller, got, _ = make_poller(yt)
    task = asyncio.ensure_future(poller.run())
    try:
        await until(lambda: len(got) == 3)
        more = [fake.add_text(f"later {i}", published_at=LIVE_PUBLISHED) for i in range(2)]
        fake.items.append(fake.items[-1])  # the platform re-sends the last item: must not be delivered twice
        await until(lambda: len(got) == 5)
        await until(lambda: poller.polls >= 4)
    finally:
        await poller.stop()
        await asyncio.wait_for(task, 2)
    assert [m.message_id for m in got] == ids + more
    calls = fake.calls("GET", "/youtube/v3/liveChat/messages")
    assert "pageToken" not in calls[0].params
    assert all("pageToken" in c.params for c in calls[1:])
    assert all(c.params["part"] == "snippet,authorDetails" and c.params["maxResults"] == "200" for c in calls)


async def test_stale_backlog_on_first_page_is_skipped(yt: SimpleNamespace) -> None:
    yt.fake.add_text("old news", published_at="2023-11-14T20:00:00Z", message_id="old")
    yt.fake.add_text("fresh", published_at=LIVE_PUBLISHED, message_id="new")
    poller, got, _ = make_poller(yt)
    await run_until(poller, lambda: len(got) >= 1 and poller.polls >= 2)
    assert [m.message_id for m in got] == ["new"] and poller.skipped_backlog == 1


async def test_resumes_after_503_without_losing_or_duplicating(yt: SimpleNamespace) -> None:
    fake = yt.fake
    fake.add_text("before", published_at=LIVE_PUBLISHED, message_id="a")
    poller, got, _ = make_poller(yt)
    task = asyncio.ensure_future(poller.run())
    try:
        await until(lambda: len(got) == 1)
        fake.inject(503, reason="backendError", times=2, path="/youtube/v3/liveChat/messages")
        fake.add_text("during outage", published_at=LIVE_PUBLISHED, message_id="b")
        await until(lambda: len(got) == 2)
    finally:
        await poller.stop()
        await asyncio.wait_for(task, 2)
    assert [m.message_id for m in got] == ["a", "b"]
    assert 1.0 in yt.clock.sleeps and 2.0 in yt.clock.sleeps  # backoff ladder 1 s, 2 s
    assert poller.last_error_code == "yt_server_error"


async def test_rate_limit_403_backs_off_and_continues(yt: SimpleNamespace) -> None:
    yt.fake.add_text("x", published_at=LIVE_PUBLISHED, message_id="a")
    yt.fake.inject(403, reason="rateLimitExceeded", path="/youtube/v3/liveChat/messages")
    poller, got, _ = make_poller(yt)
    await run_until(poller, lambda: len(got) == 1)
    assert poller.last_error_code == "yt_rate_limited" and 1.0 in yt.clock.sleeps


async def test_invalid_page_token_restarts_from_live_edge(yt: SimpleNamespace) -> None:
    yt.fake.add_text("a", published_at=LIVE_PUBLISHED, message_id="a")
    poller, got, _ = make_poller(yt)
    task = asyncio.ensure_future(poller.run())
    try:
        await until(lambda: poller.polls >= 2)
        poller._page_token = "bogus"
        yt.fake.add_text("b", published_at=LIVE_PUBLISHED, message_id="b")
        await until(lambda: len(got) == 2)
    finally:
        await poller.stop()
        await asyncio.wait_for(task, 2)
    assert [m.message_id for m in got] == ["a", "b"]  # the re-read of "a" was de-duplicated


async def test_expired_access_token_refreshes_once_and_continues(yt: SimpleNamespace) -> None:
    yt.fake.add_text("a", published_at=LIVE_PUBLISHED, message_id="a")
    poller, got, _ = make_poller(yt)
    task = asyncio.ensure_future(poller.run())
    try:
        await until(lambda: len(got) == 1)
        yt.fake.expire_tokens()
        yt.fake.add_text("b", published_at=LIVE_PUBLISHED, message_id="b")
        await until(lambda: len(got) == 2)
    finally:
        await poller.stop()
        await asyncio.wait_for(task, 2)
    assert len([r for r in yt.fake.token_requests if r["grant_type"] == "refresh_token"]) == 1


async def test_chat_ended_finishes_the_connector(yt: SimpleNamespace) -> None:
    yt.fake.chat_ended = True
    poller, _, statuses = make_poller(yt)
    await asyncio.wait_for(poller.run(), 5)
    assert poller.state == "finished" and statuses == ["yt_chat_ended"]


async def test_chat_ended_event_inside_a_page_delivers_the_rest_first(yt: SimpleNamespace) -> None:
    yt.fake.add_text("last words", published_at=LIVE_PUBLISHED, message_id="z")
    yt.fake.add_event("chatEndedEvent")
    poller, got, statuses = make_poller(yt)
    await asyncio.wait_for(poller.run(), 5)
    assert [m.message_id for m in got] == ["z"] and statuses == ["yt_chat_ended"]


async def test_unauthorised_403_is_fatal(yt: SimpleNamespace) -> None:
    yt.fake.inject(403, reason="forbidden", path="/youtube/v3/liveChat/messages")
    poller, _, statuses = make_poller(yt)
    await asyncio.wait_for(poller.run(), 5)
    assert poller.state == "failed" and statuses == ["yt_forbidden"]


async def test_quota_exceeded_pauses_reads_until_pacific_reset_then_resumes(yt: SimpleNamespace) -> None:
    yt.fake.add_text("a", published_at=LIVE_PUBLISHED, message_id="a")
    yt.fake.inject(403, reason="quotaExceeded", path="/youtube/v3/liveChat/messages", times=1)
    poller, got, _ = make_poller(yt, backlog_max_age_s=1e9)  # the message is ~10 virtual hours old by then
    list_path = "/youtube/v3/liveChat/messages"
    marks: dict[str, float] = {}

    def on_event(code: str) -> None:  # runs synchronously inside mark_exhausted, before any later poll
        if code == "yt_quota_exhausted" and not marks:
            marks.update(
                calls=len(yt.fake.calls("GET", list_path)),
                wall=yt.clock.wall(),
                reset=yt.governor.seconds_until_reset(),
            )
        yt.statuses.append(code)

    yt.governor._on_event = on_event
    task = asyncio.ensure_future(poller.run())
    try:
        await until(lambda: len(got) == 1)  # after midnight Pacific the counters reset and polling resumes
    finally:
        await poller.stop()
        await asyncio.wait_for(task, 2)
    assert 300.0 in yt.clock.sleeps  # it waited in bounded slices, not one long sleep
    first_after = yt.fake.calls("GET", list_path)[marks["calls"]]
    assert first_after.at >= marks["wall"] + marks["reset"]  # no read before the quota day rolled over
    assert yt.governor.used_total < 200


async def test_three_hour_stream_stays_inside_the_daily_budget(yt: SimpleNamespace) -> None:
    """A planned 3 h stream polls at ~6.75 s: about 1,600 lists = 8,000 units, never a 403 from the fake."""
    fake = yt.fake
    for i in range(20):
        fake.add_text(f"m{i}", published_at=LIVE_PUBLISHED, message_id=f"m{i}")
    poller, got, _ = make_poller(yt, live_chat_id=None)
    started = yt.clock.monotonic()

    def on_event(code: str) -> None:
        yt.statuses.append(code)
        if code == "yt_quota_exhausted":
            poller._stop.set()  # virtual time is instant: stop before the idle wait reaches the midnight reset

    yt.governor._on_event = on_event
    task = asyncio.ensure_future(poller.run())
    try:
        await until(lambda: yt.clock.monotonic() - started >= 3 * 3600 or poller.stopped)
    finally:
        await poller.stop()
        await asyncio.wait_for(task, 2)
    assert fake.quota_denied == 0
    assert fake.units_used <= 8000, fake.units_used
    assert fake.units_used >= 7900, fake.units_used  # and it really uses its read budget
    assert 1580 <= poller.polls <= 1600, poller.polls
    poll_sleeps = [s for s in yt.clock.sleeps if s > 1]
    steady = poll_sleeps[:1500]
    assert min(steady) >= 6.7 and max(steady) <= 6.9  # steady pace until the plan is nearly over
    assert not any(s == float("inf") for s in yt.clock.sleeps)
    assert "unexpected" not in poller.last_error_code
    assert len(got) == 20 and yt.governor.used_total <= yt.governor.daily_units


async def test_live_chat_id_lookup_retries_until_broadcast_is_live(yt: SimpleNamespace) -> None:
    yt.fake.broadcast_active = False
    task = asyncio.ensure_future(find_live_chat_id(yt.client, clock=yt.clock, retry_s=10.0))
    await until(lambda: len(yt.fake.calls("GET", "/youtube/v3/liveBroadcasts")) >= 3)
    assert not task.done() and yt.clock.sleeps[:2] == [10.0, 10.0]
    yt.fake.broadcast_active = True
    assert await asyncio.wait_for(task, 5) == yt.fake.live_chat_id
    lookups = yt.fake.calls("GET", "/youtube/v3/liveBroadcasts")
    assert all(set(c.params) >= {"broadcastStatus"} and "mine" not in c.params for c in lookups)
    assert all(c.cost == 1 for c in lookups)


async def test_consumer_exception_does_not_stop_polling(yt: SimpleNamespace) -> None:
    yt.fake.add_text("a", published_at=LIVE_PUBLISHED, message_id="a")
    yt.fake.add_text("b", published_at=LIVE_PUBLISHED, message_id="b")
    seen: list[str] = []

    def consumer(message: ChatMessage) -> None:
        seen.append(message.message_id)
        if message.message_id == "a":
            raise RuntimeError("boom with viewer text: " + message.text)

    poller = YouTubePoller(yt.client, consumer, live_chat_id=yt.fake.live_chat_id, clock=yt.clock)
    await run_until(poller, lambda: seen == ["a", "b"])
