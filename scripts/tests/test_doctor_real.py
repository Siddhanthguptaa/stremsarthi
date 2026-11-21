"""doctor --real: key presence (names only), port logic and exit code. No servers, no models, no network."""

from __future__ import annotations

import socket

from scripts import doctor

FAKE = "sk-FAKE-SECRET-VALUE-123"


def test_env_names_present_returns_names_never_values(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        f"# comment\nGROQ_API_KEY={FAKE}\nSARVAM_API_KEY=\nTWITCH_CLIENT_ID='  '\nexport HF_TOKEN=\"{FAKE}\"\n"
        f"YOUTUBE_STREAM_KEY = {FAKE}\n",
        encoding="utf-8",
    )
    present = doctor.env_names_present(env, environ={"TWITCH_STREAM_KEY": FAKE, "OTHER": ""}, names=doctor.KEYS)
    assert present == {"GROQ_API_KEY", "HF_TOKEN", "YOUTUBE_STREAM_KEY", "TWITCH_STREAM_KEY"}
    assert all(FAKE not in n for n in present)


def test_missing_env_file_and_empty_environment():
    assert doctor.env_names_present(None, environ={}, names=doctor.KEYS) == set()


def test_real_keys_output_never_contains_a_value(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"GROQ_API_KEY={FAKE}\n", encoding="utf-8")
    items = doctor.real_keys(doctor.env_names_present(env, environ={}, names=doctor.KEYS))
    blob = repr([vars(i) for i in items])
    assert FAKE not in blob
    by = {i.name: i for i in items}
    assert by["key GROQ_API_KEY"].status == "PASS"
    sarvam = by["key SARVAM_API_KEY"]
    assert sarvam.status == "OPTIONAL" and "Without it" in sarvam.detail and ".env" in sarvam.fix
    assert not any(i.required for i in items)


def test_port_free_detects_a_busy_tcp_and_udp_port():
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        s.listen()
        assert doctor.port_free(s.getsockname()[1]) is False
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
        u.bind(("0.0.0.0", 0))
        assert doctor.port_free(u.getsockname()[1], udp=True) is False


def test_real_ports_required_fix_lists_only_busy_ports():
    (ok,) = doctor.real_ports(lambda port, udp: True)
    assert ok.status == "PASS" and ok.required
    (bad,) = doctor.real_ports(lambda port, udp: port != 1935)
    assert bad.status == "FIX" and bad.required and "1935" in bad.detail and "8000" not in bad.detail


def test_exit_code_depends_only_on_required_items():
    optional_fail = doctor.Item("OPTIONAL", "x")
    required_ok = doctor.Item("PASS", "y", required=True)
    required_bad = doctor.Item("FIX", "z", required=True)
    assert doctor.real_exit_code([optional_fail, required_ok]) == 0
    assert doctor.real_exit_code([optional_fail, required_ok, required_bad]) == 1


def test_network_reports_only_ok_or_blocked():
    items = doctor.real_network(lambda host: host != "api.groq.com")
    by = {i.name: i for i in items}
    assert by["network api.sarvam.ai"].detail == "OK"
    assert by["network api.groq.com"].status == "OPTIONAL" and "blocked" in by["network api.groq.com"].detail


def test_memory_cap_warning_follows_ss_mem_max():
    names = [i.name for i in doctor.real_resources({"SS_MEM_MAX": "3500M"})]
    assert "ss.sh memory cap" in names
    names = [i.name for i in doctor.real_resources({"SS_MEM_MAX": "7G"})]
    assert "ss.sh memory cap" not in names
