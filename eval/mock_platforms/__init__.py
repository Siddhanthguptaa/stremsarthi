"""Mock YouTube and Twitch servers for the chat connector and action tests (T4.1).

Written independently of ``chat/testing`` (the connector author's own fakes): they follow the public API shapes
(YouTube Data API v3 liveChat and Google OAuth refresh, Twitch device-code OAuth, EventSub WebSocket, Helix
moderation) so a bug the connector author's fake shares with the connector shows up here. Loopback only
(``127.0.0.1``, port 0), no network, synthetic data. ``YouTubeMock.origin`` / ``TwitchMock.origin`` go to the
connectors' ``loopback_origin``; ``TwitchMock.ws_url`` to ``EventSubTransport(url=..., mock=True)``.
"""

from .twitch_mock import SCOPES, TwitchMock
from .youtube_mock import COSTS, YouTubeMock

__all__ = ["COSTS", "SCOPES", "TwitchMock", "YouTubeMock"]
