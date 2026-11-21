"""Scripted chat for the judge demo: the mock YouTube live chat plus the real chat shield process, in one command.

``python -m chat.main`` cannot be pointed at a fake: the connector's loopback origin and the token source are
``LiveOptions`` fields, not environment variables. So this module is a drop-in replacement for that process. It
starts ``eval.mock_platforms.YouTubeMock`` on a loopback port, feeds it a scripted ~90 s chat, and runs the real
``chat.main.amain`` (the same ``ChatService``, classifier, moderator queue, hold command and bus wiring) with the
YouTube connector aimed at the mock. Flagged messages go to the moderator console through the bus exactly as live
chat does; nothing leaves the machine (loopback only; the classifier uses Groq only when a key is in ``.env``,
``--rules-only`` forbids it).

Start it where the API would start ``chat.main``::

    SESSION_CHAT_CMD="python -m eval.demo.chat_feeder --profile {profile}"   # API env: the API spawns it on go-live
    python -m eval.demo.chat_feeder --profile demo                            # or by hand, next to the engine

The bus endpoints are the API's (``BUS_SUB_ENDPOINT`` / ``BUS_PUB_ENDPOINT``). The script starts ``--start-delay``
seconds after launch (default 3); chat reaches viewers about 5 s after the video (the broadcast delay), so start it a
few seconds after the sender. ``--time-scale 10`` plays the script 10x faster (tests); ``--print-plan`` prints the
schedule (times, kinds, never anything but SPECIMEN text) and exits; ``--loop`` replays the script.

The script (times in seconds): friendly chat, Hinglish abuse in Latin letters and in Devanagari, a fake UPI payment
scam link, a link-shortener "free recharge" scam, a doxxing attempt ("iska ghar kahan hai"), a staff message from a
moderator, a moderator ``!hold`` at 84 s and the broadcaster's ``!unhold`` at 88 s. The ``!hold`` lands in the calm
scene of the reel, after the music bed, so it does not mask the PII moments. Chat must be enabled for the profile
(``chat.enabled: true``); this module forces ``chat.platforms = ["youtube"]`` and a placeholder OAuth client id.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..mock_platforms import YouTubeMock


@dataclass(frozen=True)
class FeedItem:
    t: float  # seconds after the feed starts
    kind: str  # friendly | abuse | abuse_deva | scam | doxxing | staff | hold | unhold
    author: str
    text: str
    role: str = "viewer"  # viewer | moderator | owner


SCRIPT: tuple[FeedItem, ...] = (
    FeedItem(1.0, "friendly", "Priya", "Namaste bhaiya, stream bahut badhiya hai"),
    FeedItem(5.0, "friendly", "Rahul", "Aadhaar card wala scene mast hai, mask dekh ke maza aaya"),
    FeedItem(10.0, "friendly", "Amit", "awaaz clear aa rahi hai, quality ekdum sahi"),
    FeedItem(15.0, "abuse", "Troll1", "tu bilkul madarchod chutiya hai bhenchod"),
    FeedItem(
        21.0,
        "scam",
        "Prize",
        "Congrats! Aap jeet gaye Rs 50000. Claim karne ke liye upi://pay?pa=prize.claim@ybl&am=1&cu=INR pe Rs 99 bhejo",
    ),  # fmt: skip
    FeedItem(27.0, "staff", "StreamMod", "Welcome everyone, please keep the chat respectful", "moderator"),
    FeedItem(33.0, "friendly", "Sneha", "number plate wala scene dekha? kya baat hai"),
    FeedItem(39.0, "abuse_deva", "Troll2", "तू बिल्कुल चूतिया है मादरचोद"),
    FeedItem(44.0, "doxxing", "Stalker", "iska ghar kahan hai bhai, address batao"),
    FeedItem(50.0, "friendly", "Karan", "OTP wala phone scene great tha"),
    FeedItem(
        58.0, "scam", "Recharge", "free recharge milega, click karo bit.ly/free-recharge-99 abhi, sirf aaj"
    ),
    FeedItem(66.0, "friendly", "Neha", "enrolled face visible hai aur stranger blur, bahut sahi"),
    FeedItem(74.0, "friendly", "Dev", "profanity wala warning bhi dikha, strike guard mast hai"),
    FeedItem(81.0, "friendly", "Isha", "music wala part bhi pakda gaya?"),
    FeedItem(84.0, "hold", "StreamMod", "!hold", "moderator"),
    FeedItem(88.0, "unhold", "Streamer", "!unhold", "owner"),
    FeedItem(89.5, "friendly", "Priya", "great demo, thanks streamer"),
)
SCRIPT_SECONDS = 90.0
DEFAULT_START_DELAY_S = 3.0
POLL_MS = 2000


def plan(items: Sequence[FeedItem] = SCRIPT) -> list[dict[str, Any]]:
    return [{"t": i.t, "kind": i.kind, "author": i.author, "role": i.role, "text": i.text} for i in items]


def validate(items: Sequence[FeedItem] = SCRIPT) -> list[str]:
    """Problems with a script (empty = fine): sorted, inside the reel, known kinds and roles."""
    problems = []
    kinds = {"friendly", "abuse", "abuse_deva", "scam", "doxxing", "staff", "hold", "unhold"}
    last = -1.0
    for n, it in enumerate(items):
        if it.t < last:
            problems.append(f"item {n} is out of order")
        if not 0 <= it.t <= SCRIPT_SECONDS:
            problems.append(f"item {n} is outside the reel")
        if it.kind not in kinds or it.role not in {"viewer", "moderator", "owner"}:
            problems.append(f"item {n} has an unknown kind or role")
        last = it.t
    return problems


def post(mock: YouTubeMock, it: FeedItem) -> str:
    """Add one scripted message to the mock's live chat; returns its message id."""
    return mock.add_message(
        it.text,
        author_id=f"UC_SPECIMEN_{it.author.upper()}",
        display=it.author,
        owner=it.role == "owner",
        moderator=it.role == "moderator",
    )


