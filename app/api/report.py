"""Post-stream safety report (spec 5.9, ``schema_version`` 1).

The report is a timeline of ``SafetyEvent`` facts: category, time, confidence (and box, duration and
fixed codes). It never contains an image, a crop, a transcript, a chat message, a name or any raw PII.
``validate_report`` is the guard that proves it: it rejects any string longer than 64 characters, any
embedded image or base64 blob, and anything that looks like a phone number, e-mail, UPI id, Aadhaar or
PAN number, or URL. ``build_report`` always runs it, so a report that exists has passed it.

Three shapes are produced from one report:

* the JSON report (``build_report``), the data contract of ``GET /api/report/{id}``;
* a printable shape (``to_printable``): rows with English and Hindi labels, offsets from the start of
  the stream and confidence in percent, so the web app only lays it out;
* a static HTML export (``render_html``): no script, every value escaped, served with a strict CSP.
"""

from __future__ import annotations

import html
import re
from collections import defaultdict
from typing import Any

from contracts import SafetyEvent

from .events import parse_ts
from .recorder import SessionLog
from .risk import API_LEVEL, LEVELS

SCHEMA_VERSION = 1
MAX_TIMELINE = 10_000
MAX_STR = 64

CATEGORY_LABELS: dict[str, tuple[str, str]] = {
    "face": ("Face", "चेहरा"),
    "aadhaar": ("Aadhaar card", "आधार कार्ड"),
    "pan": ("PAN card", "पैन कार्ड"),
    "upi_qr": ("UPI QR code", "UPI क्यूआर कोड"),
    "plate": ("Number plate", "नंबर प्लेट"),
    "signboard": ("Signboard text", "साइनबोर्ड का लिखा"),
    "pin_code": ("PIN code", "पिन कोड"),
    "phone_number": ("Phone number", "फ़ोन नंबर"),
    "upi_id": ("UPI ID", "UPI आईडी"),
    "email": ("Email address", "ईमेल पता"),
    "otp_screen": ("OTP on screen", "स्क्रीन पर OTP"),
    "game_id": ("Game player ID", "गेम प्लेयर आईडी"),
    "room_credential": ("Game room ID or password", "गेम रूम आईडी या पासवर्ड"),
    "risky_text": ("Risky on-screen text", "जोखिम वाला स्क्रीन टेक्स्ट"),
    "spoken_phone": ("Spoken phone number", "बोला गया फ़ोन नंबर"),
    "spoken_upi": ("Spoken UPI ID", "बोली गई UPI आईडी"),
    "spoken_address": ("Spoken address", "बोला गया पता"),
    "spoken_other_pii": ("Other spoken private detail", "बोली गई अन्य निजी जानकारी"),
    "spoken_unverified": ("Muted, speech not verified", "म्यूट, बात जाँची नहीं जा सकी"),
    "chat_abuse": ("Chat abuse", "चैट में गाली-गलौज"),
    "chat_doxxing": ("Chat doxxing", "चैट में निजी जानकारी उजागर"),
    "chat_scam_link": ("Chat scam link", "चैट में स्कैम लिंक"),
    "chat_fake_upi": ("Chat fake UPI request", "चैट में नकली UPI अनुरोध"),
    "music": ("Music detected", "संगीत मिला"),
    "copyright_match": ("Copyright match", "कॉपीराइट मिलान"),
    "profanity": ("Profanity", "अपशब्द"),
    "hold": ("Hold (panic)", "होल्ड (पैनिक)"),
    "failsafe": ("Fail-safe", "फ़ेल-सेफ़"),
    "system_status": ("System notice", "सिस्टम सूचना"),
}
ACTION_LABELS: dict[str, tuple[str, str]] = {
    "masked": ("Masked", "छिपाया गया"),
    "muted": ("Muted", "म्यूट किया गया"),
    "hidden": ("Hidden", "छुपाया गया"),
    "held": ("Held", "रोका गया"),
    "deleted": ("Deleted", "हटाया गया"),
    "timed_out": ("Timed out", "टाइमआउट"),
    "warned": ("Warned", "चेतावनी"),
    "failsafe": ("Fail-safe", "फ़ेल-सेफ़"),
}
MODALITY_LABELS: dict[str, tuple[str, str]] = {
    "video": ("Video", "वीडियो"),
    "audio": ("Audio", "ऑडियो"),
    "chat": ("Chat", "चैट"),
    "guard": ("Strike guard", "स्ट्राइक गार्ड"),
    "system": ("System", "सिस्टम"),
}
LEVEL_LABELS: dict[str, tuple[str, str]] = {
    "ok": ("Low", "कम"),
    "watch": ("Watch", "ध्यान दें"),
    "warn": ("Warning", "चेतावनी"),
    "danger": ("Danger", "ख़तरा"),
}


