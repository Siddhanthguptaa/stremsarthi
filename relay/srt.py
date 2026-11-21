"""SRT tuning for a phone on 4G: latency presets, passphrase rules, exposure checks.

Pure functions, no network and no logging. New code (spec section 5.1 point 3 and T6.4); the only
upstream idea reused is the stream id layout ``publish:<path>:<user>:<password>`` that MediaMTX
documents.

How the three knobs map to MediaMTX and libsrt:

* latency: SRT negotiates ``max(sender, receiver)``. The relay sets ``srtMinLatency`` (floor) from
  ``relay.srt_latency_ms``; the phone app sets its own. 120 ms is right for a LAN, 300 to 500 ms for
  4G (libsrt guideline: latency of at least 4 x RTT at light loss, more at 2 percent loss).
* stream id: carries the publish login. It travels in the SRT handshake in the clear, so on a hostile
  network it is readable. The passphrase protects the media, not the stream id: rotate the login
  per session (the default: it is generated per run).
* passphrase (``srtPublishPassphrase``): AES-encrypts the media between phone and relay. Required for
  any bind other than loopback (``validate_exposure``).
"""

from __future__ import annotations

import re
import secrets
import string

from relay.security import Credentials, RelayAddress

LATENCY_MIN_MS = 20
LATENCY_MAX_MS = 2000

# Named presets for ``relay.srt_latency_ms``. 4G is the middle of the 300 to 500 ms range the spec gives.
SRT_LATENCY_PRESETS: dict[str, int] = {"lan": 120, "wifi": 200, "4g": 400, "4g_weak": 500}

PASSPHRASE_MIN, PASSPHRASE_MAX = 10, 79  # libsrt limits
MIN_EXPOSED_PASSWORD = 12  # generated logins are 43 characters; this only guards a fixed INGEST_PUBLISH_PASSWORD
_PASSPHRASE_RE = re.compile(r"[A-Za-z0-9_\-]{10,79}")
_PASSPHRASE_ALPHABET = string.ascii_letters + string.digits


def resolve_latency_ms(value: int | str) -> int:
    """A preset name (``lan``, ``wifi``, ``4g``, ``4g_weak``) or a number of ms, validated and returned."""
    if isinstance(value, str):
        key = value.strip().lower()
        if key in SRT_LATENCY_PRESETS:
            return SRT_LATENCY_PRESETS[key]
        if not key.isdigit():
            raise ValueError(f"unknown SRT latency preset (known: {sorted(SRT_LATENCY_PRESETS)})")
        value = int(key)
    if type(value) is not int or not LATENCY_MIN_MS <= value <= LATENCY_MAX_MS:
        raise ValueError(f"SRT latency must be an int in {LATENCY_MIN_MS}..{LATENCY_MAX_MS} ms")
    return value


def recommended_latency_ms(rtt_ms: float, loss_percent: float = 0.0) -> int:
    """libsrt rule of thumb: latency >= multiplier x RTT, with the multiplier growing with loss.

    <= 1 % loss: 4x; <= 3 %: 5.5x; <= 7 %: 7.5x; above: 10x. Clamped to the supported range and
    never below the LAN default of 120 ms.
    """
    if rtt_ms < 0 or loss_percent < 0:
        raise ValueError("rtt and loss must be non-negative")
    if loss_percent <= 1.0:
        factor = 4.0
    elif loss_percent <= 3.0:
        factor = 5.5
    elif loss_percent <= 7.0:
        factor = 7.5
    else:
        factor = 10.0
    return int(min(LATENCY_MAX_MS, max(SRT_LATENCY_PRESETS["lan"], round(rtt_ms * factor))))


def generate_passphrase(length: int = 32) -> str:
    """Random SRT passphrase (letters and digits: safe inside URLs, YAML and a QR payload)."""
    if not PASSPHRASE_MIN <= length <= PASSPHRASE_MAX:
        raise ValueError("passphrase length must be 10..79")
    return "".join(secrets.choice(_PASSPHRASE_ALPHABET) for _ in range(length))


def validate_passphrase(value: str) -> str:
    """Return the passphrase if libsrt accepts it and it is URL-safe, else raise (never echoing it)."""
    if not isinstance(value, str) or not _PASSPHRASE_RE.fullmatch(value):
        raise ValueError("SRT passphrase must be 10..79 characters from [A-Za-z0-9_-]")
    return value


def is_loopback_bind(address: RelayAddress) -> bool:
    return address.host.startswith("127.")


def validate_exposure(address: RelayAddress, phone: Credentials | None, passphrase: str | None) -> None:
    """Refuse a phone-reachable ingest that is not protected.

    Loopback needs nothing. Any other bind (LAN, Tailscale, 0.0.0.0) needs a publish login and an
    SRT passphrase. ``RelayAddress`` itself already refuses a wildcard bind unless it was asked for
    explicitly (``allow_wildcard``).
    """
    if is_loopback_bind(address):
        if passphrase is not None:
            validate_passphrase(passphrase)
        return
    if phone is None:
        raise ValueError("a phone-reachable ingest bind requires publish credentials")
    if passphrase is None:
        raise ValueError(
            "a phone-reachable ingest bind requires an SRT passphrase: set INGEST_SRT_PASSPHRASE "
            "(10..79 characters from A-Za-z0-9_-)"
        )
    validate_passphrase(passphrase)
    if len(phone.password) < MIN_EXPOSED_PASSWORD:
        raise ValueError(f"a phone-reachable ingest bind requires a publish password of {MIN_EXPOSED_PASSWORD}+ characters")


def tuning_warnings(srt_latency_ms: int, hold_after_s: float) -> list[str]:
    """Fixed-text warnings for a config that will misbehave (no values from secrets, safe to log).

    SRT recovers a lost packet within about one latency window, so playback can pause for that long
    without the link being down. A transport hold shorter than that holds on every retransmit burst.
    """
    out: list[str] = []
    if hold_after_s <= 0:
        out.append("ingest_hold_after_s is 0: the transport hold would assert permanently")
    elif hold_after_s < 2.0 * srt_latency_ms / 1000.0:
        out.append(
            "ingest_hold_after_s is below twice srt_latency_ms: expect false transport holds on retransmit bursts"
        )
    if srt_latency_ms > 1000:
        out.append("srt_latency_ms above 1000 uses a large share of the 5 s delay budget")
    return out