async def feed(
    mock: YouTubeMock,
    items: Sequence[FeedItem] = SCRIPT,
    *,
    time_scale: float = 1.0,
    start_delay_s: float = 0.0,
    loop: bool = False,
) -> int:
    """Post the script in real time (divided by ``time_scale``). Returns the number of messages posted."""
    posted = 0
    while True:
        origin = time.monotonic() + start_delay_s / time_scale
        for it in items:
            wait = origin + it.t / time_scale - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            post(mock, it)
            posted += 1
        if not loop:
            return posted
        await asyncio.sleep(max(0.0, origin + SCRIPT_SECONDS / time_scale - time.monotonic()))
        start_delay_s = 0.0


def demo_config(base: Any, *, rules_only: bool = False, min_poll_s: float | None = None) -> Any:
    """``base`` (a loaded ``Config``) with chat on, YouTube only, and a placeholder OAuth client id for the mock."""
    from pydantic import SecretStr  # noqa: PLC0415

    yt = base.chat.youtube
    if min_poll_s is not None:
        yt = yt.model_copy(update={"min_poll_s": min_poll_s})
    chat = base.chat.model_copy(update={"enabled": True, "platforms": ["youtube"], "youtube": yt})
    update: dict[str, Any] = {"chat": chat, "youtube_oauth_client_id": SecretStr("specimen-client-id")}
    if rules_only:
        update["groq_api_key"] = None
    return base.model_copy(update=update)


async def serve(
    *,
    config: Any,
    bus: Any = None,
    items: Sequence[FeedItem] = SCRIPT,
    time_scale: float = 1.0,
    start_delay_s: float = DEFAULT_START_DELAY_S,
    loop: bool = False,
    poll_ms: int = POLL_MS,
    log_level: str = "INFO",
    install_signals: bool = True,
    on_ready: Any = None,
) -> int:
    """Mock server + scripted feed + the real chat process. Returns the chat exit code."""
    from chat.common import Secret  # noqa: PLC0415
    from chat.live import LiveOptions  # noqa: PLC0415
    from chat.main import amain  # noqa: PLC0415
    from chat.youtube.auth import YouTubeTokens  # noqa: PLC0415

    mock = YouTubeMock(polling_interval_ms=poll_ms)
    origin = await mock.start()
    access = mock.issue_access_token()

    async def tokens(_auth: object) -> YouTubeTokens:
        return YouTubeTokens(Secret(access), Secret(mock.refresh_token), time.monotonic() + 6 * 3600)

    options = LiveOptions(youtube_origin=origin, youtube_tokens=tokens)
    feeder = asyncio.get_running_loop().create_task(
        feed(mock, items, time_scale=time_scale, start_delay_s=start_delay_s, loop=loop), name="chat-feeder"
    )
    if on_ready is not None:
        on_ready(mock)
    try:
        return await amain(
            ["--log-level", log_level],
            bus=bus,
            config=config,
            options=options,
            install_signals=install_signals,
        )
    finally:
        feeder.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await feeder
        await mock.stop()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m eval.demo.chat_feeder", description=__doc__.split("\n\n")[0])
    p.add_argument("--profile", choices=("dev", "demo"), default=None)
    p.add_argument("--config-dir", default="config/profiles")
    p.add_argument("--env-file", default=".env")
    p.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    p.add_argument(
        "--start-delay", type=float, default=DEFAULT_START_DELAY_S, help="seconds before message 0"
    )
    p.add_argument("--time-scale", type=float, default=1.0, help="play the script this many times faster")
    p.add_argument("--loop", action="store_true", help="replay the script when it ends")
    p.add_argument("--rules-only", action="store_true", help="never call Groq: local rules classify the chat")
    p.add_argument("--print-plan", action="store_true", help="print the schedule as JSON and exit")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if args.print_plan:
        print(json.dumps(plan(), indent=2, ensure_ascii=False))  # noqa: T201
        return 0
    if args.time_scale <= 0:
        print("--time-scale must be positive", file=sys.stderr)  # noqa: T201
        return 1
    from contracts import load_config  # noqa: PLC0415

    try:
        base = load_config(args.profile, args.config_dir, args.env_file)
    except Exception as exc:  # pydantic errors can echo values: report the class only
        print(f"config_error {type(exc).__name__}", file=sys.stderr)  # noqa: T201
        return 1
    if args.rules_only:
        os.environ.pop("GROQ_API_KEY", None)
    config = demo_config(base, rules_only=args.rules_only)
    try:
        return asyncio.run(
            serve(
                config=config,
                time_scale=args.time_scale,
                start_delay_s=args.start_delay,
                loop=args.loop,
                log_level=args.log_level,
            )
        )
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
