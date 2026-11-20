"""Platform action dispatch with downgrade (T4.3 / T4.4): DELETE and TIMEOUT verdicts to YouTube / Twitch."""

from .dispatcher import ActionDispatcher, PlatformActions, PlatformPolicy, policy_for

__all__ = ["ActionDispatcher", "PlatformActions", "PlatformPolicy", "policy_for"]
