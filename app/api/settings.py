"""API settings (pydantic-settings). Secrets are ``SecretStr`` and never printed.

Environment names are the plain field names in upper case (``SECRET_KEY``, ``REQUIRE_AUTH``,
``API_HOST`` ...). ``.env`` is read by the settings loader only, never by hand.
"""

from __future__ import annotations

import shlex
import sys
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]
DEFAULT_ALLOWED_ORIGINS = ["http://127.0.0.1:3000", "http://localhost:3000"]
BUS_SUB_ENDPOINT = "ipc:///tmp/streamsarthi-bus"
BUS_PUB_ENDPOINT = "ipc:///tmp/streamsarthi-bus-in"
_NEVER_BIND = {"0.0.0.0", "::", "[::]", ""}  # noqa: S104 - listed only to refuse them


def _split(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    host: str = Field("127.0.0.1", validation_alias="API_HOST")
    port: int = Field(8000, validation_alias="API_PORT")
    web_port: int = Field(3000, validation_alias="WEB_PORT")
    lan_host: str | None = Field(None, validation_alias="API_LAN_HOST")
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_HOSTS), validation_alias="API_ALLOWED_HOSTS"
    )
    allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_ORIGINS), validation_alias="API_ALLOWED_ORIGINS"
    )

    secret_key: SecretStr | None = Field(None, validation_alias="SECRET_KEY")
    require_auth: bool = Field(True, validation_alias="REQUIRE_AUTH")

    bus_backend: Literal["zmq", "inprocess"] = Field("zmq", validation_alias="BUS_BACKEND")
    # The engine's XPUB side (subscribers connect here) and XSUB side (publishers connect here).
    bus_sub_endpoint: str = Field(BUS_SUB_ENDPOINT, validation_alias="BUS_SUB_ENDPOINT")
    bus_pub_endpoint: str = Field(BUS_PUB_ENDPOINT, validation_alias="BUS_PUB_ENDPOINT")

    # Opt-in encrypted persistence of the face allowlist (``enrol.persist``). The key is read from the
    # environment only (``ALLOWLIST_KEY``) and never defaulted; without it nothing is ever written.
    allowlist_key: SecretStr | None = Field(None, validation_alias="ALLOWLIST_KEY")
    allowlist_path: str = Field("~/.streamsarthi/allowlist.enc", validation_alias="ALLOWLIST_PATH")
    # Enrolment guards (the 12 MB body limit lives in security.py).
    enrol_max_inflight: int = 2
    enrol_rate_limit: int = 5  # requests per principal ...
    enrol_rate_window_s: float = 60.0  # ... per this window
    allowlist_sweep_s: float = 30.0  # how often expired faces are removed and the removal published
    allowlist_resync_grace_s: float = 3.0  # an engine.status this soon after a change may be stale

    engine_stale_s: float = 5.0  # 5 missed 1 Hz heartbeats
    ws_auth_timeout_s: float = 3.0
    ws_ping_interval_s: float = 15.0
    ws_queue_max: int = 500
    ws_lag_close_s: float = 5.0
    # Hold release is rate limited per caller (a stuck key must not flap the stream). Hold
    # *activation* is never limited.
    hold_release_limit: int = 8
    hold_release_window_s: float = 10.0
    session_start_timeout_s: float = 30.0
    # Go-live orchestration (T6.1). ``session_spawn=false`` never starts child processes (the engine is
    # run by hand with scripts/run_engine.sh, as in tests). Commands are shell-style strings; ``{profile}``
    # is replaced. They contain no secrets: keys travel in the child's environment.
    session_spawn: bool = Field(True, validation_alias="SESSION_SPAWN")
    session_engine_cmd: str | None = Field(None, validation_alias="SESSION_ENGINE_CMD")
    session_chat_cmd: str | None = Field(None, validation_alias="SESSION_CHAT_CMD")
    session_stop_timeout_s: float = Field(10.0, validation_alias="SESSION_STOP_TIMEOUT_S")
    session_ttl_s: float = Field(21600.0, validation_alias="SESSION_TTL_S")
    sessions_dir: str = Field("~/.streamsarthi/sessions", validation_alias="SESSIONS_DIR")
    ingest_host: str = Field("127.0.0.1", validation_alias="INGEST_BIND_HOST")
    # Moderator actions per moderator token (a held key or a script must not flood the platform APIs).
    mod_action_limit: int = 30
    mod_action_window_s: float = 10.0

    @field_validator("allowed_hosts", "allowed_origins", mode="before")
    @classmethod
    def _csv(cls, value: object) -> object:
        return _split(value)

    @field_validator("host", "lan_host")
    @classmethod
    def _never_wildcard(cls, value: str | None) -> str | None:
        if value is not None and value.strip() in _NEVER_BIND:
            raise ValueError("refusing to bind a wildcard address; use 127.0.0.1 or a private IP")
        return value

    @model_validator(mode="after")
    def _lan_rules(self) -> ApiSettings:
        if self.lan_host:
            self.require_auth = True  # LAN access always needs a token
            if self.lan_host not in self.allowed_hosts:
                self.allowed_hosts = [*self.allowed_hosts, self.lan_host]
            origin = f"http://{self.lan_host}:{self.web_port}"
            if origin not in self.allowed_origins:
                self.allowed_origins = [*self.allowed_origins, origin]
        return self

    @property
    def bind_host(self) -> str:
        return self.lan_host or self.host

    def engine_argv(self) -> list[str]:
        if self.session_engine_cmd:
            return shlex.split(self.session_engine_cmd)
        return [sys.executable, "-m", "engine.main", "--profile", "{profile}"]

    def chat_argv(self) -> list[str]:
        if self.session_chat_cmd:
            return shlex.split(self.session_chat_cmd)
        return [sys.executable, "-m", "chat.main", "--profile", "{profile}"]

    def secret(self) -> str:
        if self.secret_key is None or not self.secret_key.get_secret_value():
            raise RuntimeError("SECRET_KEY must be set (in .env or the environment)")
        return self.secret_key.get_secret_value()
