"""Safety report (schema, privacy guard, printable, export), the recorder, and the risk snapshot."""

from __future__ import annotations

import copy
import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.api.recorder import SessionRecorder
from app.api.report import ReportPrivacyError, build_report, render_html, to_printable, validate_report
from contracts import (
    Category,
    ChatAction,
    ChatVerdict,
    EngineStatus,
    HoldCommand,
    HoldSource,
    Modality,
    Quad,
    RiskMeter,
    RiskSignal,
    SafetyEvent,
    Topic,
)

T0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)
CATS = [
    (Modality.VIDEO, Category.FACE, "masked"),
    (Modality.VIDEO, Category.UPI_QR, "masked"),
    (Modality.AUDIO, Category.SPOKEN_PHONE, "muted"),
    (Modality.AUDIO, Category.SPOKEN_UNVERIFIED, "muted"),
    (Modality.CHAT, Category.CHAT_ABUSE, "hidden"),
    (Modality.GUARD, Category.MUSIC, "warned"),
]


def ev(i: int) -> SafetyEvent:
    modality, category, action = CATS[i % len(CATS)]
    return SafetyEvent(
        event_id=f"ev{i:06d}",
        session_id="engine-session",
        wall_ts=(T0 + timedelta(seconds=i)).isoformat(),
        pts=float(i),
        modality=modality,
        category=category,
        action=action,  # type: ignore[arg-type]
        confidence=0.5 + (i % 5) / 10,
        bbox=Quad.from_xyxy(10, 20, 110, 120) if modality is Modality.VIDEO else None,
        duration_ms=1000 if modality is Modality.AUDIO else None,
        source="test",
        detail_code="ok",
    )


def status(**kw) -> EngineStatus:
    base = dict(
        state="live", hold_active=False, hold_sources=(), delay_s_p50=4.0, fps=30.0, cpu_cores=1.0,
        vram_mb=100, allowlist_version=0, lanes_stale=(), protect_seconds=0.0, contract_version="1.0.0",
    )  # fmt: skip
    base.update(kw)
    return EngineStatus(**base)  # type: ignore[arg-type]


@pytest.fixture
def recorder(tmp_path: Path) -> SessionRecorder:
    r = SessionRecorder(tmp_path)
    r.begin("sess-test-0001", T0.isoformat(), "dev", 4.0)
    return r


# --- recorder and report ------------------------------------------------------------------------


def test_report_from_500_events_validates_and_has_no_images_or_text(recorder, tmp_path):
    for i in range(500):
        recorder.observe(Topic.SAFETY_EVENT, ev(i))
    log = recorder.end()
    report = build_report(log)
    validate_report(report)
    assert report["schema_version"] == 1 and report["totals"]["events"] == 500
    assert len(report["timeline"]) == 500
    assert report["timeline"][0].keys() == {
        "event_id", "wall_ts", "pts", "modality", "category", "action", "confidence", "bbox",
        "duration_ms", "source", "detail_code",
    }  # fmt: skip
    assert report["totals"]["by_category"]["face"]["count"] == 84
    assert report["totals"]["by_modality"]["video"] == 168
    assert report["timeline"] == sorted(report["timeline"], key=lambda r: r["wall_ts"])
    assert report["failsafe"]["audio_failsafe_seconds"] == 83.0  # 83 spoken_unverified events of 1 s
    blob = json.dumps(report)
    assert "base64" not in blob and "data:image" not in blob
    # events.jsonl: SafetyEvent wire objects only, mode 0600
    path = tmp_path / "sess-test-0001" / "events.jsonl"
    lines = path.read_text().splitlines()
    assert len(lines) == 500 and all(json.loads(x)["t"] == "SafetyEvent" for x in lines)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_event_update_with_duration_counts_once(recorder):
    first = ev(0)
    recorder.observe(Topic.SAFETY_EVENT, first)
    from dataclasses import replace

    recorder.observe(Topic.SAFETY_EVENT, replace(first, duration_ms=2500))
    report = build_report(recorder.end())
    assert report["totals"]["events"] == 1 and report["timeline"][0]["duration_ms"] == 2500