class ReportPrivacyError(ValueError):
    """A report field would carry something that must never be in a report."""


def label(table: dict[str, tuple[str, str]], code: str) -> dict[str, str]:
    en, hi = table.get(code, (code.replace("_", " ").capitalize(), code.replace("_", " ")))
    return {"en": en, "hi": hi}


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return round(ordered[idx], 3)


def _iso_to_epoch(value: str | None) -> float | None:
    return None if value is None else parse_ts(value)


def _bbox(event: SafetyEvent) -> list[list[float]] | None:
    if event.bbox is None:
        return None
    return [[round(x, 1), round(y, 1)] for x, y in event.bbox.pts]


def timeline_row(event: SafetyEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "wall_ts": event.wall_ts,
        "pts": None if event.pts is None else round(event.pts, 3),
        "modality": event.modality.value,
        "category": event.category.value,
        "action": event.action,
        "confidence": round(event.confidence, 3),
        "bbox": _bbox(event),
        "duration_ms": event.duration_ms,
        "source": event.source,
        "detail_code": event.detail_code,
    }


def build_report(rec: SessionLog) -> dict[str, Any]:
    """Build the report from a session record. Partial while ``ended_at`` is None."""
    all_events: list[SafetyEvent] = []
    cursor = 0
    while True:
        page, nxt = rec.events.query(cursor=cursor, limit=1000)
        if not page:
            break
        all_events.extend(page)
        cursor = nxt
    all_events.sort(key=lambda e: (_iso_to_epoch(e.wall_ts) or 0.0, e.event_id))

    by_cat: dict[str, list[float]] = defaultdict(list)
    by_action: dict[str, int] = defaultdict(int)
    by_modality: dict[str, int] = defaultdict(int)
    failsafe_audio_ms = 0
    notices = 0
    for e in all_events:
        if e.category.value == "system_status":
            notices += 1  # a notice (for example "chat unavailable"), not a protection: never counted as one
            continue
        by_cat[e.category.value].append(e.confidence)
        by_action[e.action] += 1
        by_modality[e.modality.value] += 1
        if e.category.value == "spoken_unverified" and e.duration_ms:
            failsafe_audio_ms += e.duration_ms

    start, end = _iso_to_epoch(rec.started_at), _iso_to_epoch(rec.ended_at)
    duration = None if start is None or end is None else round(max(0.0, end - start), 1)
    delays, fps = list(rec.delay_samples), list(rec.fps_samples)
    notes = list(rec.notes)
    if rec.jsonl_failed:
        notes.append("events_file_unavailable")
    if len(all_events) > MAX_TIMELINE:
        notes.append("timeline_truncated")
    if rec.ended_at is None:
        notes.append("partial_report_session_live")
    risk_level = API_LEVEL[LEVELS[rec.risk_max_idx]]

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "session_id": rec.session_id,
        "started_at": rec.started_at,
        "ended_at": rec.ended_at,
        "duration_s": duration,
        "profile": rec.profile,
        "delay_seconds": rec.delay_seconds,
        "totals": {
            "events": len(all_events) - notices,
            "system_notices": notices,
            "by_category": {
                c: {"count": len(v), "mean_confidence": round(sum(v) / len(v), 3)}
                for c, v in sorted(by_cat.items())
            },
            "by_action": dict(sorted(by_action.items())),
            "by_modality": dict(sorted(by_modality.items())),
        },
        "timeline": [timeline_row(e) for e in all_events[:MAX_TIMELINE]],
        "holds": [
            {"start_ts": h.start_ts, "end_ts": h.end_ts, "sources": sorted(h.sources)} for h in rec.holds
        ],
        "failsafe": {
            "protect_seconds": round(rec.protect_seconds, 2),
            "audio_failsafe_seconds": round(failsafe_audio_ms / 1000.0, 2),
        },
        "latency": {
            "delay_p50_s": _percentile(delays, 0.5),
            "delay_p95_s": _percentile(delays, 0.95),
            "fps_mean": None if not fps else round(sum(fps) / len(fps), 2),
        },
        "risk": {"max_level": risk_level, "warnings": list(rec.warnings)},
        "chat": rec.chat_totals(),
        "notes": notes,
    }
    validate_report(report)
    return report


