"""``relay.runtime`` bind, passphrase and pairing wiring (CPU only; MediaMTX is never started)."""

from __future__ import annotations

import pytest

from contracts.config import Config, RelayConfig
from relay.runtime import _bind_address, _srt_passphrase


def _config(**relay) -> Config:
    return Config(_env_file=None, relay=RelayConfig(**relay))


def test_default_bind_is_loopback_without_a_passphrase():
    address = _bind_address(_config(), {})
    assert address.host == "127.0.0.1" and not address.allow_wildcard and address.rtsp_port == 8554
    assert _srt_passphrase(address, {}) is None


def test_wildcard_bind_written_in_config_is_on_purpose_and_still_needs_a_passphrase():
    address = _bind_address(_config(ingest_bind_host="0.0.0.0"), {})
    assert address.host == "0.0.0.0" and address.allow_wildcard and address.connect_host == "127.0.0.1"
    with pytest.raises(ValueError, match="INGEST_SRT_PASSPHRASE"):  # fail closed, no generated secret
        _srt_passphrase(address, {})


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20"])
def test_non_loopback_bind_fails_closed_without_a_passphrase_and_accepts_a_valid_one(host):
    address = _bind_address(_config(), {"INGEST_BIND_HOST": host})
    with pytest.raises(ValueError, match="INGEST_SRT_PASSPHRASE"):
        _srt_passphrase(address, {"INGEST_BIND_HOST": host, "INGEST_SRT_PASSPHRASE": "  "})
    assert _srt_passphrase(address, {"INGEST_SRT_PASSPHRASE": " abcdefghij-_0123 "}) == "abcdefghij-_0123"


@pytest.mark.parametrize("bad", ["short", "a" * 80, "has space inside", "pass:phrase!", "p\u00e4ssphrase123"])
def test_bad_passphrase_format_is_rejected_without_echoing_it(bad):
    for host in ("127.0.0.1", "0.0.0.0"):
        address = _bind_address(_config(), {"INGEST_BIND_HOST": host})
        with pytest.raises(ValueError, match="10..79") as info:
            _srt_passphrase(address, {"INGEST_SRT_PASSPHRASE": bad})
        assert bad not in str(info.value)


def test_passphrase_boundaries_10_and_79_are_accepted():
    address = _bind_address(_config(), {})
    assert _srt_passphrase(address, {"INGEST_SRT_PASSPHRASE": "a" * 10}) == "a" * 10
    assert _srt_passphrase(address, {"INGEST_SRT_PASSPHRASE": "-" + "b" * 78}) == "-" + "b" * 78


def test_env_overrides_the_bind_host_and_supplies_a_fixed_passphrase():
    address = _bind_address(_config(ingest_bind_host="0.0.0.0"), {"INGEST_BIND_HOST": "192.168.1.20"})
    assert address.host == "192.168.1.20" and not address.allow_wildcard
    assert (
        _srt_passphrase(address, {"INGEST_SRT_PASSPHRASE": "fixed-passphrase-123"}) == "fixed-passphrase-123"
    )


def test_public_addresses_are_refused_even_from_config():
    with pytest.raises(ValueError):
        _bind_address(_config(ingest_bind_host="8.8.8.8"), {})
