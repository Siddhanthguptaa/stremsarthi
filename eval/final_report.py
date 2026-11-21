"""Final report (T7.6): one table of every PLAN section 5 target, built from the latest per-gate reports.

    python -m eval.run final-report [--report-dir D] [--since EPOCH] [--max-age-days N] [--results R.json]
                                    [--date YYYY-MM-DD] [--no-write]

Writes eval/reports/<date>-final.md and .json. Rules (a missing or old report is never a pass):

* a gate with no report is NOT RUN; a report older than --max-age-days or written before --since is STALE
  (counted with the NOT RUN list); a check flagged `not_run` in its report is NOT RUN;
* the verdict of a row is the verdict of the check that measured it; a gate that exited non-zero in the
  orchestrator run (--results, written by scripts/gate_final.sh) can only make things worse;
* "what is verified live" lists only checks of a `<date>-live*.json` report; with no such report it stays empty;
* manual items (soak, netem 4G, three runs on the RTX 5050) are NOT RUN until a report exists.

Caveat tags follow docs/STATUS.md: SYNTHETIC (generated data), ORACLE (recogniser built from truth words),
STAND-IN (a fake translator or Groq), BUILDER-REPORTED vs LEAD-RUN (read from the Gates table of STATUS.md),
QUICK (subset run), NOT RUN.

Exit codes (same precedence as scripts/gate_lib.sh): 2 an environment error was recorded, 1 something FAILED,
3 something is NOT RUN or STALE, 0 only when every row passed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = ROOT / "eval" / "reports"
PASS, FAIL, NOT_RUN, STALE, ENV, RECORDED, ESTIMATE = (
    "PASS",
    "FAIL",
    "NOT RUN",
    "STALE",
    "ENV",
    "RECORDED",
    "ESTIMATE",
)
INFORMATIONAL = (RECORDED, ESTIMATE)

# gate id -> (title, report phase names (never reference/seeded/defect variants), orchestrator step name)
GATES: dict[str, tuple[str, tuple[str, ...], str]] = {
    "G0": ("Environment, contracts, harness", ("phase0",), "G0"),
    "G1": ("End-to-end pipe + one detector", ("phase1", "phase1-quick"), "G1"),
    "G2": (
        "Indian video layer",
        ("phase2", "phase2-quick", "phase2-offline-d48", "phase2-offline"),
        "G2",
    ),
    "G3": ("Hinglish audio", ("phase3", "phase3-quick"), "G3"),
    "G4": ("Chat shield", ("phase4",), "G4"),
    "G5": ("Strike guard, captions", ("phase5",), "G5"),
    "G6": ("Product", ("phase6",), "G6"),
    "RT-V": ("Red team: video", ("redteam-video",), "redteam-video"),
    "RT-A": ("Red team: audio", ("redteam-audio",), "redteam-audiochat"),
    "RT-C": ("Red team: chat", ("redteam-chat",), "redteam-audiochat"),
    "E2E": ("End-to-end smoke (no keys)", (), "e2e"),
}

# (gate, item, regex on the check name, PLAN section 5 target text, informational)
TARGETS: list[tuple[str, str, str, str, bool]] = [
    ("G0", "doctor", r"^doctor", "scripts/doctor.py passes", False),
    ("G0", "contract tests", r"contracts tests", "contract tests green", False),
    ("G0", "ruff check", r"^ruff check", "ruff clean", False),
    ("G0", "ruff format", r"^ruff format", "ruff format clean", False),
    ("G0", "eval selftest and unit tests", r"eval (selftest|unit tests)", "eval selftest OK", False),
    ("G0", "ffprobe SRT/RTMP ingest", r"ffprobe", "ingest receives", False),
    ("G0", "loopback 60 s", r"loopback", "0 drops", False),
    ("G0", "STT spike report", r"STT spike", "report present", False),
    ("G1", "delay p95 capture to egress", r"delay p95 at tap", "delay p95 <= 5.0 s (CPU and GPU legs)", False),
    ("G1", "output fps", r"output fps", "output >= 29 fps", False),
    ("G1", "frames showing the card after a fault", r"showing the card", "0 unmasked frames in the fail-safe", False),
    ("G1", "slate after a deadline miss", r"slate frames seen", "slate within 1 s of the miss", False),
    ("G1", "hold: slate after the command", r"slate appeared after the hold", "slate within 1 frame", False),
    ("G1", "30 min soak (RSS growth)", r"RSS growth", "memory growth <= 10 %", False),
    ("G2", "faces masked (non-allowlisted)", r"Faces masked", "recall >= 95 %", False),
    ("G2", "allowlisted faces visible", r"Allowlisted faces", ">= 95 %", False),
    ("G2", "Aadhaar", r"Aadhaar", "recall >= 95 %", False),
    ("G2", "PAN", r"PAN card", "recall >= 95 %", False),
    ("G2", "UPI QR", r"UPI QR", "recall >= 98 %", False),
    ("G2", "signboard text", r"Signboard", "recall >= 85 %", False),
    ("G2", "phone number on screen", r"Phone number", "recall >= 90 %", False),
    ("G2", "PIN code on screen", r"PIN code", "recall >= 90 %", False),
    ("G2", "UPI ID on screen", r"UPI ID", "recall >= 90 %", False),
    ("G2", "email on screen", r"Email", "recall >= 90 %", False),
    ("G2", "OTP screens", r"OTP screen", "recall >= 85 %", False),
    ("G2", "game IDs", r"Game IDs", "recall >= 90 %", False),
    ("G2", "number plates", r"Number plate", "recall >= 85 %", False),
    ("G2", "mask flicker", r"flicker", "<= 2 %", False),
    ("G2", "accurate lane latency", r"ACCURATE lane", "<= 450 ms p95 (this repo's lane budget)", False),
    ("G2", "perf: fps", r"perf-(cpu|gpu): .*fps", "fps >= 29, all detectors on", False),
    ("G2", "perf: delay p95", r"perf-(cpu|gpu): .*delay", "delay p95 <= 5.0 s", False),
    ("G2", "perf: CPU cores", r"perf-(cpu|gpu): .*cpu", "CPU <= 5 cores", False),
    ("G2", "perf: VRAM", r"perf-(cpu|gpu): .*vram", "VRAM <= 3.5 GB (dev)", False),
    ("G3", "mute coverage, PII words", r"mute coverage, PII", ">= 90 %", False),
    ("G3", "mute coverage, digit words", r"mute coverage, spoken digit", ">= 95 %", False),
    ("G3", "over-mute of non-PII speech", r"over-mute", "<= 15 %", False),
    ("G3", "audio pipeline latency p95", r"audio pipeline latency", "<= 3.3 s", False),
    ("G3", "mouth blur on muted frames", r"mouth blur", ">= 95 %", False),
    ("G3", "API down: PII muted", r"API down: PII", "100 % (fail-safe)", False),
    ("G3", "API down: all voiced speech muted", r"API down: all voiced", "fail-safe", False),
    ("G3", "API down: local recogniser", r"local recogniser", ">= 70 % of PII", False),
    ("G4", "abuse recall", r"^abuse recall", ">= 85 %", False),
    ("G4", "abuse precision", r"^abuse precision", ">= 90 %", False),
    ("G4", "scam link recall", r"^scam link", ">= 95 %", False),
    ("G4", "fake UPI recall", r"^fake UPI", ">= 98 %", False),
    ("G4", "doxxing recall", r"^doxxing", ">= 85 %", False),
    ("G4", "clean false positives", r"clean false-positive", "<= 3 %", False),
    ("G4", "classify p95", r"classify latency", "<= 300 ms", False),
    ("G4", "YouTube mock round trips", r"^YouTube:", "connect, classify, hide, delete, timeout", False),
    ("G4", "Twitch mock round trips and !hold", r"^Twitch:", "reconnect, !hold only from staff", False),
    ("G4", "held-out chat set (informational)", r"held-?out", "reported, never gating", True),
    ("G5", "music detect recall", r"music detect recall", ">= 90 %", False),
    ("G5", "music false positives", r"music detect false", "<= 5 %", False),
    ("G5", "fingerprint match", r"fingerprint match", ">= 90 % on noisy 5 s snippets", False),
    ("G5", "Hinglish profanity recall", r"Hinglish profanity recall", ">= 85 %", False),
    ("G5", "risk meter reaction", r"risk meter reaction", "<= 2 s", False),
    ("G5", "captions render in 5 scripts", r"captions render", "5 scripts (raqm)", False),
    ("G5", "captions arrive before display", r"captions published after", "0 late", False),
    ("G6", "web build", r"\bbuild\b", "build passes", False),
    ("G6", "web lint and typecheck", r"lint|tsc|typecheck", "lint and typecheck pass", False),
    ("G6", "API tests", r"pytest|api tests", "pass", False),
    ("G6", "Playwright flows", r"playwright", "setup, enrol, toggles, hold, mod queue, report, mobile", False),
    ("G6", "safety report schema", r"schema|safety report", "no images or raw text", False),
    ("G6", "fresh clone to live", r"walkthrough|fresh", "scripted walkthrough time", True),
    ("RT-A", "red team audio: fast numbers", r"audio (fast_speech|digits_fillers)", "miss rate recorded, under ceiling", False),
    ("RT-A", "red team audio: accents and code-mixed numbers", r"audio (code_mixed_numbers|upi_phonetic|letter_spelling)", "miss rate recorded, under ceiling", False),
    ("RT-A", "red team audio: noise", r"audio (whisper_noisy|background_music)", "miss rate recorded, under ceiling", False),
    ("RT-C", "red team chat: misspellings", r"chat (leetspeak|split_words|homoglyph|zero_width)", "miss rate recorded, under ceiling", False),
    ("RT-C", "red team chat: mixed script", r"chat mixed_script", "miss rate recorded, under ceiling", False),
]  # fmt: skip

VIDEO_GROUPS = ("angle", "glare", "occlusion", "motion")
MANUAL_ITEMS = (
    ("G1", "netem 4G profile (no hang, slate during drops)", "needs tc/sudo; no gate leg exists yet"),
    ("7", "three full runs on the RTX 5050 (demo laptop)", "manual item, never automated"),
    ("G1", "3 min unlisted YouTube run (--live-youtube)", "needs a stream key and the user"),
)

GATE_CAVEATS: dict[str, list[str]] = {
    "G0": [],
    "G1": ["SYNTHETIC SPECIMEN stream"],
    "G2": ["SYNTHETIC (optimistic: clean on-screen text)"],
    "G3": ["SYNTHETIC", "ORACLE (recogniser built from truth words; real STT never run)"],
    "G4": ["SYNTHETIC (author-written set; no live platform)", "STAND-IN Groq in the informational leg"],
    "G5": ["SYNTHETIC audio", "STAND-IN translator; no real music"],
    "G6": [],
    "RT-V": ["SYNTHETIC SPECIMEN attacks; coverage proxy plus read-back"],
    "RT-A": ["SYNTHETIC", "ORACLE recogniser"],
    "RT-C": ["SYNTHETIC"],
    "E2E": ["SYNTHETIC SPECIMEN sender, no API keys"],
}


@dataclass
class Row:
    gate: str
    item: str
    plan_target: str
    target: str
    measured: str
    scope: str
    verdict: str
    caveats: str
    source: str
    informational: bool = False


@dataclass
class GateInfo:
    gate: str
    title: str
    verdict: str = NOT_RUN
    report: str = ""
    phase: str = ""
    date: str = ""
    git_rev: str = ""
    note: str = ""
    data: dict[str, Any] = field(default_factory=dict, repr=False)


# ----------------------------------------------------------------------------------------------- helpers


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def _date_of(path: Path) -> str:
    m = re.match(r"(\d{4}-\d{2}-\d{2})-", path.name)
    return m.group(1) if m else ""


def find_report(
    dirs: list[Path], names: tuple[str, ...], since: float | None, max_age_days: float, today: str
) -> tuple[Path | None, dict[str, Any] | None, str]:
    """Newest `<date>-<name>.json` for any of `names` in `dirs`. Returns (path, data, stale_reason)."""
    best: tuple[tuple[str, float], Path] | None = None
    for d in dirs:
        for name in names:
            for p in d.glob(f"[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-{name}.json"):
                if p.name[len("YYYY-MM-DD-") : -len(".json")] != name:
                    continue
                key = (_date_of(p), p.stat().st_mtime)
                if best is None or key > best[0]:
                    best = (key, p)
    if best is None:
        return None, None, ""
    path = best[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return path, None, "unreadable report"
    if not isinstance(data, dict):
        return path, None, "unreadable report"
    stale = ""
    if since is not None and path.stat().st_mtime < since:
        stale = "written before this run started"
    else:
        age = _age_days(_date_of(path), today)
        if age is not None and age > max_age_days:
            stale = f"{age:.0f} days old (limit {max_age_days:g})"
    return path, data, stale


def _age_days(date: str, today: str) -> float | None:
    try:
        a = time.mktime(time.strptime(today, "%Y-%m-%d"))
        b = time.mktime(time.strptime(date, "%Y-%m-%d"))
    except ValueError:
        return None
    return (a - b) / 86400.0


def status_notes(status_md: str) -> dict[str, list[str]]:
    """Caveat tags per gate read from the Gates table of docs/STATUS.md (never a source of a pass)."""
    out: dict[str, list[str]] = {}
    for line in status_md.splitlines():
        m = re.match(r"^\|\s*(G\d|Final)\s*\|", line)
        if not m:
            continue
        gid, low = m.group(1), line.lower()
        tags: list[str] = []
        if "run by the lead" in low:
            tags.append("LEAD-RUN")
        if "reported" in low:
            tags.append("BUILDER-REPORTED")
        if "pending" in low and "|" in line and re.search(r"\|\s*pending\s*\|", low):
            tags.append("STATUS.md: pending")
        if "not run" in low:
            tags.append("STATUS.md notes NOT RUN legs")
        if "oracle" in low:
            tags.append("ORACLE")
        out[gid] = tags
    return out


def _scope(gate: str, phase: str, data: dict[str, Any], item: str) -> str:
    if gate == "G2":
        if item.startswith("perf") or item == "accurate lane latency":
            return "real-time engine chain" if item.startswith("perf") else "offline lane timing"
        banner = str(data.get("banner", ""))
        if "d48" in phase or "clean+light" in banner:
            return "clean+light (D-48); heavy is a stress level, not gated"
        return "all levels pooled incl. heavy (pre-D-48 scoring, stricter than the gate)"
    if gate in ("RT-V", "RT-A", "RT-C"):
        return "adversarial set (stress), not the clean+light gate scope"
    if gate == "G0":
        return "environment"
    return "single level (generated set)"


def _check_verdict(c: dict[str, Any]) -> str:
    if c.get("not_run"):
        return NOT_RUN
    return PASS if c.get("passed") else FAIL


def _tags(gate: str, phase: str, status: dict[str, list[str]], head: str | None, rev: str) -> str:
    tags = list(GATE_CAVEATS.get(gate, []))
    gid = gate if gate in status else ""
    tags += status.get(gid, [])
    if not any(t in tags for t in ("LEAD-RUN", "BUILDER-REPORTED")) and gate.startswith("G"):
        tags.append("BUILDER-REPORTED (not marked lead-run in STATUS.md)")
    if phase.endswith("-quick"):
        tags.append("QUICK subset")
    if phase.startswith("phase2-offline"):
        tags.append("offline leg only (perf legs NOT RUN in this report)")
    if head and rev and not head.startswith(rev[:7]) and not rev.startswith(head[:7]):
        tags.append(f"report from commit {rev[:7]}, HEAD is {head[:7]}")
    seen: list[str] = []
    for t in tags:
        if t not in seen:
            seen.append(t)
    return "; ".join(seen)


# ----------------------------------------------------------------------------------------------- build


def build(
    report_dir: Path,
    *,
    search_dirs: list[Path] | None = None,
    since: float | None = None,
    max_age_days: float = 7.0,
    today: str | None = None,
    results: dict[str, Any] | None = None,
    status_md: str = "",
    head_rev: str | None = None,
    root: Path = ROOT,
) -> dict[str, Any]:
    today = today or time.strftime("%Y-%m-%d")
    dirs: list[Path] = []
    for d in [report_dir, *(search_dirs or [])]:
        if d not in dirs:
            dirs.append(d)
    status = status_notes(status_md)
    steps = {s.get("name"): s for s in (results or {}).get("steps", [])}
    rows: list[Row] = []
    gates: dict[str, GateInfo] = {}

    for gid, (title, names, _step) in GATES.items():
        info = GateInfo(gid, title)
        gates[gid] = info
        if not names:
            continue
        path, data, stale = find_report(dirs, names, since, max_age_days, today)
        if path is None:
            info.note = "no report"
            continue
        info.report = path.name
        info.phase = path.name[len("YYYY-MM-DD-") : -len(".json")]
        info.date = _date_of(path)
        if data is None:
            info.note = stale
            info.verdict = ENV
            continue
        info.git_rev = str((data.get("header") or {}).get("git_rev", ""))
        info.data = data
        info.note = f"STALE: {stale}" if stale else ""
        info.verdict = STALE if stale else PASS  # refined below

    claimed: dict[str, set[int]] = {g: set() for g in GATES}
    for gate, item, rx, plan, informational in TARGETS:
        info = gates[gate]
        if info.verdict == NOT_RUN and not info.report:
            rows.append(_missing_row(gate, item, plan, "no report found", informational))
            continue
        if info.verdict in (STALE, ENV):
            rows.append(
                Row(
                    gate,
                    item,
                    plan,
                    "",
                    "",
                    "",
                    info.verdict,
                    f"{info.verdict}: {info.note}",
                    info.report,
                    informational,
                )
            )
            continue
        checks = info.data.get("checks") or []
        matched = [
            (i, c)
            for i, c in enumerate(checks)
            if i not in claimed[gate] and re.search(rx, str(c.get("name", "")), re.I)
        ]
        if not matched:
            hint = (
                _heldout_row(info, item, plan, informational) if gate == "G4" and "held-out" in item else None
            )
            rows.append(
                hint or _missing_row(gate, item, plan, f"no check matches in {info.report}", informational)
            )
            continue
        for i, c in matched:
            claimed[gate].add(i)
            label = item if len(matched) == 1 else f"{item} [{_bracket(str(c.get('name', '')))}]"
            rows.append(
                Row(
                    gate,
                    label,
                    plan,
                    f"{c.get('op', '')} {_fmt(c.get('target', ''))}{c.get('unit', '')}".strip(),
                    f"{_fmt(c.get('value', ''))}{c.get('unit', '')}",
                    _scope(gate, info.phase, info.data, item),
                    _check_verdict(c),
                    _tags(gate, info.phase, status, head_rev, info.git_rev)
                    + (f"; {c['note']}" if c.get("note") else ""),
                    info.report,
                    informational,
                )
            )

    # video red team (no checks list: groups and regressions)
    _video_rows(gates["RT-V"], rows, status, head_rev, root)

    # roll-ups: every check not claimed by a target, per gate
    for gid, info in gates.items():
        if not info.data or info.verdict in (STALE, ENV) or gid == "RT-V":
            continue
        rest = [c for i, c in enumerate(info.data.get("checks") or []) if i not in claimed[gid]]
        if not rest:
            continue
        bad = [c for c in rest if not c.get("not_run") and not c.get("passed")]
        nr = [c for c in rest if c.get("not_run")]
        verdict = FAIL if bad else (NOT_RUN if nr else PASS)
        names = "; ".join(str(c.get("name"))[:60] for c in (bad or nr)[:3])
        rows.append(
            Row(
                gid,
                f"other checks in the report ({len(rest)})",
                "remaining criteria of the gate",
                "all pass",
                f"{len(rest) - len(bad) - len(nr)}/{len(rest)} pass"
                + (f"; failing: {names}" if bad else (f"; NOT RUN: {names}" if nr else "")),
                _scope(gid, info.phase, info.data, "other"),
                verdict,
                _tags(gid, info.phase, status, head_rev, info.git_rev),
                info.report,
            )
        )

    # e2e smoke has no report: it comes from the orchestrator's results only
    rows.append(_e2e_row(steps.get("e2e"), gates["E2E"], status))
    for gate, item, why in MANUAL_ITEMS:
        rows.append(Row(gate, item, "manual / hardware item", "", "", "", NOT_RUN, f"NOT RUN: {why}", ""))
    rows.append(_cost_row(root))

    # orchestrator results can only make a gate worse
    for gid, (_t, _n, step_name) in GATES.items():
        s = steps.get(step_name)
        if not s or gid == "E2E":
            continue
        mapped = int(s.get("mapped_rc", 0))
        if mapped == 0:
            continue
        verdict = {1: FAIL, 2: ENV, 3: NOT_RUN}.get(mapped, FAIL)
        rows.append(
            Row(
                gid,
                f"orchestrator run of the gate (exit {s.get('rc')})",
                "gate script exits 0",
                "exit 0",
                f"exit {s.get('rc')}",
                "",
                verdict,
                (str(s.get("note") or "") or "recorded by scripts/gate_final.sh"),
                "gate_final results",
            )
        )
        if mapped == 3 and gates[gid].verdict != NOT_RUN:
            gates[gid].verdict = NOT_RUN

    live = _live(dirs, since, max_age_days, today)
    for gid, info in gates.items():
        info.verdict = _gate_verdict([r for r in rows if r.gate == gid and not r.informational], info)
    overall, code = _overall(rows)
    not_run = [
        f"{r.gate}: {r.item}"
        + (f" ({r.caveats})" if r.caveats and r.verdict in (NOT_RUN, STALE, ENV) else "")
        for r in rows
        if r.verdict in (NOT_RUN, STALE, ENV)
    ]
    return {
        "date": today,
        "overall": overall,
        "exit_code": code,
        "head_rev": head_rev or "",
        "gates": {g: {k: v for k, v in asdict(i).items() if k != "data"} for g, i in gates.items()},
        "rows": [asdict(r) for r in rows],
        "not_run": not_run,
        "verified_live": live,
        "cost_pointer": "docs/COST.md",
        "rules": "a missing or stale report is NOT RUN / STALE, never a pass",
    }


def _bracket(name: str) -> str:
    m = re.search(r"\[(.+?)\]", name)
    if m:
        return m.group(1)
    m = re.match(r"([\w -]{1,24}):", name)
    return m.group(1) if m else name[:30]


def _missing_row(gate: str, item: str, plan: str, why: str, informational: bool) -> Row:
    return Row(gate, item, plan, "", "", "", NOT_RUN, f"NOT RUN: {why}", "", informational)


def _heldout_row(info: GateInfo, item: str, plan: str, informational: bool) -> Row | None:
    h = info.data.get("heldout")
    if not isinstance(h, dict) or not h:
        return None
    return Row(
        "G4", item, plan, "informational", json.dumps(h, sort_keys=True)[:160], "blind held-out set",
        RECORDED, "SYNTHETIC; aggregates only", info.report, True,
    )  # fmt: skip


def _video_rows(
    info: GateInfo, rows: list[Row], status: dict[str, list[str]], head: str | None, root: Path
) -> None:
    gate = "RT-V"
    if info.verdict in (STALE, ENV) or not info.data:
        for g in VIDEO_GROUPS:
            rows.append(
                Row(gate, f"red team video: {g}", "miss rate recorded", "", "", "", info.verdict if info.report else NOT_RUN,
                    f"{info.verdict if info.report else NOT_RUN}: {info.note or 'no report'}", info.report)
            )  # fmt: skip
        return
    groups = info.data.get("groups") or {}
    tags = _tags(gate, info.phase, status, head, info.git_rev)
    for g in VIDEO_GROUPS:
        key = next((k for k in groups if g in k.lower()), None)
        if key is None:
            rows.append(
                _missing_row(
                    gate,
                    f"red team video: {g}",
                    "miss rate recorded",
                    f"group absent in {info.report}",
                    False,
                )
            )
            continue
        fr = (groups[key] or {}).get("frames") or {}
        lo, hi = (fr.get("ci") or [None, None])[:2]
        rows.append(
            Row(
                gate,
                f"red team video: {g}",
                "miss rate recorded and published",
                "recorded (no absolute ceiling; regression only)",
                f"frame leak {_fmt(fr.get('rate', ''))} ({fr.get('k', '?')}/{fr.get('n', '?')}), CI {_fmt(lo)}-{_fmt(hi)}",
                _scope(gate, info.phase, info.data, g),
                RECORDED,
                tags,
                info.report,
                True,
            )
        )
    baseline = root / "eval" / "baselines" / "redteam_video.json"
    reg = info.data.get("regressions")
    if not baseline.exists():
        rows.append(
            _missing_row(
                gate,
                "red team video: regression vs baseline",
                ">5 points worse fails",
                "no eval/baselines/redteam_video.json",
                False,
            )
        )
    else:
        rows.append(
            Row(gate, "red team video: regression vs baseline", ">5 points worse fails", "0 regressions",
                f"{len(reg or [])} regressions", "", FAIL if reg else PASS, tags, info.report)
        )  # fmt: skip


def _e2e_row(step: dict[str, Any] | None, info: GateInfo, status: dict[str, list[str]]) -> Row:
    item, plan = "end-to-end smoke (API spawns engine, mask, hold, stop)", "scripts/e2e_smoke.sh exits 0"
    if not step:
        return _missing_row(
            "E2E", item, plan, "no orchestrator result (scripts/gate_final.sh not run)", False
        )
    mapped = int(step.get("mapped_rc", 1))
    verdict = {0: PASS, 1: FAIL, 2: ENV, 3: NOT_RUN}.get(mapped, FAIL)
    return Row(
        "E2E", item, plan, "exit 0", f"exit {step.get('rc')}", "one local stream, dev profile", verdict,
        "; ".join(GATE_CAVEATS["E2E"] + ([str(step["note"])] if step.get("note") else [])), "gate_final results",
    )  # fmt: skip


def _cost_row(root: Path) -> Row:
    p = root / "docs" / "COST.md"
    if not p.exists():
        return _missing_row("7", "cost per streaming hour", "computed", "docs/COST.md missing", False)
    return Row(
        "7", "cost per streaming hour", "computed from API rates", "see docs/COST.md", "ESTIMATE (see docs/COST.md)",
        "", ESTIMATE, "ESTIMATE: assumed request rates, no live API call has ever been made", "docs/COST.md", True,
    )  # fmt: skip


def _live(dirs: list[Path], since: float | None, max_age_days: float, today: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for d in dirs:
        for p in sorted(d.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-live*.json")):
            if p.name in seen:
                continue
            seen.add(p.name)
            path, data, stale = find_report(
                [d], (p.name[len("YYYY-MM-DD-") : -len(".json")],), since, max_age_days, today
            )
            if path is None or data is None or stale:
                continue
            for c in data.get("checks") or []:
                if c.get("passed") and not c.get("not_run"):
                    out.append({"report": p.name, "check": c.get("name"), "value": c.get("value")})
    return out


def _gate_verdict(rows: list[Row], info: GateInfo) -> str:
    vs = [r.verdict for r in rows]
    if ENV in vs:
        return ENV
    if FAIL in vs:
        return FAIL
    if STALE in vs:
        return STALE
    if NOT_RUN in vs or not vs:
        return NOT_RUN
    return PASS


def _overall(rows: list[Row]) -> tuple[str, int]:
    vs = {r.verdict for r in rows if not r.informational}
    if ENV in vs:
        return "ENVIRONMENT ERROR", 2
    if FAIL in vs:
        return "FAIL", 1
    if NOT_RUN in vs or STALE in vs:
        return "INCOMPLETE (NOT RUN items remain; never a pass)", 3
    return "PASS", 0


# ----------------------------------------------------------------------------------------------- output


def _cell(s: Any) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def to_markdown(rep: dict[str, Any]) -> str:
    L = [
        f"# final report ({rep['date']})",
        "",
        f"Overall: **{rep['overall']}** (exit {rep['exit_code']})",
        "",
    ]
    L += [
        "Every row is the latest per-gate report. A missing or stale report is NOT RUN or STALE, never a pass.",
        "Scope: clean+light is the gate scope (D-48); heavy is a stress level; red-team sets are adversarial stress.",
        "Caveat tags (docs/STATUS.md): SYNTHETIC generated data, ORACLE recogniser built from truth, STAND-IN fakes, "
        "BUILDER-REPORTED vs LEAD-RUN, QUICK subset, NOT RUN.",
        "",
        "## Gates",
        "",
        "| gate | title | verdict | report | note |",
        "|---|---|---|---|---|",
    ]
    for g, i in rep["gates"].items():
        L.append(
            f"| {g} | {_cell(i['title'])} | {i['verdict']} | {_cell(i['report'] or '-')} | {_cell(i['note'])} |"
        )
    L += [
        "",
        "## Targets (PLAN section 5)",
        "",
        "| gate | item | PLAN target | measured | target in report | level scope | verdict | caveats | source |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rep["rows"]:
        L.append(
            "| " + " | ".join(
                _cell(r[k] or "-")
                for k in ("gate", "item", "plan_target", "measured", "target", "scope", "verdict", "caveats", "source")
            ) + " |"
        )  # fmt: skip
    L += ["", "## NOT RUN", ""]
    L += [f"- {x}" for x in rep["not_run"]] or ["- (none)"]
    L += ["", "## What is verified live", ""]
    if rep["verified_live"]:
        L += [f"- {v['report']}: {v['check']} = {v['value']}" for v in rep["verified_live"]]
    else:
        L += ["(empty: no live report exists, so nothing is verified live)"]
    L += [
        "",
        "## Cost",
        "",
        f"Cost per streaming hour: see `{rep['cost_pointer']}` (an estimate, not a measurement).",
        "",
    ]
    return "\n".join(L)


def write(rep: dict[str, Any], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    md = report_dir / f"{rep['date']}-final.md"
    js = report_dir / f"{rep['date']}-final.json"
    md.write_text(to_markdown(rep), encoding="utf-8")
    js.write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return md, js


def _head() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.run final-report", description=__doc__.split("\n\n")[0])
    ap.add_argument("--report-dir", default=None)
    ap.add_argument(
        "--search-dir", action="append", default=[], help="extra directory to look for gate reports"
    )
    ap.add_argument("--since", type=float, default=None, help="epoch seconds: older reports are STALE")
    ap.add_argument("--max-age-days", type=float, default=7.0)
    ap.add_argument("--results", default=None, help="gate_final results JSON (scripts/gate_lib.sh format)")
    ap.add_argument("--status", default=str(ROOT / "docs" / "STATUS.md"))
    ap.add_argument("--date", default=None)
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    report_dir = Path(a.report_dir) if a.report_dir else DEFAULT_REPORT_DIR
    results = None
    if a.results:
        try:
            results = json.loads(Path(a.results).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"ENV ERROR: cannot read --results: {exc}", file=sys.stderr)
            return 2
    try:
        status_md = Path(a.status).read_text(encoding="utf-8")
    except OSError:
        status_md = ""
    rep = build(
        report_dir,
        search_dirs=[Path(d) for d in a.search_dir],
        since=a.since,
        max_age_days=a.max_age_days,
        today=a.date,
        results=results,
        status_md=status_md,
        head_rev=_head(),
    )
    if not a.no_write:
        md, _ = write(rep, report_dir)
        print(f"report: {md}")
    n_nr = len(rep["not_run"])
    print(f"FINAL: {rep['overall']} (exit {rep['exit_code']}); {n_nr} item(s) NOT RUN/STALE/ENV")
    return int(rep["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
