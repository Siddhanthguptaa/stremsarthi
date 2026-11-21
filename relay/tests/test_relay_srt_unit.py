"""SRT tuning for 4G: latency presets, passphrase rules, wildcard bind rules, MediaMTX config (CPU, no network)."""

from __future__ import annotations

import json

import pytest

from relay.mediamtx import MediaMTXSupervisor, render_config
from relay.security import Credentials, RelayAddress, redact
from relay.srt import (
    SRT_LATENCY_PRESETS,
    generate_passphrase,
    recommended_latency_ms,
    resolve_latency_ms,
    tuning_warnings,
    validate_exposure,
    validate_passphrase,
)


def test_presets_cover_lan_and_4g_ranges_from_the_spec():
    assert SRT_LATENCY_PRESETS["lan"] == 120
    assert 300 <= SRT_LATENCY_PRESETS["4g"] <= 500 and 300 <= SRT_LATENCY_PRESETS["4g_weak"] <= 500
    assert (
        resolve_latency_ms("LAN") == 120
        and resolve_latency_ms("4g") == 400
        and resolve_latency_ms(350) == 350
    )
    assert resolve_latency_ms("350") == 350


@pytest.mark.parametrize("bad", [19, 2001, -1, True, 1.5, "fast", "", None])
def test_latency_rejects_out_of_range_and_unknown(bad):
    with pytest.raises(ValueError):
        resolve_latency_ms(bad)


def test_recommended_latency_follows_rtt_and_loss():
    assert recommended_latency_ms(10) == 120, "never below the LAN default"
    assert 300 <= recommended_latency_ms(80, 0.5) <= 500  # typical 4G
    assert recommended_latency_ms(80, 2.0) > recommended_latency_ms(80, 0.5)
    assert recommended_latency_ms(1000, 20) == 2000, "clamped"
    with pytest.raises(ValueError):
        recommended_latency_ms(-1)


def test_passphrase_generation_and_validation():
    a, b = generate_passphrase(), generate_passphrase()
    assert a != b and len(a) == 32 and validate_passphrase(a) == a
    for bad in ("short", "x" * 80, "has space 123456", "ünicode-pass-123", "a&b=c-1234567", None):
        with pytest.raises(ValueError) as error:
            validate_passphrase(bad)
        assert str(bad) not in str(error.value) or bad is None, "the error never echoes the value"
    with pytest.raises(ValueError):
        generate_passphrase(5)


def test_srt_url_with_and_without_passphrase():
    address = RelayAddress("192.168.1.20", rtmp_port=19350, srt_port=18890, rtsp_port=18554, api_port=19997)
    creds = Credentials("alice", "s3cret-_x")
    plain = address.srt("publish", "camera", creds, latency_ms=400)
    assert (
        "latency=400000" in plain
        and "passphrase" not in plain
        and plain.startswith("srt://192.168.1.20:18890?")
    )
    secured = address.srt("publish", "camera", creds, latency_ms=400, passphrase="0123456789abcdef")
    assert secured.endswith("&passphrase=0123456789abcdef&pbkeylen=16")
    with pytest.raises(ValueError):
        address.srt("publish", "camera", creds, passphrase="short")


def test_wildcard_bind_needs_an_explicit_flag_and_dials_loopback():
    with pytest.raises(ValueError):
        RelayAddress("0.0.0.0")
    with pytest.raises(ValueError):
        RelayAddress("0.0.0.0", allow_wildcard=False)
    with pytest.raises(ValueError):
        RelayAddress("8.8.8.8", allow_wildcard=True), "the flag only legalises 0.0.0.0"
    wild = RelayAddress("0.0.0.0", allow_wildcard=True)
    assert wild.connect_host == "127.0.0.1"
    assert wild.rtmp("camera").startswith("rtmp://127.0.0.1:"), "in-process clients never dial 0.0.0.0"
    assert wild.srt("read", "shielded").startswith("srt://127.0.0.1:")
    assert wild.api_base().startswith("http://127.0.0.1:") and wild.rtsp("shielded").startswith(
        "rtsp://127.0.0.1:"
    )


