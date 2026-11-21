"""``relay.runtime`` bind, passphrase and pairing wiring (CPU only; MediaMTX is never started)."""

from __future__ import annotations

import pytest

from contracts.config import Config, RelayConfig
from relay.runtime import _bind_address, _srt_passphrase
from relay.srt import validate_passphrase


def _config(**relay) -> Config:
    return Config(_env_file=None, relay=RelayConfig(**relay))


def test_default_bind_is_loopback_without_a_passphrase():
    address = _bind_address(_config(), {})
    assert address.host == "127.0.0.1" and not address.allow_wildcard and address.rtsp_port == 8554
    assert _srt_passphrase(address, {}) is None


def test_wildcard_bind_written_in_config_is_on_purpose_and_gets_a_generated_passphrase():
    address = _bind_address(_config(ingest_bind_host="0.0.0.0"), {})
    assert address.host == "0.0.0.0" and address.allow_wildcard and address.connect_host == "127.0.0.1"
    passphrase = _srt_passphrase(address, {})
    assert passphrase is not None and validate_passphrase(passphrase) == passphrase
    assert _srt_passphrase(address, {}) != passphrase, "a new one every run"


def test_env_overrides_the_bind_host_and_supplies_a_fixed_passphrase():
    address = _bind_address(_config(ingest_bind_host="0.0.0.0"), {"INGEST_BIND_HOST": "192.168.1.20"})
    assert address.host == "192.168.1.20" and not address.allow_wildcard
    assert (
        _srt_passphrase(address, {"INGEST_SRT_PASSPHRASE": "fixed-passphrase-123"}) == "fixed-passphrase-123"
    )


def test_public_addresses_are_refused_even_from_config():
    with pytest.raises(ValueError):
        _bind_address(_config(ingest_bind_host="8.8.8.8"), {})
