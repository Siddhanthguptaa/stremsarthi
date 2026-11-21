"""Relay address and credential rules (ported from IRL Shield tests/m2/test_security.py)."""

from __future__ import annotations

import json

import pytest

from relay.security import Credentials, RelayAddress, is_allowed_bind, mediamtx_users, redact


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "::", "::1", "100.63.255.255", "100.128.0.0", "localhost", "8.8.8.8", "172.32.0.1"]
)
def test_bind_hosts_outside_loopback_lan_tailscale_are_refused(host):
    assert not is_allowed_bind(host)
    with pytest.raises(ValueError):
        RelayAddress(host)


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.255.255.254",
        "10.1.2.3",
        "172.16.0.9",
        "192.168.1.20",
        "100.64.0.0",
        "100.80.90.100",
        "100.127.255.255",
    ],
)
def test_loopback_lan_and_tailscale_binds_are_allowed(host):
    assert RelayAddress(host).host == host


def test_ports_must_be_unprivileged_and_distinct():
    with pytest.raises(ValueError):
        RelayAddress("127.0.0.1", rtmp_port=80)
    with pytest.raises(ValueError):
        RelayAddress("127.0.0.1", rtmp_port=9000, srt_port=9000)


def test_default_ports_match_the_config_defaults():
    address = RelayAddress()
    assert (address.host, address.rtmp_port, address.srt_port) == ("127.0.0.1", 1935, 8890)


def test_credentials_are_hidden_and_unique():
    phone, internal = Credentials.generate(), Credentials.generate()
    assert phone.password != internal.password
    for text in (repr(phone), str(phone)):
        assert phone.password not in text
        assert phone.user not in text


def test_credentials_reject_characters_that_could_break_urls():
    for bad in ("a:b", "a b", "a&b", "", "x" * 200):
        with pytest.raises(ValueError):
            Credentials("user", bad)


def test_user_list_permissions_and_ips():
    address = RelayAddress("192.168.1.20")
    phone, internal = Credentials.generate(), Credentials.generate()
    users = mediamtx_users(address, phone, internal)
    by_user = {(u["user"], tuple(u["ips"])): u["permissions"] for u in users}
    assert by_user[(phone.user, ())] == [{"action": "publish", "path": "camera"}]
    internal_perms = next(u for u in users if u["user"] == internal.user)
    assert internal_perms["permissions"] == [
        {"action": "read", "path": "camera"},
        {"action": "publish", "path": "shielded"},
    ]
    assert set(internal_perms["ips"]) == {"127.0.0.1", "192.168.1.20"}
    anonymous = [u for u in users if u["user"] == "any"]
    local = {"127.0.0.1", "192.168.1.20"}  # this machine: loopback and the bind address itself
    assert all(set(u["ips"]) <= local for u in anonymous), "anonymous access is this machine only"
    assert all(u["ips"] == ["127.0.0.1"] for u in anonymous if {"action": "api"} in u["permissions"])
    assert {p["action"] for u in anonymous for p in u["permissions"]} == {"read", "api"}
    assert not any(p["path"] == "camera" for u in anonymous for p in u["permissions"] if "path" in p)
    json.dumps(users)  # serialisable


def test_url_builders():
    address = RelayAddress("127.0.0.1", rtmp_port=19350, srt_port=18890, rtsp_port=18554)
    creds = Credentials("alice", "s3cret-_x")
    assert address.rtmp("camera", creds) == "rtmp://127.0.0.1:19350/camera?user=alice&pass=s3cret-_x"
    srt = address.srt("publish", "camera", creds, latency_ms=300)
    assert "streamid=publish:camera:alice:s3cret-_x" in srt and "latency=300000" in srt
    assert address.rtsp("camera", creds) == "rtsp://alice:s3cret-_x@127.0.0.1:18554/camera"
    assert address.rtsp("shielded") == "rtsp://127.0.0.1:18554/shielded"
    with pytest.raises(ValueError):
        address.rtmp("other")
    with pytest.raises(ValueError):
        address.srt("write", "camera")


def test_no_twitch_or_tailscale_coupling_left():
    import relay.security as security

    assert not hasattr(security, "TwitchDestination")
    source = open(security.__file__, encoding="utf-8").read()
    assert "bandwidthtest" not in source and "naie" not in source


