"""Phone pairing payloads: the Moblin import URL and generic SRT or RTMP publish URLs (T6.4).

Pure functions: no network, no logging, no files. QR image rendering is ``app/api``'s job (it encodes
``PairingInfo.moblin_url`` or one of the generic URLs). Every URL here embeds the publish login and
the SRT passphrase, so the objects hide them from ``repr``/``str`` and offer ``redacted()`` for
anything that is shown, logged or stored.

Adapted from IRL Shield ``shield/dashboard/pairing.py`` (MIT, Copyright (c) 2026 Elan Nash; see
``relay/LICENSE``): the ``moblin://?`` plus percent-encoded JSON ``streams`` import format and the
literal-address rule. Changed: the address rule is relaxed from Tailscale-only to loopback, LAN
private ranges and Tailscale 100.64.0.0/10 (spec section 5.5); SRT is no longer "unqualified"; the
SRT URL carries the stream id login, the optional passphrase and the latency; QR rendering moved out.

UNVERIFIED on a real phone: Moblin and Larix honouring ``latency`` and ``passphrase`` in the URL
query. The documented minimum (name, url, selected) is all the Moblin payload contains, so a client
that ignores the extra query keys still imports; set latency and passphrase in the app in that case.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import quote

from relay.netenv import effective_env
from relay.security import PATH_CAMERA, WILDCARD, Credentials, is_allowed_bind
from relay.srt import resolve_latency_ms, validate_passphrase

MAX_PAYLOAD_CHARS = 2000  # a QR code at error-correction M holds about 2300 alphanumeric characters
REDACTED = "<redacted>"
ENV_PAIRING_HOST = "PAIRING_HOST"


def detect_lan_host() -> str | None:
    """This machine's outbound IPv4 address (a UDP ``connect`` sends no packet), or ``None``.

    Under WSL2 mirrored networking this is the Windows LAN address; under NAT networking it is the
    WSL virtual address (172.x), which a phone cannot reach: set ``PAIRING_HOST`` then.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))  # TEST-NET-1: never routed, only selects the interface
            return str(sock.getsockname()[0])
    except OSError:
        return None


def resolve_pairing_host(
    bind_host: str,
    environ: Mapping[str, str] | None = None,
    *,
    detect: Callable[[], str | None] = detect_lan_host,
) -> tuple[str, str]:
    """The address a phone should dial, and where it came from (``bind``, ``PAIRING_HOST`` or ``autodetect``).

    A specific bind address is dialled as is. For the wildcard bind (``0.0.0.0`` or empty) the
    ``PAIRING_HOST`` variable wins, else the outbound interface address is detected; a result that is
    not loopback/LAN/Tailscale is refused with a message that names ``PAIRING_HOST``.
    """
    if bind_host not in (WILDCARD, ""):
        return pairing_host(bind_host), "bind"
    override = (effective_env(environ).get(ENV_PAIRING_HOST) or "").strip()
    if override:
        try:
            return pairing_host(override), ENV_PAIRING_HOST
        except ValueError:
            raise ValueError(f"{ENV_PAIRING_HOST} must be a literal LAN private or Tailscale IPv4 address") from None
    found = detect()
    if found:
        try:
            host = pairing_host(found)
        except ValueError:
            host = ""
        if host and not host.startswith("127."):
            return host, "autodetect"
    raise ValueError(f"cannot detect a LAN address for the phone: set {ENV_PAIRING_HOST}=<this PC's LAN IPv4>")


def pairing_host(host: str) -> str:
    """A literal loopback, LAN private or Tailscale IPv4 address the phone can dial, else ``ValueError``.

    ``0.0.0.0`` is a bind address, never a destination, and is refused.
    """
    try:
        if type(host) is not str:
            raise ValueError
        address = ipaddress.IPv4Address(host)
    except ValueError:
        raise ValueError(
            "pairing host must be a literal loopback, LAN private or Tailscale IPv4 address"
        ) from None
    if address.is_unspecified or not is_allowed_bind(host):
        raise ValueError("pairing host must be a literal loopback, LAN private or Tailscale IPv4 address")
    return str(address)


