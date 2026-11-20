"""Restricted relay addresses and runtime-only credentials. No network side effects.

Adapted from IRL Shield ``relay/security.py`` (MIT, Copyright (c) 2026 Elan Nash; see
``relay/LICENSE``). Changes: the Tailscale-only bind rule is relaxed to loopback, LAN private
ranges and Tailscale 100.64.0.0/10 (spec section 5.5); the Twitch destination coupling is gone;
the MediaMTX user list is built here but travels over stdin only (see ``relay/mediamtx.py``);
URL and key redaction helpers were added for the log classifier.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

try:  # Linux only; the relay runs in WSL2/Linux.
    import resource
except ImportError:  # pragma: no cover
    resource = None  # type: ignore[assignment]

PATH_CAMERA = "camera"
PATH_SHIELDED = "shielded"
PATHS = (PATH_CAMERA, PATH_SHIELDED)
LOOPBACK = "127.0.0.1"

_ALLOWED_NETWORKS = tuple(
    ipaddress.IPv4Network(n)
    for n in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
)


def is_allowed_bind(host: str) -> bool:
    """True for IPv4 loopback, RFC 1918 LAN ranges and Tailscale 100.64.0.0/10."""
    try:
        address = ipaddress.IPv4Address(host)
    except (ipaddress.AddressValueError, ValueError):
        return False
    return any(address in net for net in _ALLOWED_NETWORKS)


@dataclass(frozen=True)
class RelayAddress:
    """Where MediaMTX listens. RTSP (internal reader) and the API are always loopback."""

    host: str = LOOPBACK
    rtmp_port: int = 1935
    srt_port: int = 8890
    rtsp_port: int = 8554
    api_port: int = 9997

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not is_allowed_bind(self.host):
            raise ValueError("Relay bind must be an explicit IPv4 loopback, LAN private or Tailscale address")
        ports = (self.rtmp_port, self.srt_port, self.rtsp_port, self.api_port)
        if any(type(p) is not int or not 1024 <= p <= 65535 for p in ports):
            raise ValueError("Relay requires explicit unprivileged ports")
        if len(set(ports)) != len(ports):
            raise ValueError("Relay ports must be distinct")

    # --- URL builders (credentials are only ever embedded in memory-held URLs) ---
    def rtmp(self, path: str, credentials: Credentials | None = None) -> str:
        _check_path(path)
        query = (
            "?" + urlencode({"user": credentials.user, "pass": credentials.password}) if credentials else ""
        )
        return f"rtmp://{self.host}:{self.rtmp_port}/{path}{query}"

    def srt(
        self, action: str, path: str, credentials: Credentials | None = None, *, latency_ms: int = 120
    ) -> str:
        if action not in ("read", "publish"):
            raise ValueError("Unknown SRT action")
        _check_path(path)
        streamid = f"{action}:{path}"
        if credentials:
            streamid += f":{credentials.user}:{credentials.password}"
        # FFmpeg and libsrt callers do not percent-decode `streamid`, so it is passed raw. Every
        # component matches [A-Za-z0-9_-] (see Credentials), so nothing needs escaping.
        return (
            f"srt://{self.host}:{self.srt_port}?streamid={streamid}"
            f"&pkt_size=1316&mode=caller&latency={int(latency_ms) * 1000}"
        )

    def rtsp(self, path: str, credentials: Credentials | None = None) -> str:
        _check_path(path)
        auth = (
            quote(credentials.user, safe="") + ":" + quote(credentials.password, safe="") + "@"
            if credentials
            else ""
        )
        return f"rtsp://{auth}{LOOPBACK}:{self.rtsp_port}/{path}"

    def api_base(self) -> str:
        return f"http://{LOOPBACK}:{self.api_port}"


def _check_path(path: str) -> None:
    if path not in PATHS:
        raise ValueError("Unknown relay path")


@dataclass(frozen=True)
class Credentials:
    """Runtime-only username and password. Hidden from ``repr``; never written to disk or logs."""

    user: str = field(repr=False)
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        # Both travel inside SRT stream ids and URLs: keep them to a safe alphabet.
        for value in (self.user, self.password):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_\-]{1,128}", value):
                raise ValueError("Credentials must match [A-Za-z0-9_-]{1,128}")

    @classmethod
    def generate(cls) -> Credentials:
        # Native media crashes must not write in-memory credentials into a core dump.
        disable_core_dumps()
        return cls("u" + secrets.token_hex(8), secrets.token_urlsafe(32))

    def __repr__(self) -> str:
        return "Credentials(<hidden>)"


def disable_core_dumps() -> None:
    if resource is not None:
        try:
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except (ValueError, OSError):  # pragma: no cover
            pass


def mediamtx_users(address: RelayAddress, phone: Credentials, internal: Credentials) -> list[dict]:
    """MediaMTX ``authInternalUsers``. No anonymous publisher; no wildcard paths.

    * ``phone``: publish ``camera`` only (OBS or the phone app).
    * ``internal``: read ``camera`` and publish ``shielded``, loopback only (the relay itself).
    * anonymous loopback: read ``shielded`` (the eval tap) and use the API.
    """
    return [
        {
            "user": phone.user,
            "pass": phone.password,
            "ips": [],
            "permissions": [{"action": "publish", "path": PATH_CAMERA}],
        },
        {
            "user": internal.user,
            "pass": internal.password,
            "ips": [LOOPBACK, address.host],
            "permissions": [
                {"action": "read", "path": PATH_CAMERA},
                {"action": "publish", "path": PATH_SHIELDED},
            ],
        },
        {"user": "any", "ips": [LOOPBACK], "permissions": [{"action": "read", "path": PATH_SHIELDED}]},
        {"user": "any", "ips": [LOOPBACK], "permissions": [{"action": "api"}]},
    ]


# --- redaction -----------------------------------------------------------------------------
_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]{1,12}://[^\s'\"<>]+")
_KV_RE = re.compile(r"(?i)\b(pass(?:word)?|user(?:name)?|key|token|secret|streamid)=([^\s&'\"]+)")
_LIVE_PATH_RE = re.compile(r"(/live2?/)[A-Za-z0-9_\-]{4,}")


def redact(text: str) -> str:
    """Replace URLs and ``key=value`` credentials in a log line. Used before any line is surfaced."""
    text = _URL_RE.sub("<url>", text)
    text = _KV_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    return _LIVE_PATH_RE.sub(r"\1<redacted>", text)