def test_privacy_guard_rejects_long_strings_blobs_and_pii(recorder):
    for i in range(3):
        recorder.observe(Topic.SAFETY_EVENT, ev(i))
    report = build_report(recorder.end())

    def bad(mutate) -> None:
        r = copy.deepcopy(report)
        mutate(r)
        with pytest.raises(ReportPrivacyError):
            validate_report(r)

    bad(lambda r: r["timeline"][0].__setitem__("detail_code", "A" * 200))
    bad(lambda r: r["notes"].append("x" * 65))
    bad(lambda r: r["timeline"][0].__setitem__("source", "iVBORw0KGgoAAAANSUhEUgAA"))
    bad(lambda r: r["timeline"][1].__setitem__("source", "data:image/png;base64,AAAA"))
    bad(lambda r: r["timeline"][1].__setitem__("detail_code", "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo0MTIzNDU2"))
    bad(lambda r: r["notes"].append("call 9876543210"))
    bad(lambda r: r["notes"].append("a.b@example.com"))
    bad(lambda r: r["notes"].append("pay me@okhdfc"))
    bad(lambda r: r["notes"].append("1234 5678 9012"))
    bad(lambda r: r["notes"].append("ABCDE1234F"))
    bad(lambda r: r["notes"].append("https://evil.example"))
    bad(lambda r: r["totals"].__setitem__("image", b"\x89PNG"))
    # ids and timestamps may hold long digit runs and are not mistaken for PII
    ok = copy.deepcopy(report)
    ok["timeline"][0]["event_id"] = "ev-9876543210"
    validate_report(ok)


def test_holds_merge_overlapping_sources_and_release_clears_only_own_source(recorder):
    cmds = [
        (HoldSource.APP, True), (HoldSource.HOTKEY, True), (HoldSource.APP, False),
        (HoldSource.HOTKEY, False), (HoldSource.CHAT, True),
    ]  # fmt: skip
    for src, on in cmds:
        recorder.observe(Topic.HOLD, HoldCommand(source=src, active=on, actor="h_x"))
    report = build_report(recorder.end())  # the last hold is still open at the end: closed by end()
    assert len(report["holds"]) == 2
    assert report["holds"][0]["sources"] == ["app", "hotkey"] and report["holds"][0]["end_ts"]
    assert report["holds"][1]["sources"] == ["chat"] and report["holds"][1]["end_ts"]


def test_risk_chat_latency_and_failsafe_sections(recorder):
    sig = lambda s, label: RiskSignal(  # noqa: E731
        wall_ts=T0.isoformat(),
        pts=None,
        source="music",
        category=Category.MUSIC,
        score=s,
        ttl_s=5,
        label=label,
    )
    recorder.observe(Topic.RISK, sig(0.3, "fp:low"))
    recorder.observe(Topic.RISK, sig(0.8, "fp:demo_03"))
    v = lambda mid, act, res: ChatVerdict(  # noqa: E731
        platform="twitch", message_id=mid, action=act, categories=(Category.CHAT_ABUSE,) if act is not ChatAction.ALLOW else (),
        confidence=0.9, reason="rule", stage="rules", latency_ms=3.0, timeout_seconds=None, platform_result=res,
    )  # fmt: skip
    recorder.observe(Topic.CHAT_VERDICT, v("m1", ChatAction.DELETE, "pending"))
    recorder.observe(Topic.CHAT_VERDICT, v("m1", ChatAction.DELETE, "done"))  # same message: counted once
    recorder.observe(Topic.CHAT_VERDICT, v("m2", ChatAction.HIDE_FROM_STREAMER, "unsupported"))
    recorder.observe(Topic.CHAT_VERDICT, v("m3", ChatAction.ALLOW, "pending"))
    for d in (3.0, 4.0, 5.0):
        recorder.observe(Topic.ENGINE_STATUS, status(delay_s_p50=d, fps=24.0 + d, protect_seconds=d))
    report = build_report(recorder.end())
    assert report["risk"]["max_level"] == "danger"
    assert [w["label"] for w in report["risk"]["warnings"]] == ["fp:demo_03"]  # only orange and up
    assert report["chat"]["by_action"] == {"allow": 1, "delete": 1, "hide": 1}
    assert report["chat"]["platform_actions"] == {"done": 1, "failed": 0, "unsupported": 1}
    assert report["chat"]["by_category"] == {"chat_abuse": 2}
    assert report["latency"] == {"delay_p50_s": 4.0, "delay_p95_s": 5.0, "fps_mean": 28.0}
    assert report["failsafe"]["protect_seconds"] == 5.0
    assert "m1" not in json.dumps(report)  # message ids are never reported


