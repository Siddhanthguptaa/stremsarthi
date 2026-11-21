"""Phone pairing for the streamer app: publish URLs, the Moblin import URL and a QR image.

The API does not import ``relay`` at module level. ``build_pairing_info`` tries the relay's pairing
helper (``relay.pairing.build_pairing``, T6.4: address rules, SRT latency and passphrase) through
``importlib`` and falls back to a small local builder with the same URL shapes, so pairing works even
if the helper is missing or refuses the host. Tests inject a fake through ``app.state.pairing_builder``.

Every URL carries the per-session publish login (and maybe an SRT passphrase): it is returned only by the
streamer-only pairing routes, never logged, never stored, and ``PairingView`` hides it from ``repr``.
The YouTube or Twitch stream key is never part of it.
"""

from __future__ import annotations

import importlib
import ipaddress
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Protocol
from urllib.parse import quote

MAX_QR_CHARS = 2000


class PairingView(Protocol):
    @property
    def rtmp_url(self) -> str: ...
    @property
    def srt_url(self) -> str: ...
    def moblin_url(self, protocol: str = "srt") -> str: ...


_PASSPHRASE_RE = re.compile(r"^[A-Za-z0-9_\-]{10,79}$")  # relay.srt.validate_passphrase


def srt_passphrase() -> str | None:
    """INGEST_SRT_PASSPHRASE from the environment or ``.env`` (via ``relay.netenv``), if it is valid.

    The engine child reads the same value, so both sides agree. Never logged."""
    try:
        value = importlib.import_module("relay.netenv").effective_env().get("INGEST_SRT_PASSPHRASE")
    except ImportError:
        value = os.environ.get("INGEST_SRT_PASSPHRASE")
    value = (value or "").strip()
    return value if _PASSPHRASE_RE.match(value) else None


def public_host(bind_host: str) -> str:
    """The address OBS or a phone dials. A specific bind address is used as is; for the wildcard bind
    ``relay.pairing.resolve_pairing_host`` picks ``PAIRING_HOST`` or the detected LAN address.
    Raises ``ValueError`` (naming ``PAIRING_HOST``) when none can be found."""
    try:
        resolve = importlib.import_module("relay.pairing").resolve_pairing_host
    except (ImportError, AttributeError):
        return bind_host
    return str(resolve(bind_host)[0])


PairingBuilder = Callable[[str, str, str], PairingView]  # (host, user, password)


@dataclass(frozen=True, repr=False)
class LocalPairing:
    _rtmp: str = field(repr=False)
    _srt: str = field(repr=False)
    stream_name: str = "Stream Sarthi"

    def __repr__(self) -> str:
        return "LocalPairing(<secrets hidden>)"

    @property
    def rtmp_url(self) -> str:
        return self._rtmp

    @property
    def srt_url(self) -> str:
        return self._srt

    def moblin_url(self, protocol: str = "srt") -> str:
        url = {"srt": self._srt, "rtmp": self._rtmp}.get(protocol)
        if url is None:
            raise ValueError("protocol must be 'srt' or 'rtmp'")
        payload = {"streams": [{"name": self.stream_name, "url": url, "selected": True}]}
        text = "moblin://?" + quote(json.dumps(payload, separators=(",", ":")), safe="")
        if len(text) > MAX_QR_CHARS:
            raise ValueError("pairing payload too large for a QR code")
        return text


def local_pairing(
    host: str, user: str, password: str, rtmp_port: int = 1935, srt_port: int = 8890
) -> PairingView:
    from .orchestrator import ingest_urls

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("pairing host must be a literal IP address") from None
    if address.is_unspecified or address.is_multicast:
        raise ValueError("pairing host must be an address the phone can dial")

    urls = ingest_urls(host, user, password, rtmp_port, srt_port, srt_passphrase())
    return LocalPairing(urls["rtmp_url"], urls["srt_url"])


def relay_pairing(
    host: str, user: str, password: str, rtmp_port: int = 1935, srt_port: int = 8890
) -> PairingView:
    """``relay.pairing.build_pairing`` for this login; raises ``ImportError``/``ValueError`` if unusable."""
    pairing = importlib.import_module("relay.pairing")
    security = importlib.import_module("relay.security")
    # The engine child reads the same INGEST_SRT_PASSPHRASE (environment or .env), so both sides agree.
    passphrase = srt_passphrase()
    return pairing.build_pairing(  # type: ignore[no-any-return]
        host,
        security.Credentials(user, password),
        rtmp_port=rtmp_port,
        srt_port=srt_port,
        srt_passphrase=passphrase,
    )


def build_pairing_info(
    host: str,
    user: str,
    password: str,
    builder: PairingBuilder | None = None,
    rtmp_port: int = 1935,
    srt_port: int = 8890,
) -> PairingView:
    if builder is not None:
        return builder(host, user, password)
    try:
        return relay_pairing(host, user, password, rtmp_port, srt_port)
    except (ImportError, AttributeError, ValueError, TypeError):
        return local_pairing(host, user, password, rtmp_port, srt_port)


def pairing_payload(info: PairingView) -> dict[str, Any]:
    """The streamer-only JSON: no stream key (the login is in the URL query, so ``key`` is omitted)."""
    return {
        "rtmp": {"url": info.rtmp_url},
        "srt": {"url": info.srt_url},
        "moblin_url": info.moblin_url("srt"),
        "moblin_rtmp_url": info.moblin_url("rtmp"),
    }


def qr_png(text: str) -> bytes:
    import qrcode
    from qrcode.image.pil import PilImage

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    stream = BytesIO()
    qr.make_image(image_factory=PilImage).save(stream, format="PNG")
    return stream.getvalue()
