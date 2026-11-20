"""Turn a ``ChatVerdict`` into platform actions and downgrade what the platform cannot do.

Only DELETE and TIMEOUT touch the platform. ALLOW, HIDE_FROM_STREAMER and HOLD_FOR_MOD are local
(overlay and mod queue) and pass through unchanged.

Downgrade rules (every downgrade keeps the message hidden and visible to moderators, never allowed):

* TIMEOUT where timeouts are off (YouTube ``allow_timeout=false`` by default, Twitch ``auto_timeout``
  false), unsupported, or failed: fall back to DELETE of that message.
* DELETE where deletion is off (``auto_delete=false``), the platform refuses (privileged author,
  quota low or exhausted, missing permission, rate limit, no connector such as Twitch anonymous read):
  fall back to HOLD_FOR_MOD.

A downgraded verdict has the new ``action``, ``timeout_seconds`` cleared unless still TIMEOUT, the
original reason plus ``:dg_<code>``, and ``platform_result`` of the action that was finally attempted
(``done`` if the fallback delete worked, else ``unsupported`` or ``failed``). The follow-up verdict is
published on ``chat.verdict`` when a publisher is given (spec section 4).

Nothing here logs message text.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from typing import Literal, Protocol

from chat.common import ActionResult, ChatPublisher, hash_id, log_event
from contracts import ChatAction, ChatMessage, ChatVerdict
from contracts.config import ChatConfig

_LOG = logging.getLogger("chat.moderation")
_LOCAL_ACTIONS = frozenset({ChatAction.ALLOW, ChatAction.HIDE_FROM_STREAMER, ChatAction.HOLD_FOR_MOD})
_REASON_MAX = 64


class PlatformActions(Protocol):
    """Satisfied by ``YouTubeActions`` and ``TwitchActions``."""

    async def delete(self, message: ChatMessage) -> ActionResult: ...

    async def timeout(self, message: ChatMessage, seconds: int) -> ActionResult: ...


@dataclass(frozen=True, slots=True)
class PlatformPolicy:
    auto_delete: bool
    allow_timeout: bool


def policy_for(platform: Literal["youtube", "twitch"], config: ChatConfig) -> PlatformPolicy:
    if platform == "youtube":
        return PlatformPolicy(config.youtube.auto_delete, config.youtube.allow_timeout)
    return PlatformPolicy(config.twitch.auto_delete, config.twitch.auto_timeout)


def _with_reason(reason: str, code: str) -> str:
    suffix = f":dg_{code}"
    return reason[: _REASON_MAX - len(suffix)] + suffix


class ActionDispatcher:
    def __init__(
        self,
        *,
        youtube: PlatformActions | None = None,
        twitch: PlatformActions | None = None,
        config: ChatConfig | None = None,
        publisher: ChatPublisher | None = None,
    ) -> None:
        self._backends: dict[str, PlatformActions | None] = {"youtube": youtube, "twitch": twitch}
        self._cfg = config or ChatConfig()
        self._publisher = publisher

    def update_config(self, config: ChatConfig) -> None:
        self._cfg = config

    def policy(self, platform: Literal["youtube", "twitch"]) -> PlatformPolicy:
        return policy_for(platform, self._cfg)

    # ---- direct calls (moderator actions from the API) ----------------------------------------
    async def delete(self, message: ChatMessage) -> ActionResult:
        """A moderator asked for it: no policy gate, no downgrade, the result is reported as is."""
        backend = self._backends.get(message.platform)
        if backend is None:
            return ActionResult("unsupported", "no_connector")
        return await self._guard(backend.delete(message))

    async def timeout(self, message: ChatMessage, seconds: int) -> ActionResult:
        backend = self._backends.get(message.platform)
        if backend is None:
            return ActionResult("unsupported", "no_connector")
        if not self.policy(message.platform).allow_timeout:
            return ActionResult("unsupported", "timeout_disabled")
        return await self._guard(backend.timeout(message, seconds))

    # ---- verdict driven -----------------------------------------------------------------------
    async def apply(self, message: ChatMessage, verdict: ChatVerdict) -> ChatVerdict:
        """Run the platform action for ``verdict`` and return the verdict as it finally stands."""
        if verdict.action in _LOCAL_ACTIONS:
            return verdict
        policy = self.policy(message.platform)
        backend = self._backends.get(message.platform)
        result = verdict
        if verdict.action is ChatAction.TIMEOUT:
            result = await self._timeout_or_downgrade(message, verdict, policy, backend)
        else:
            result = await self._delete_or_downgrade(message, verdict, policy, backend)
        log_event(
            _LOG,
            "action_applied",
            mid=hash_id(message.message_id),
            asked=verdict.action.value,
            final=result.action.value,
            result=result.platform_result,
            reason=result.reason,
        )
        if self._publisher is not None:
            self._publisher.verdict(result)
        return result

    # ---- internals ----------------------------------------------------------------------------
    async def _timeout_or_downgrade(
        self,
        message: ChatMessage,
        verdict: ChatVerdict,
        policy: PlatformPolicy,
        backend: PlatformActions | None,
    ) -> ChatVerdict:
        seconds = verdict.timeout_seconds or 0
        if backend is None:
            code = "no_connector_hold"
        elif not policy.allow_timeout:
            code = "timeout_off_delete"
        else:
            res = await self._guard(backend.timeout(message, seconds))
            if res.status == "done":
                if policy.auto_delete:  # also remove the offending message itself, best effort
                    await self._guard(backend.delete(message))
                return dataclasses.replace(verdict, platform_result="done")
            code = "timeout_failed_delete"
        if backend is not None:
            return await self._delete_or_downgrade(message, verdict, policy, backend, code=code)
        return self._hold(verdict, code, "unsupported")

    async def _delete_or_downgrade(
        self,
        message: ChatMessage,
        verdict: ChatVerdict,
        policy: PlatformPolicy,
        backend: PlatformActions | None,
        *,
        code: str | None = None,
    ) -> ChatVerdict:
        from_timeout = verdict.action is ChatAction.TIMEOUT
        if backend is None:
            return self._hold(verdict, "no_connector_hold", "unsupported")
        if not policy.auto_delete:
            return self._hold(verdict, "delete_off_hold", "unsupported")
        res = await self._guard(backend.delete(message))
        if res.status == "done":
            if from_timeout:
                return dataclasses.replace(
                    verdict,
                    action=ChatAction.DELETE,
                    timeout_seconds=None,
                    reason=_with_reason(verdict.reason, code or "timeout_off_delete"),
                    platform_result="done",
                )
            return dataclasses.replace(verdict, platform_result="done")
        return self._hold(verdict, "delete_failed_hold", res.status)

    @staticmethod
    def _hold(
        verdict: ChatVerdict, code: str, platform_result: Literal["unsupported", "failed", "done"]
    ) -> ChatVerdict:
        # ``done`` cannot happen for a hold fallback; map it to ``failed`` defensively.
        pr: Literal["unsupported", "failed"] = "unsupported" if platform_result == "unsupported" else "failed"
        return dataclasses.replace(
            verdict,
            action=ChatAction.HOLD_FOR_MOD,
            timeout_seconds=None,
            reason=_with_reason(verdict.reason, code),
            platform_result=pr,
        )

    @staticmethod
    async def _guard(call: object) -> ActionResult:
        """A platform adapter bug must never crash the pipeline: report it as a failed action."""
        try:
            return await call  # type: ignore[misc]
        except Exception as exc:
            log_event(_LOG, "action_exception", level=logging.ERROR, error=type(exc).__name__)
            return ActionResult("failed", "exception")