@pytest.mark.parametrize(
    "line",
    [
        "publishing rtmps://a.rtmps.youtube.com:443/live2/abcd-efgh-ijkl-mnop failed",
        "connect rtsp://user:hunter2@127.0.0.1:8554/camera ok",
        "bad request ?user=bob&pass=hunter2",
        "path /live2/abcd-efgh-ijkl-mnop",
    ],
)
def test_redact_removes_urls_and_keys(line):
    cleaned = redact(line)
    for secret in ("hunter2", "abcd-efgh-ijkl-mnop", "youtube.com", "bob"):
        assert secret not in cleaned


def _raw_read_entries(users):
    return [
        u for u in users if any(p.get("path") == "camera" and p["action"] == "read" for p in u["permissions"])
    ]


def test_raw_preview_entry_is_absent_by_default():
    address = RelayAddress("192.168.1.20")
    phone, internal = Credentials.generate(), Credentials.generate()
    users = mediamtx_users(address, phone, internal)
    assert len(users) == 4
    assert [u["user"] for u in _raw_read_entries(users)] == [internal.user], (
        "only the engine login reads camera"
    )


def test_raw_preview_entry_is_loopback_read_of_camera_only():
    address = RelayAddress("192.168.1.20")
    phone, internal = Credentials.generate(), Credentials.generate()
    users = mediamtx_users(address, phone, internal, demo_raw_preview=True)
    assert len(users) == 5
    extra = users[-1]
    assert extra == {
        "user": "any",
        "ips": ["127.0.0.1", "192.168.1.20"],
        "permissions": [{"action": "read", "path": "camera"}],
    }
    assert users[:4] == mediamtx_users(address, phone, internal), "nothing else changes"
    anonymous = [u for u in users if u["user"] == "any"]
    assert all(set(u["ips"]) <= {"127.0.0.1", "192.168.1.20"} for u in anonymous), "never a remote address"
    perms = {(p["action"], p.get("path")) for u in anonymous for p in u["permissions"]}
    assert perms == {("read", "shielded"), ("api", None), ("read", "camera")}
    assert not any(a == "publish" for a, _ in perms), "no anonymous publish on any path"


def test_runtime_turns_the_preview_on_only_for_env_value_1(caplog):
    from relay import runtime

    assert runtime.ENV_DEMO_RAW_PREVIEW == "DEMO_RAW_PREVIEW"
    source = open(runtime.__file__, encoding="utf-8").read()
    assert 'env.get(ENV_DEMO_RAW_PREVIEW) == "1"' in source and source.count("demo_raw_preview: enabled") == 1


def _users(host, **kw):
    address = RelayAddress(host, allow_wildcard=host == "0.0.0.0")
    return mediamtx_users(address, Credentials("phoneuser", "p" * 20), Credentials("internal", "i" * 20), **kw)


def _reader_ips(users, path):
    return [
        u["ips"]
        for u in users
        if u["user"] == "any" and {"action": "read", "path": path} in u["permissions"]
    ]


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0", "192.168.1.20"])
def test_publisher_always_has_a_login_and_anonymous_never_publishes(host):
    users = _users(host, demo_raw_preview=True)
    assert all(u["user"] != "any" or all(p["action"] != "publish" for p in u["permissions"]) for u in users)
    phone = next(u for u in users if u["user"] == "phoneuser")
    assert phone["pass"] and phone["permissions"] == [{"action": "publish", "path": "camera"}]


def test_wildcard_bind_keeps_anonymous_readers_and_api_on_loopback():
    users = _users("0.0.0.0", demo_raw_preview=True)
    assert _reader_ips(users, "shielded") == [["127.0.0.1"]] == _reader_ips(users, "camera")
    api = [u for u in users if {"action": "api"} in u["permissions"]]
    assert api and all(u["ips"] == ["127.0.0.1"] for u in api)


def test_lan_bind_lets_the_local_preview_reader_in_from_the_bind_address_only():
    users = _users("192.168.1.20", demo_raw_preview=True)
    assert _reader_ips(users, "shielded") == [["127.0.0.1", "192.168.1.20"]]
    assert _reader_ips(users, "camera") == [["127.0.0.1", "192.168.1.20"]]
    api = [u for u in users if {"action": "api"} in u["permissions"]]
    assert all(u["ips"] == ["127.0.0.1"] for u in api)
    assert _reader_ips(_users("192.168.1.20"), "camera") == [], "raw camera read stays off by default"