def test_validate_exposure_rules():
    phone = Credentials.generate()
    loop = RelayAddress("127.0.0.1")
    validate_exposure(loop, phone, None)  # loopback: nothing required
    validate_exposure(loop, None, generate_passphrase())
    for address in (
        RelayAddress("192.168.1.20"),
        RelayAddress("100.100.1.2"),
        RelayAddress("0.0.0.0", allow_wildcard=True),
    ):
        with pytest.raises(ValueError, match="passphrase"):
            validate_exposure(address, phone, None)
        with pytest.raises(ValueError, match="credentials"):
            validate_exposure(address, None, generate_passphrase())
        validate_exposure(address, phone, generate_passphrase())
    with pytest.raises(ValueError):
        validate_exposure(RelayAddress("10.0.0.2"), phone, "short")


def test_supervisor_refuses_an_unprotected_phone_reachable_bind_before_spawning():
    spawned: list[object] = []
    with pytest.raises(ValueError, match="passphrase"):
        MediaMTXSupervisor(
            RelayAddress("0.0.0.0", allow_wildcard=True),
            binary="/nonexistent/mediamtx",
            popen=lambda *a, **k: spawned.append(a),
            check_ports=False,
        )
    assert not spawned
    sup = MediaMTXSupervisor(
        RelayAddress("0.0.0.0", allow_wildcard=True),
        binary="/nonexistent/mediamtx",
        srt_passphrase=generate_passphrase(),
        srt_latency_ms=400,
        check_ports=False,
    )
    assert sup.publish_url("srt").count("passphrase=") == 1 and "latency=400000" in sup.publish_url("srt")
    assert sup.publish_url("rtmp").startswith("rtmp://127.0.0.1:")


def test_render_config_carries_latency_and_passphrase_for_the_camera_path_only():
    address = RelayAddress("0.0.0.0", allow_wildcard=True)
    phone, internal = Credentials.generate(), Credentials.generate()
    secret = "-leading-dash-pass-1234"  # a YAML hazard unless quoted
    text = render_config(address, phone, internal, srt_latency_ms=450, srt_passphrase=secret)
    assert "srtMinLatency: 450ms" in text and "srtAddress: 0.0.0.0:" in text
    assert f"srtPublishPassphrase: {json.dumps(secret)}" in text
    assert text.index("camera:") < text.index("srtPublishPassphrase") < text.index("shielded:")
    assert "rtspAddress: 127.0.0.1:" in text and "apiAddress: 127.0.0.1:" in text, (
        "RTSP and API stay on loopback"
    )
    assert "srtPublishPassphrase" not in render_config(RelayAddress(), phone, internal)
    users = [u for u in json.loads(text.split("authInternalUsers: ", 1)[1].split("\n", 1)[0])]
    assert next(u for u in users if u["user"] == internal.user)["ips"] == ["127.0.0.1"]
    assert next(u for u in users if u["user"] == phone.user)["ips"] == [], (
        "the phone may come from any address, but must log in"
    )


def test_passphrase_never_reaches_logs_or_redaction_output():
    line = "SRT conn failed streamid=publish:camera:u1:p1 passphrase=Sup3rSecretPhrase&pbkeylen=16"
    assert "Sup3rSecretPhrase" not in redact(line)
    assert "Sup3rSecretPhrase" not in redact("GET /?passphrase=Sup3rSecretPhrase x")


def test_tuning_warnings():
    assert tuning_warnings(120, 1.0) == []
    assert tuning_warnings(400, 1.0) == [], "1.0 s hold with 400 ms latency is fine (>= 2 x latency)"
    assert any("false transport holds" in w for w in tuning_warnings(600, 1.0))
    assert any("permanently" in w for w in tuning_warnings(120, 0.0))
    assert any("5 s" in w for w in tuning_warnings(1500, 4.0))
