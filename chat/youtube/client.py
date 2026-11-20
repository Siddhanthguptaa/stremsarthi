"""Authorised, quota-charged YouTube API calls. Every API call goes through ``YouTubeClient.call``, so
the governor's counter sees all of them."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from chat.common.errors import ChatError
from chat.common.http import FixedOriginClient, HttpResponse

from .auth import YouTubeSession
from .governor import Kind, QuotaGovernor
from .http import api_error_reason

QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded"})


class QuotaDenied(ChatError):
    """The governor refused the call (budget spent). Nothing was sent."""

    def __init__(self) -> None:
        super().__init__("yt_quota_denied")


class YouTubeClient:
    def __init__(self, http: FixedOriginClient, session: YouTubeSession, governor: QuotaGovernor) -> None:
        self.http = http
        self.session = session
        self.governor = governor

    async def call(
        self,
        method: str,
        path: str,
        kind: Kind,
        *,
        params: Mapping[str, str | int] | None = None,
        json_body: Any = None,
    ) -> HttpResponse:
        if not self.governor.try_spend(kind):
            raise QuotaDenied
        response = await self._send(method, path, params, json_body, force_refresh=False)
        if response.status == 401:
            # expired or revoked access token: one forced refresh, charged again (conservative)
            if not self.governor.try_spend(kind):
                raise QuotaDenied
            response = await self._send(method, path, params, json_body, force_refresh=True)
        if response.status == 403 and api_error_reason(response.body) in QUOTA_REASONS:
            self.governor.mark_exhausted()
        return response

    async def _send(
        self,
        method: str,
        path: str,
        params: Mapping[str, str | int] | None,
        json_body: Any,
        *,
        force_refresh: bool,
    ) -> HttpResponse:
        token = await self.session.access_token(force_refresh=force_refresh)
        return await self.http.request(
            method,
            path,
            params=params,
            json_body=json_body,
            headers={"Authorization": "Bearer " + token.reveal(), "Accept": "application/json"},
        )