# --- privacy guard -----------------------------------------------------------------------------

_ID_KEYS = frozenset(
    {"event_id", "session_id", "wall_ts", "start_ts", "end_ts", "started_at", "ended_at", "ts"}
)
_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+")),
    ("upi_id", re.compile(r"[\w.\-]{2,}@[A-Za-z]{2,}")),
    ("phone", re.compile(r"(?<!\d)(?:\+?91[\s\-]?)?[6-9]\d{9}(?!\d)")),
    ("aadhaar", re.compile(r"(?<!\d)\d{4}[\s\-]?\d{4}[\s\-]?\d{4}(?!\d)")),
    ("pan", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("url", re.compile(r"(?i)\b(?:https?|upi)://|www\.")),
)
_BLOB_PREFIXES = ("data:", "iVBORw0KGgo", "/9j/", "R0lGOD")
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


def _check_string(path: str, key: str, value: str) -> None:
    if len(value) > MAX_STR:
        raise ReportPrivacyError(f"{path}: string longer than {MAX_STR} characters")
    if value.startswith(_BLOB_PREFIXES) or _BASE64_RUN.search(value):
        raise ReportPrivacyError(f"{path}: looks like an embedded image or blob")
    if key in _ID_KEYS:
        return  # ids and timestamps may contain long digit runs
    for name, pattern in _PII_PATTERNS:
        if pattern.search(value):
            raise ReportPrivacyError(f"{path}: looks like {name}")


def validate_report(report: dict[str, Any]) -> None:
    """Raise ``ReportPrivacyError`` if any value could carry an image, free text or raw PII."""

    def walk(node: Any, path: str, key: str) -> None:
        if isinstance(node, str):
            _check_string(path, key, node)
        elif isinstance(node, dict):
            for k, v in node.items():
                if not isinstance(k, str):
                    raise ReportPrivacyError(f"{path}: non-string key")
                _check_string(f"{path}.{k}", "key", k)
                walk(v, f"{path}.{k}", k)
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]", key)
        elif isinstance(node, (bytes, bytearray)):
            raise ReportPrivacyError(f"{path}: binary data")

    walk(report, "report", "")


# --- printable shape ---------------------------------------------------------------------------