def test_disk_failure_keeps_ram_and_adds_a_note(recorder, tmp_path):
    cur = recorder.current
    cur.jsonl = tmp_path  # a directory: opening it for append fails
    recorder.observe(Topic.SAFETY_EVENT, ev(1))
    recorder.observe(Topic.SAFETY_EVENT, ev(2))
    report = build_report(recorder.end())
    assert report["totals"]["events"] == 2 and "events_file_unavailable" in report["notes"]


def test_partial_report_while_live_and_printable_shape(recorder):
    for i in range(4):
        recorder.observe(Topic.SAFETY_EVENT, ev(i))
    partial = build_report(recorder.current)
    assert partial["ended_at"] is None and "partial_report_session_live" in partial["notes"]
    p = to_printable(partial)
    assert p["partial"] is True and p["title"]["hi"]
    row = p["timeline"][1]
    assert row["offset"] == "00:01" and row["category"] == "upi_qr" and row["confidence_pct"] == 60
    assert row["category_label"]["hi"] and row["category_label"]["en"] == "UPI QR code"
    assert {c["category"] for c in p["categories"]} == {"face", "upi_qr", "spoken_phone", "spoken_unverified"}
    assert "No images" in p["privacy_note"]["en"]


def test_html_export_is_static_escaped_and_script_free(recorder):
    recorder.observe(Topic.SAFETY_EVENT, ev(0))
    report = build_report(recorder.end())
    report["notes"] = ["<script>alert(1)</script>"]
    page = render_html(report)
    assert "<script" not in page.lower() and "<img" not in page.lower() and "http" not in page.lower()
    assert "Face" in page and "चेहरा" in page


# --- API ----------------------------------------------------------------------------------------


def start_recording(app, session_id="sess-api-0001") -> None:
    app.state.recorder.begin(session_id, T0.isoformat(), "dev", 4.0)


def test_report_endpoints_roles_and_export(app, client, auth, role_headers, publish):
    start_recording(app)
    for i in range(6):
        publish(Topic.SAFETY_EVENT, ev(i))
    for _ in range(100):
        if len(app.state.recorder.current.events) == 6:
            break
        import time

        time.sleep(0.02)
    sid = "sess-api-0001"
    assert client.get(f"/api/report/{sid}").status_code == 401
    for role in ("moderator", "hotkey"):
        assert client.get(f"/api/report/{sid}", headers=role_headers(role)).status_code == 403
    live = client.get(f"/api/report/{sid}", headers=auth).json()
    assert live["ended_at"] is None and live["totals"]["events"] == 6
    # viewer-readonly may read (report only)
    assert client.get(f"/api/report/{sid}", headers=role_headers("viewer-readonly")).status_code == 200
    assert client.get("/api/report/unknown-session", headers=auth).status_code == 404
    assert client.get("/api/report/..%2Fetc", headers=auth).status_code == 404

    app.state.golive._store(app.state.recorder.end())  # as the stop task does
    done = client.get(f"/api/report/{sid}", headers=auth).json()
    assert done["ended_at"] and done["totals"]["events"] == 6
    assert sid in client.get("/api/report", headers=auth).json()["sessions"]
    printable = client.get(f"/api/report/{sid}/printable", headers=auth).json()
    assert printable["timeline"][0]["category_label"]["hi"]

    html = client.get(f"/api/report/{sid}/export?format=html", headers=auth)
    assert html.status_code == 200 and "<script" not in html.text.lower()
    assert "default-src 'none'" in html.headers["content-security-policy"]
    assert "attachment" in html.headers["content-disposition"]
    raw = client.get(f"/api/report/{sid}/export?format=json", headers=auth)
    assert json.loads(raw.text)["session_id"] == sid
    assert client.get(f"/api/report/{sid}/export?format=pdf", headers=auth).status_code == 422

    assert client.delete(f"/api/session/{sid}/data", headers=auth).json() == {"deleted": True}
    assert client.get(f"/api/report/{sid}", headers=auth).status_code == 404
    assert client.delete(f"/api/session/{sid}/data", headers=auth).status_code == 404


