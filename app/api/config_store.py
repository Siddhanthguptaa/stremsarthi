"""The API's view of ``contracts.Config``: a public read model and validated, atomic patches.

Rules (docs/specs/app-api.md 5.2 and 5.8):

* ``GET`` never returns a secret. Secrets show as ``"***"`` when set and ``null`` when not, and the
  streamer's private chat terms and phone numbers, the channel login and the risky-text blocklist are
  never returned (counts only).
* ``PATCH`` is a JSON merge. It is flattened to dotted keys, checked against the patchable set,
  validated by building a full ``Config`` (so every contract limit applies), diffed against the
  current value, and only the diff is published as one ``ConfigChange``. A rejected patch changes
  nothing, and so does a failed publish.
* Live (while a session is active) only ``categories.*``, ``captions.*``, ``audio.languages``,
  ``hold.*`` and ``chat.*`` may change. ``delay_seconds``, ``gpu.*`` and ``detect.*`` need the
  engine to restart and are refused with 409 while a session is active.

The configuration lives in RAM only. The engine reads files and ``.env`` itself at start.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import ValidationError

from contracts import Config, ConfigChange
from contracts.config import Destination

from .errors import ApiError

SECRET_FIELDS = (
    "youtube_stream_key",
    "twitch_stream_key",
    "youtube_oauth_client_id",
    "youtube_oauth_client_secret",
    "twitch_client_id",
    "twitch_client_secret",
    "groq_api_key",
    "sarvam_api_key",
)
LIVE_PREFIXES = ("categories.", "captions.", "hold.", "chat.")
LIVE_KEYS = frozenset({"audio.languages"})
RESTART_PREFIXES = ("gpu.", "detect.")
RESTART_KEYS = frozenset({"delay_seconds"})
# Names and phone numbers the streamer wants protected (``chat.streamer_private.*``) and the channel
# login. Never returned, never accepted here: the chat module reads them from its own config.
PRIVATE_PREFIXES = ("chat.streamer_private", "chat.twitch.channel_login")
# Languages the speech pipeline can transcribe (docs/specs/audio.md): Hinglish/Hindi, English, Kannada,
# Tamil, Telugu, Bengali. Native names are for the UI; the API only checks the codes.
SUPPORTED_AUDIO_LANGUAGES = ("hi", "en", "kn", "ta", "te", "bn")
MAX_DEPTH = 5
MAX_KEY_CHARS = 64


def flatten(patch: dict[str, Any], prefix: str = "", depth: int = 0) -> dict[str, Any]:
    """``{"categories": {"face": {"enabled": False}}}`` -> ``{"categories.face.enabled": False}``."""
    if depth > MAX_DEPTH:
        raise ApiError(422, "invalid_patch", "patch is nested too deeply")
    out: dict[str, Any] = {}
    for key, value in patch.items():
        if not isinstance(key, str) or not key or len(key) > MAX_KEY_CHARS or "." in key:
            raise ApiError(422, "invalid_patch", "keys must be short names without dots")
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flatten(value, f"{path}.", depth + 1))
        else:
            out[path] = value
    return out


def _get(tree: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(tree, dict) or part not in tree:
            return None
        tree = tree[part]
    return tree


def _set(tree: dict[str, Any], path: str, value: Any) -> None:
    *parents, leaf = path.split(".")
    for part in parents:
        nxt = tree.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            tree[part] = nxt
        tree = nxt
    tree[leaf] = value


def classify(path: str) -> str:
    """``live``, ``restart`` or ``fixed`` (not patchable through the API)."""
    if path.startswith(PRIVATE_PREFIXES):
        return "fixed"
    if path in RESTART_KEYS or path.startswith(RESTART_PREFIXES):
        return "restart"
    if path in LIVE_KEYS or path.startswith(LIVE_PREFIXES):
        return "live"
    return "fixed"


class ConfigStore:
    def __init__(self, config: Config) -> None:
        self.config = config

    # --- read ------------------------------------------------------------------------------
    def configured_secrets(self) -> dict[str, bool]:
        return {name: getattr(self.config, name) is not None for name in SECRET_FIELDS}

    def public(self) -> dict[str, Any]:
        """What the UI may see: no secrets, no private terms, no engine internals or file paths."""
        c = self.config
        dump = c.model_dump(mode="json", exclude=set(SECRET_FIELDS))
        chat = dict(dump["chat"])
        private = chat.pop("streamer_private")
        chat["streamer_private_counts"] = {k: len(v) for k, v in private.items()}  # counts only
        twitch = dict(chat["twitch"])
        chat["twitch"] = {
            **{k: v for k, v in twitch.items() if k != "channel_login"},
            "channel_configured": bool(twitch["channel_login"]),
        }
        guard = {k: dump["guard"][k] for k in ("enabled", "profanity_enabled", "warn_cooldown_s")}
        return {
            "profile": dump["profile"],
            "delay_seconds": dump["delay_seconds"],
            "delay_warning": c.delay_warning(),
            "fps": dump["fps"],
            "detect": dump["detect"],
            "gpu": {k: dump["gpu"][k] for k in ("enabled", "ort_provider", "mem_limit_mb")},
            "categories": dump["categories"],
            "audio": {
                k: dump["audio"][k]
                for k in ("languages", "mute_mode", "failsafe_mute_voiced", "address_mute", "stt_order")
            }
            | {"supported_languages": list(SUPPORTED_AUDIO_LANGUAGES)},
            "hold": dump["hold"],
            "chat": chat,
            "guard": guard,
            "captions": dump["captions"],
            "destinations": [{"kind": d["kind"], "enabled": d["enabled"]} for d in dump["destinations"]],
            "secrets": {name: ("***" if ok else None) for name, ok in self.configured_secrets().items()},
        }

    # --- write -----------------------------------------------------------------------------
    def plan_patch(self, patch: dict[str, Any], live: bool) -> tuple[Config, dict[str, Any]]:
        """Validate ``patch`` and return ``(new_config, diff)`` without changing anything."""
        leaves = flatten(patch)
        if not leaves:
            raise ApiError(422, "empty_patch", "the patch changes nothing")
        for path in leaves:
            kind = classify(path)
            if kind == "fixed":
                raise ApiError(422, "not_patchable", f"{path} cannot be changed through the API")
            if kind == "restart" and live:
                raise ApiError(409, "requires_restart", f"{path} cannot change while a session is active")

        if "audio.languages" in leaves:
            leaves["audio.languages"] = self._checked_languages(leaves["audio.languages"])

        secrets = {name: getattr(self.config, name) for name in SECRET_FIELDS}
        current = self.config.model_dump(mode="json", exclude=set(SECRET_FIELDS))
        merged = copy.deepcopy(current)
        for path, value in leaves.items():
            _set(merged, path, value)
        try:
            validated = Config.model_validate(merged)
        except ValidationError as exc:
            # Locations and messages only: never echo the submitted values.
            problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
            raise ApiError(422, "invalid_config", problems[:500]) from None
        new_dump = validated.model_dump(mode="json", exclude=set(SECRET_FIELDS))
        diff = {p: _get(new_dump, p) for p in leaves if _get(new_dump, p) != _get(current, p)}
        new_config = validated.model_copy(update=secrets)
        return new_config, diff

    @staticmethod
    def _checked_languages(value: Any) -> list[str]:
        """A non-empty list of supported language codes, in the given order, without repeats."""
        if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
            raise ApiError(422, "invalid_config", "audio.languages must be a non-empty list of codes")
        if any(v not in SUPPORTED_AUDIO_LANGUAGES for v in value):
            raise ApiError(
                422, "invalid_config", f"audio.languages: use only {', '.join(SUPPORTED_AUDIO_LANGUAGES)}"
            )
        return list(dict.fromkeys(value))

    def change_for(self, diff: dict[str, Any], actor: str) -> ConfigChange:
        try:
            return ConfigChange(diff=diff, actor=actor)
        except ValueError as exc:
            raise ApiError(422, "invalid_config", str(exc)[:200]) from None

    def commit(self, config: Config) -> None:
        self.config = config

    def with_destinations(self, enabled: dict[str, bool]) -> Config:
        """Copy of the config with the youtube/twitch enabled flags set (session start)."""
        dests = [
            Destination(kind=d.kind, url_host=d.url_host, enabled=enabled.get(d.kind, d.enabled))
            for d in self.config.destinations
        ]
        return self.config.model_copy(update={"destinations": dests})