@dataclass(frozen=True, repr=False)
class PairingInfo:
    """Everything a phone needs to publish. ``repr`` and ``str`` never show a login or passphrase."""

    host: str
    rtmp_port: int
    srt_port: int
    latency_ms: int
    stream_name: str
    _rtmp_url: str = field(repr=False)
    _srt_url: str = field(repr=False)

    def __repr__(self) -> str:
        return f"PairingInfo(host={self.host!r}, rtmp_port={self.rtmp_port}, srt_port={self.srt_port}, <secrets hidden>)"

    __str__ = __repr__

    @property
    def rtmp_url(self) -> str:
        """Generic RTMP publish URL with the login in the query. Secret: never log it."""
        return self._rtmp_url

    @property
    def srt_url(self) -> str:
        """Generic SRT publish URL (libsrt convention: ``latency`` in ms). Secret: never log it."""
        return self._srt_url

    def moblin_url(self, protocol: str = "srt") -> str:
        """``moblin://?<percent-encoded JSON>`` for Moblin's "import from URL" or QR scan. Secret."""
        url = self._srt_url if protocol == "srt" else self._rtmp_url if protocol == "rtmp" else None
        if url is None:
            raise ValueError("protocol must be 'srt' or 'rtmp'")
        payload = {"streams": [{"name": self.stream_name, "url": url, "selected": True}]}
        # Encode the outer JSON once; the stream URL inside it must stay byte for byte as built.
        text = "moblin://?" + quote(json.dumps(payload, separators=(",", ":")), safe="")
        if len(text) > MAX_PAYLOAD_CHARS:
            raise ValueError("pairing payload too large for a QR code")
        return text

    def redacted(self) -> dict[str, object]:
        """Safe to show, log or store: addresses, ports and latency, no login, no passphrase."""
        return {
            "host": self.host,
            "rtmp": f"rtmp://{self.host}:{self.rtmp_port}/{PATH_CAMERA}?{REDACTED}",
            "srt": f"srt://{self.host}:{self.srt_port}?{REDACTED}",
            "latency_ms": self.latency_ms,
            "srt_encrypted": "passphrase=" in self._srt_url,
        }

    def private_payload(self) -> dict[str, object]:
        """For the authenticated streamer app only (never log, never store)."""
        return {
            "rtmp_url": self._rtmp_url,
            "srt_url": self._srt_url,
            "moblin_srt": self.moblin_url("srt"),
            "moblin_rtmp": self.moblin_url("rtmp"),
            "latency_ms": self.latency_ms,
        }


def build_pairing(
    host: str,
    phone: Credentials,
    *,
    rtmp_port: int = 1935,
    srt_port: int = 8890,
    srt_passphrase: str | None = None,
    latency_ms: int | str = 400,
    stream_name: str = "Stream Sarthi",
) -> PairingInfo:
    """Build the phone's publish URLs for ``host`` (the address the phone can reach, not the bind).

    ``latency_ms`` defaults to the 4G preset; pass ``"lan"`` or a number. The login and passphrase
    are embedded in the returned URLs only; nothing is logged or written.
    """
    if host in (WILDCARD, ""):  # the bind address is not a destination: pick one the phone can dial
        host, _source = resolve_pairing_host(host)  # callers that need the source call resolve_pairing_host
    host = pairing_host(host)
    latency = resolve_latency_ms(latency_ms)
    for port in (rtmp_port, srt_port):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("ports must be unprivileged integers")
    if not isinstance(stream_name, str) or not 1 <= len(stream_name) <= 40 or not stream_name.isprintable():
        raise ValueError("stream name must be 1..40 printable characters")
    rtmp = f"rtmp://{host}:{rtmp_port}/{PATH_CAMERA}?user={phone.user}&pass={phone.password}"
    # Stream id components match [A-Za-z0-9_-] (see Credentials), so nothing needs escaping.
    srt = (
        f"srt://{host}:{srt_port}?streamid=publish:{PATH_CAMERA}:{phone.user}:{phone.password}"
        f"&latency={latency}&mode=caller"
    )
    if srt_passphrase is not None:
        srt += f"&passphrase={validate_passphrase(srt_passphrase)}&pbkeylen=16"
    return PairingInfo(host, rtmp_port, srt_port, latency, stream_name, rtmp, srt)