def test_delete_data_refused_while_session_records(app, client, auth):
    start_recording(app)
    r = client.delete("/api/session/sess-api-0001/data", headers=auth)
    assert r.status_code == 409 and r.json()["error"] == "session_active"
    assert client.delete("/api/session/bad..id/data", headers=auth).status_code == 404


def test_risk_snapshot_maps_levels_and_expires_signals(client, auth, role_headers, publish, wait_until):
    def sig(score: float, ttl: float, label: str) -> RiskSignal:
        return RiskSignal(
            wall_ts=T0.isoformat(), pts=None, source="fingerprint", category=Category.COPYRIGHT_MATCH,
            score=score, ttl_s=ttl, label=label,
        )  # fmt: skip

    assert client.get("/api/risk").status_code == 401
    assert client.get("/api/risk", headers=role_headers("moderator")).status_code == 403
    idle = client.get("/api/risk", headers=auth).json()
    assert idle["level"] == "ok" and idle["score"] == 0 and idle["signals"] == []

    publish(Topic.RISK, sig(0.6, 0.4, "fp:demo_01"))
    assert wait_until(lambda: client.get("/api/risk", headers=auth).json()["signals"])
    snap = client.get("/api/risk", headers=auth).json()
    assert (
        snap["level"] == "warn" and snap["basis"] == "signals" and snap["signals"][0]["label"] == "fp:demo_01"
    )
    assert set(snap["signals"][0]) == {"source", "category", "score", "label", "at", "expires_in_s"}
    assert wait_until(lambda: client.get("/api/risk", headers=auth).json()["level"] == "ok", timeout=3)

    publish(
        Topic.RISK_METER,
        RiskMeter(wall_ts=T0.isoformat(), level="red", score=0.9, top_sources=("fingerprint",)),
    )
    assert wait_until(lambda: client.get("/api/risk", headers=auth).json()["level"] == "danger")
    snap = client.get("/api/risk", headers=auth).json()
    assert (
        snap["meter_level"] == "red" and snap["basis"] == "meter" and snap["top_sources"] == ["fingerprint"]
    )


def test_system_notices_are_not_protection_events(recorder):
    from dataclasses import replace

    recorder.observe(Topic.SAFETY_EVENT, ev(0))
    notice = replace(
        ev(1), event_id="notice001", modality=Modality.SYSTEM, category=Category.SYSTEM_STATUS,
        action="warned", source="chat", detail_code="chat_unavailable", bbox=None, duration_ms=None,
    )  # fmt: skip
    recorder.observe(Topic.SAFETY_EVENT, notice)
    report = build_report(recorder.end())
    assert report["totals"]["events"] == 1 and report["totals"]["system_notices"] == 1
    assert "system_status" not in report["totals"]["by_category"]
    assert "system" not in report["totals"]["by_modality"]
    assert {r["category"] for r in report["timeline"]} == {CATS[0][1].value, "system_status"}  # still listed
    assert to_printable(report)["summary"][1]["value"] == 1
