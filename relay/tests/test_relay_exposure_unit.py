"""Phone-reachable ingest: exposure checks, pairing host resolution, .env subset (CPU only, no sockets)."""

from __future__ import annotations

import pytest

from relay.netenv import effective_env, read_dotenv
from relay.pairing import build_pairing, resolve_pairing_host
from relay.security import Credentials, RelayAddress
from relay.srt import validate_exposure

PHONE = Credentials("phoneuser", "p" * 24)
GOOD = "abcdefghij-_0123"
WILD = RelayAddress("0.0.0.0", allow_wildcard=True)


@pytest.mark.parametrize("address", [WILD, RelayAddress("192.168.1.20")])
def test_non_loopback_requires_login_passphrase_and_strong_password(address):
    validate_exposure(address, PHONE, GOOD)
    with pytest.raises(ValueError, match="credentials"):
        validate_exposure(address, None, GOOD)
    with pytest.raises(ValueError, match="INGEST_SRT_PASSPHRASE"):
        validate_exposure(address, PHONE, None)
    with pytest.raises(ValueError, match="passphrase"):
        validate_exposure(address, PHONE, "short")
    with pytest.raises(ValueError, match="password"):
        validate_exposure(address, Credentials("u", "weak"), GOOD)


def test_loopback_needs_nothing_but_a_given_passphrase_is_still_validated():
    validate_exposure(RelayAddress(), None, None)
    with pytest.raises(ValueError):
        validate_exposure(RelayAddress(), None, "bad pass")


def test_specific_bind_is_dialled_as_is():
    assert resolve_pairing_host("192.168.1.20", {"PAIRING_HOST": "10.0.0.5"}) == ("192.168.1.20", "bind")


def test_wildcard_prefers_pairing_host_then_autodetect():
    never = lambda: pytest.fail("must not detect when PAIRING_HOST is set")  # noqa: E731
    assert resolve_pairing_host("0.0.0.0", {"PAIRING_HOST": " 10.0.0.5 "}, detect=never) == (
        "10.0.0.5",
        "PAIRING_HOST",
    )
    assert resolve_pairing_host("0.0.0.0", {}, detect=lambda: "192.168.1.7") == ("192.168.1.7", "autodetect")


@pytest.mark.parametrize("override", ["8.8.8.8", "0.0.0.0", "localhost", "192.168.1.1/24"])
def test_bad_pairing_host_override_is_refused(override):
    with pytest.raises(ValueError, match="PAIRING_HOST"):
        resolve_pairing_host("0.0.0.0", {"PAIRING_HOST": override}, detect=lambda: "192.168.1.7")


@pytest.mark.parametrize("found", [None, "127.0.0.1", "8.8.8.8"])
def test_autodetect_failure_names_the_override(found):
    with pytest.raises(ValueError, match="PAIRING_HOST"):
        resolve_pairing_host("0.0.0.0", {}, detect=lambda: found)


def test_build_pairing_resolves_the_wildcard_bind(monkeypatch):
    monkeypatch.setenv("PAIRING_HOST", "192.168.1.7")
    info = build_pairing("0.0.0.0", PHONE, srt_passphrase=GOOD)
    assert info.host == "192.168.1.7"
    assert info.srt_url.startswith("srt://192.168.1.7:8890?") and f"passphrase={GOOD}" in info.srt_url


def test_dotenv_subset_is_read_and_the_environment_wins(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# c\nINGEST_BIND_HOST=0.0.0.0\nexport PAIRING_HOST='192.168.1.9'\nOTHER=1\nINGEST_SRT_PASSPHRASE=\n",
        encoding="utf-8",
    )
    assert read_dotenv(env_file) == {"INGEST_BIND_HOST": "0.0.0.0", "PAIRING_HOST": "192.168.1.9"}
    merged = effective_env({"PAIRING_HOST": "10.0.0.2"}, dotenv=env_file)
    assert merged["PAIRING_HOST"] == "10.0.0.2" and merged["INGEST_BIND_HOST"] == "0.0.0.0"
    assert "INGEST_BIND_HOST" not in effective_env({}), "an explicit mapping reads no file"
    assert read_dotenv(tmp_path / "missing") == {}