def _mmss(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def to_printable(report: dict[str, Any]) -> dict[str, Any]:
    """Layout-ready rows: English and Hindi labels, offsets from stream start, confidence in percent."""
    start = _iso_to_epoch(report["started_at"])
    rows = []
    for ev in report["timeline"]:
        ts = _iso_to_epoch(ev["wall_ts"])
        rows.append(
            {
                "offset": _mmss(None if ts is None or start is None else ts - start),
                "wall_ts": ev["wall_ts"],
                "category": ev["category"],
                "category_label": label(CATEGORY_LABELS, ev["category"]),
                "modality_label": label(MODALITY_LABELS, ev["modality"]),
                "action_label": label(ACTION_LABELS, ev["action"]),
                "confidence_pct": round(ev["confidence"] * 100),
                "duration_ms": ev["duration_ms"],
            }
        )
    totals = report["totals"]
    summary = [
        {
            "key": "duration",
            "label": {"en": "Stream length", "hi": "स्ट्रीम की अवधि"},
            "value": _mmss(report["duration_s"]),
        },
        {
            "key": "events",
            "label": {"en": "Protections applied", "hi": "लागू सुरक्षा"},
            "value": totals["events"],
        },
        {"key": "holds", "label": {"en": "Holds", "hi": "होल्ड"}, "value": len(report["holds"])},
        {
            "key": "risk",
            "label": {"en": "Highest strike risk", "hi": "सबसे ऊँचा स्ट्राइक जोखिम"},
            "value": label(LEVEL_LABELS, report["risk"]["max_level"]),
        },
        {
            "key": "delay",
            "label": {"en": "Typical delay (s)", "hi": "सामान्य देरी (सेकंड)"},
            "value": report["latency"]["delay_p50_s"],
        },
    ]
    return {
        "title": {"en": "Stream Sarthi safety report", "hi": "स्ट्रीम सारथी सुरक्षा रिपोर्ट"},
        "session_id": report["session_id"],
        "started_at": report["started_at"],
        "ended_at": report["ended_at"],
        "partial": report["ended_at"] is None,
        "privacy_note": {
            "en": "No images, audio, chat text or personal details are stored in this report.",
            "hi": "इस रिपोर्ट में कोई तस्वीर, ऑडियो, चैट टेक्स्ट या निजी जानकारी नहीं है।",
        },
        "summary": summary,
        "categories": [
            {
                "category": c,
                "label": label(CATEGORY_LABELS, c),
                "count": v["count"],
                "mean_confidence_pct": round(v["mean_confidence"] * 100),
            }
            for c, v in totals["by_category"].items()
        ],
        "timeline": rows,
        "holds": [
            {"start_ts": h["start_ts"], "end_ts": h["end_ts"], "sources": h["sources"]}
            for h in report["holds"]
        ],
        "warnings": [
            {"ts": w["ts"], "source": w["source"], "label": w["label"], "score_pct": round(w["score"] * 100)}
            for w in report["risk"]["warnings"]
        ],
        "chat": report["chat"],
        "notes": report["notes"],
    }


# --- HTML export -------------------------------------------------------------------------------

HTML_CSP = (
    "default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)
_HTML_STYLE = (
    "body{font-family:system-ui,sans-serif;margin:24px;color:#1b1f24}h1{font-size:20px}"
    "table{border-collapse:collapse;width:100%;margin:12px 0}th,td{border:1px solid #c9ced6;"
    "padding:4px 8px;text-align:left;font-size:13px}th{background:#f1f3f6}.muted{color:#5b6572}"
)


def render_html(report: dict[str, Any]) -> str:
    """Static printable page. Every value is escaped; there is no script and no external resource."""
    p = to_printable(report)
    e = html.escape

    def table(head: list[str], rows: list[list[Any]]) -> str:
        th = "".join(f"<th>{e(h)}</th>" for h in head)
        body = "".join(
            "<tr>" + "".join(f"<td>{e('' if c is None else str(c))}</td>" for c in r) + "</tr>" for r in rows
        )
        return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"

    summary_rows = [
        [
            s["label"]["en"] + " / " + s["label"]["hi"],
            s["value"]["en"] if isinstance(s["value"], dict) else s["value"],
        ]
        for s in p["summary"]
    ]
    cat_rows = [
        [c["label"]["en"] + " / " + c["label"]["hi"], c["count"], f"{c['mean_confidence_pct']}%"]
        for c in p["categories"]
    ]
    tl_rows = [
        [
            r["offset"],
            r["category_label"]["en"] + " / " + r["category_label"]["hi"],
            r["action_label"]["en"],
            f"{r['confidence_pct']}%",
            r["duration_ms"],
        ]
        for r in p["timeline"]
    ]
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        f"<title>{e(p['title']['en'])}</title><style>{_HTML_STYLE}</style></head><body>",
        f"<h1>{e(p['title']['en'])} / {e(p['title']['hi'])}</h1>",
        f'<p class="muted">Session {e(p["session_id"])}, {e(p["started_at"])}'
        f"{' (partial: stream still live)' if p['partial'] else ''}</p>",
        f'<p class="muted">{e(p["privacy_note"]["en"])}<br>{e(p["privacy_note"]["hi"])}</p>',
        table(["Summary", "Value"], summary_rows),
        table(["Category", "Count", "Mean confidence"], cat_rows),
        table(["Time", "Category", "Action", "Confidence", "Duration (ms)"], tl_rows),
        "</body></html>",
    ]
    return "".join(parts)
