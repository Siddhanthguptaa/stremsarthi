"""The scripted demo chat: script sanity, and the real chat shield on the mock YouTube chat (rules only, fast clock)."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from eval.demo import chat_feeder as cf


def test_script_is_valid_and_covers_every_demo_message_kind() -> None:
    assert cf.validate() == []
    kinds = {i.kind for i in cf.SCRIPT}
    assert kinds >= {"friendly", "abuse", "abuse_deva", "scam", "doxxing", "staff", "hold", "unhold"}
    hold = next(i for i in cf.SCRIPT if i.kind == "hold")
    assert hold.text == "!hold" and hold.role == "moderator"
    assert next(i for i in cf.SCRIPT if i.kind == "unhold").role == "owner"
    assert any("iska ghar kahan hai" in i.text for i in cf.SCRIPT if i.kind == "doxxing")
    assert any(i.text.startswith("Congrats") and "upi://pay" in i.text for i in cf.SCRIPT if i.kind == "scam")
    assert cf.plan()[0]["t"] == cf.SCRIPT[0].t


def test_validate_catches_disorder_and_unknowns() -> None:
    bad = (cf.FeedItem(5.0, "friendly", "a", "x"), cf.FeedItem(1.0, "nonsense", "b", "y", "admin"))
    assert len(cf.validate(bad)) == 2  # out of order, unknown kind or role
    assert cf.validate((cf.FeedItem(95.0, "friendly", "a", "x"),))


def test_print_plan_cli(capsys: pytest.CaptureFixture[str]) -> None:
    assert cf.main(["--print-plan"]) == 0
    out = capsys.readouterr().out
    assert '"kind": "hold"' in out and "iska ghar kahan hai" in out


async def test_feed_posts_in_order_with_roles() -> None:
    from eval.mock_platforms import YouTubeMock  # noqa: PLC0415

    mock = YouTubeMock()
    items = (cf.FeedItem(0.0, "friendly", "A", "one"), cf.FeedItem(0.05, "hold", "M", "!hold", "moderator"))
    assert await cf.feed(mock, items) == 2
    texts = [i["snippet"]["textMessageDetails"]["messageText"] for i in mock.items]
    assert texts == ["one", "!hold"]
    assert (
        mock.items[1]["authorDetails"]["isChatModerator"]
        and not mock.items[0]["authorDetails"]["isChatModerator"]
    )


async def test_real_chat_process_on_the_scripted_mock_hides_flagged_and_publishes_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contracts import ChatAction, Config, Topic  # noqa: PLC0415
    from contracts.fakes import InProcessBus  # noqa: PLC0415

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    bus = InProcessBus()
    sub = bus.subscribe([Topic.CHAT_ALLOWED, Topic.CHAT_VERDICT, Topic.HOLD])
    cfg = cf.demo_config(Config(groq_api_key=None), rules_only=True, min_poll_s=0.5)
    assert cfg.chat.enabled and cfg.chat.platforms == ["youtube"]
    task = asyncio.ensure_future(
        cf.serve(config=cfg, bus=bus, time_scale=45.0, start_delay_s=0.0, poll_ms=500, install_signals=False)
    )
    seen: list[tuple[object, object]] = []

    def drain() -> int:
        while len(sub):
            seen.append(sub.get_nowait())
        return len(seen)

    holds = 0
    try:
        for _ in range(150):  # up to 30 s; the script takes 2 s on the fast clock
            await asyncio.sleep(0.2)
            drain()
            holds = sum(1 for t, _e in seen if t == Topic.HOLD)
            if holds >= 2 and len({e.message_id for t, e in seen if t == Topic.CHAT_VERDICT}) >= 15:
                break
        await asyncio.sleep(0.5)
        drain()
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    holds = sum(1 for t, _e in seen if t == Topic.HOLD)
    assert holds == 2, "the moderator !hold and the broadcaster !unhold must both reach the bus"
    verdicts = {e.message_id: e for t, e in seen if t == Topic.CHAT_VERDICT}
    allowed = {e.message_id for t, e in seen if t == Topic.CHAT_ALLOWED}
    flagged = {mid for mid, v in verdicts.items() if v.action != ChatAction.ALLOW}
    bad = {
        f"MSG_SPECIMEN_{n + 1:06d}"
        for n, i in enumerate(cf.SCRIPT)
        if i.kind in ("abuse", "abuse_deva", "scam", "doxxing")
    }
    assert bad <= flagged, sorted(bad - flagged)  # every scripted bad message is caught (rules only)
    assert not flagged & allowed  # nothing flagged reaches the overlay
    friendly = {
        f"MSG_SPECIMEN_{n + 1:06d}" for n, i in enumerate(cf.SCRIPT) if i.kind in ("friendly", "staff")
    }
    assert friendly <= allowed, sorted(friendly - allowed)
