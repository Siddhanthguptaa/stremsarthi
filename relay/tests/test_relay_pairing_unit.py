"""Phone pairing payloads: Moblin import URL and generic URLs round-trip and never leak secrets (CPU only)."""

from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from relay.pairing import MAX_PAYLOAD_CHARS, build_pairing, pairing_host
from relay.security import Credentials
from relay.srt import generate_passphrase

PHONE = Credentials("uabc123", "pw-Secret_Value-9")
PASSPHRASE = "Pass0123456789phrase"


def _decode_moblin(url: str) -> dict:
    assert url.startswith("moblin://?")
    return json.loads(unquote(url[len("moblin://?") :]))


def test_moblin_payload_round_trips_the_srt_url():
    info = build_pairing("192.168.1.50", PHONE, srt_passphrase=PASSPHRASE, latency_ms="4g")
    decoded = _decode_moblin(info.moblin_url())
    (stream,) = decoded["streams"]
    assert stream["selected"] is True and stream["name"] == "Stream Sarthi"
    assert stream["url"] == info.srt_url
    parts = urlsplit(stream["url"])
    assert (parts.scheme, parts.hostname, parts.port) == ("srt", "192.168.1.50", 8890)
    query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
    assert query["streamid"] == f"publish:camera:{PHONE.user}:{PHONE.password}"
    assert query["latency"] == "400" and query["passphrase"] == PASSPHRASE and query["pbkeylen"] == "16"


def test_generic_rtmp_url_and_moblin_rtmp_payload():
    info = build_pairing("10.0.0.7", PHONE, rtmp_port=1936, latency_ms="lan")
    assert info.rtmp_url == f"rtmp://10.0.0.7:1936/camera?user={PHONE.user}&pass={PHONE.password}"
    assert _decode_moblin(info.moblin_url("rtmp"))["streams"][0]["url"] == info.rtmp_url
    assert "passphrase" not in info.srt_url, "no passphrase supplied, none invented"
    assert "latency=120" in info.srt_url
    with pytest.raises(ValueError):
        info.moblin_url("hls")


def test_payload_fits_a_qr_code():
    info = build_pairing("100.101.102.103", Credentials.generate(), srt_passphrase=generate_passphrase(79))
    assert len(info.moblin_url()) <= MAX_PAYLOAD_CHARS


@pytest.mark.parametrize(
    "host",
    ["8.8.8.8", "0.0.0.0", "localhost", "example.com", "::1", "172.32.0.1", "", None, "192.168.1.1/24"],
)
def test_hosts_outside_loopback_lan_tailscale_are_refused(host):
    with pytest.raises(ValueError):
        pairing_host(host)
    if host not in ("0.0.0.0", ""):  # the wildcard is resolved by build_pairing (tested separately)
        with pytest.raises(ValueError):
            build_pairing(host, PHONE)


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "10.1.2.3", "172.16.5.5", "192.168.0.9", "100.64.0.1", "100.127.1.1"]
)
def test_loopback_lan_and_tailscale_hosts_are_accepted(host):
    assert build_pairing(host, PHONE).host == host


def test_bad_inputs_are_rejected_without_echoing_secrets():
    with pytest.raises(ValueError) as error:
        build_pairing("192.168.1.2", PHONE, srt_passphrase="short")
    assert "short" not in str(error.value)
    for kwargs in (
        {"rtmp_port": 80},
        {"srt_port": 70000},
        {"latency_ms": 5},
        {"stream_name": ""},
        {"stream_name": "x" * 41},
    ):
        with pytest.raises(ValueError):
            build_pairing("192.168.1.2", PHONE, **kwargs)


def test_secrets_are_hidden_from_repr_str_and_logs(caplog):
    caplog.set_level(logging.DEBUG)
    info = build_pairing("192.168.1.50", PHONE, srt_passphrase=PASSPHRASE)
    info.moblin_url()
    info.redacted()
    for text in (repr(info), str(info), f"{info}", json.dumps(info.redacted())):
        for secret in (PHONE.password, PHONE.user, PASSPHRASE):
            assert secret not in text
    assert not caplog.records, "pairing never logs"
    assert info.redacted()["srt_encrypted"] is True
    assert build_pairing("192.168.1.50", PHONE).redacted()["srt_encrypted"] is False
    private = info.private_payload()
    assert PASSPHRASE in private["srt_url"], "the private payload is the one place the secret travels"


def test_module_has_no_logging_or_file_output():
    import relay.pairing as pairing

    source = open(pairing.__file__, encoding="utf-8").read()
    for needle in ("logging", "print(", "open(", "write("):
        assert needle not in source.split('"""', 2)[2], needle
