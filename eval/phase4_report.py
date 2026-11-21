"""Gate 4 criteria and the ``eval/reports/<date>-phase4.md`` writer (reads the leg JSONs ``eval.phase4`` wrote)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .harness import report as rep

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("data", "classify", "youtube", "twitch")
NOTES = [
    "The classify leg runs with NO LLM (offline, no key): borderline messages are held for a moderator "
    "(HOLD_FOR_MOD, counted as flagged), never allowed. The classify_llm leg (informational) uses a stand-in Groq that "
    "answers from the truth labels with 5 % errors, so it only shows the cascade's behaviour with an LLM.",
    "The set is written independently of chat/classify (own vocabulary, spellings, code-mixing, obfuscation and hard "
    "negatives). Messages marked not-gating (honestly ambiguous: mild banter, payment lures with no id) are reported by "
    "kind but left out of the gating rates.",
    "Scam recall counts a hit when the verdict carries a scam-link or fake-UPI category; fake-UPI recall needs the "
    "fake-UPI category; precision of a group = flagged in that group that truly belong to its label family.",
    "Connector legs use eval/mock_platforms (own YouTube and Twitch mocks, not chat/testing) with the real connector "
    "code and a ManualClock (virtual time), over loopback HTTP and WebSocket.",
    "Log scan: root logger at DEBUG for the whole leg; every record is searched for flagged message texts, staff texts, "
    "the streamer's private terms and phone. Counts only; nothing is printed.",
    "The RTMPS sink of spec 5.7 is not part of this gate (egress is covered by relay/ tests and gate 1).",
]


def _chk(c: dict[str, Any]) -> rep.Check:
    return rep.Check(c["name"], c["value"], c["op"], c["target"], c.get("unit", ""), c.get("note", ""))


def evaluate(res: dict[str, Any]) -> list[rep.Check]:
    leg = res.get("leg")
    out: list[rep.Check] = []
    if leg == "data":
        out.append(
            rep.Check(
                "data: problems found",
                float(len(res.get("problems", []))),
                "==",
                0.0,
                note="; ".join(res.get("problems", [])),
            )
        )
    elif leg == "classify":
        lat = res["latency_ms"]
        out += [
            rep.Check("abuse recall", res["abuse"]["recall"], ">=", 0.85),
            rep.Check("abuse precision", res["abuse"]["precision"], ">=", 0.90),
            rep.Check("scam link recall (scam or fake-UPI category)", res["scam"]["recall"], ">=", 0.95),
            rep.Check("fake UPI recall", res["fake_upi"]["recall"], ">=", 0.98),
            rep.Check("doxxing recall", res["doxxing"]["recall"], ">=", 0.85),
            rep.Check("clean false-positive rate", res["clean"]["false_positive"], "<=", 0.03),
            rep.Check("classify latency p95", lat["classifier_p95"], "<=", 300.0, "ms"),
            rep.Check("staff messages hidden", float(res["staff"]["hidden"]), "==", 0.0),
            rep.Check("unclassified messages", float(res["missing"]), "==", 0.0),
            rep.Check(
                "flagged text shown on the streamer overlay", float(res["flagged_text_on_overlay"]), "==", 0.0
            ),
            rep.Check(
                "log records containing flagged text or private data",
                float(res["log_scan"]["leaks"]),
                "==",
                0.0,
            ),
        ]
    elif leg in ("youtube", "twitch"):
        out += [_chk(c) for c in res.get("checks", [])]
        if not res.get("checks"):
            out.append(rep.Check(f"{leg}: checks ran", None, "==", 1.0, note="no checks recorded"))
    return out


def _pct(v: Any) -> Any:
    return None if v is None else round(100 * v, 2)


def _heldout_tables(r: rep.Report, ho: dict[str, Any]) -> None:
    """Informational rows for the blind held-out set v2 (aggregates only; never gating, never any message text)."""
    rows = []
    for g in ("abuse", "scam", "fake_upi", "doxxing"):
        d = ho[g]
        rows.append(
            [
                g,
                d["n"],
                _pct(d["recall"]["rate"]),
                _pct(d["recall_hard"]["rate"]),
                d["held_with_right_category"] + d["held_without_right_category"],
                d["allowed_missed"],
                _pct(d["precision"]),
                _pct(d["precision_hard"]),
            ]
        )
    c = ho["clean"]
    rows.append(
        [
            "clean (false positives)",
            c["n"],
            _pct(c["false_positive"]["rate"]),
            _pct(c["false_positive_hard"]["rate"]),
            c["held"]["k"],
            "",
            "",
            "",
        ]
    )
    r.tables.append(
        rep.Table(
            "Held-out set v2, informational, NOT gating (blind set, different style; rule-based classifier, no LLM)",
            [
                "category",
                "n",
                "recall % (FP % for clean)",
                "hard-flag only %",
                "held for mod",
                "allowed (missed)",
                "precision %",
                "hard precision %",
            ],
            rows,
        )
    )
    h = ho["holds"]
    r.tables.append(
        rep.Table(
            "Held-out v2: HOLD_FOR_MOD apart from hard flags",
            ["metric", "value"],
            [
                ["messages", h["of_messages"]],
                ["held for a moderator", h["total"]],
                ["held, by true label", json.dumps(h["by_true_label"], sort_keys=True)],
                ["hard flags (hide, delete, timeout)", h["hard_flags_total"]],
                ["allowed", h["allowed_total"]],
                ["staff messages hidden", ho["staff"]["hidden"]],
                ["classifier p95 (ms)", ho["latency_ms"]["classifier_p95"]],
                ["log leaks", ho["log_scan"]["leaks"]],
            ],
        )
    )
    for title, key in (("difficulty", "by_difficulty"), ("kind", "by_kind")):
        r.tables.append(
            rep.Table(
                f"Held-out v2 by {title} (recall or false-positive rate, any flag / hard flag only)",
                ["slice", "n", "any flag %", "hard flag only %"],
                [
                    [
                        k,
                        v["n"],
                        _pct(v.get("recall", v.get("false_positive"))),
                        _pct(v.get("recall_hard", v.get("false_positive_hard"))),
                    ]
                    for k, v in ho[key].items()
                ],
            )
        )


def build_report(out_dir: Path, results: Path | None, phase: str, report_dir: Path | None) -> int:
    r = rep.Report(phase, time.strftime("%Y-%m-%d"))
    legs: dict[str, dict[str, Any]] = {}
    for p in sorted(out_dir.glob("phase4-*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        legs[d.get("leg", p.stem)] = d
    cls = legs.get("classify", {})
    r.header = {
        "git_rev": rep.git_rev(),
        "profile": "dev",
        "hardware": rep.hardware_info(),
        "seed": legs.get("data", {}).get("seed", 1234),
        "dataset": legs.get("data", {}).get("totals", {}),
        "dataset_sha256": legs.get("data", {}).get("sha256", ""),
        "llm": cls.get("llm", "not run"),
    }
    for name in REQUIRED:
        if name not in legs:
            r.checks.append(rep.Check(f"{name} leg ran", None, "==", 1.0, note="not run: no result file"))
            continue
        r.checks += evaluate(legs[name])
    if cls:
        rows = []
        for g in ("abuse", "scam", "fake_upi", "doxxing"):
            d = cls[g]
            rows.append(
                [
                    g,
                    d["n"],
                    _pct(d["recall"]),
                    f"{100 * d['recall_ci'][0]:.1f}-{100 * d['recall_ci'][1]:.1f}",
                    d["predicted"],
                    _pct(d["precision"]),
                ]
            )
        c = cls["clean"]
        rows.append(
            [
                "clean (false positives)",
                c["n"],
                _pct(c["false_positive"]),
                f"{100 * c['ci'][0]:.1f}-{100 * c['ci'][1]:.1f}",
                "",
                "",
            ]
        )
        r.tables.append(
            rep.Table(
                "Per-category results (gating messages, % and 95% Wilson CI)",
                ["category", "n", "recall % (FP % for clean)", "95% CI %", "predicted", "precision %"],
                rows,
            )
        )
        r.tables.append(
            rep.Table(
                "Rate by kind (recall for flagged kinds, pass rate for clean kinds)",
                ["label/kind", "n", "rate %"],
                [[k, v["n"], _pct(v["rate"])] for k, v in cls["by_kind"].items()],
            )
        )
        r.tables.append(
            rep.Table(
                "Recall by language",
                ["label/lang", "n", "recall %"],
                [[k, v["n"], _pct(v["rate"])] for k, v in cls["by_lang"].items()],
            )
        )
        r.tables.append(
            rep.Table(
                "Obfuscated vs plain",
                ["group", "n", "recall %"],
                [[k, v["n"], _pct(v["recall"])] for k, v in cls["obfuscation"].items()],
            )
        )
        lat = cls["latency_ms"]
        r.tables.append(rep.Table("Latency (ms)", ["metric", "value"], [[k, v] for k, v in lat.items()]))
        r.tables.append(
            rep.Table(
                "Other numbers",
                ["metric", "value"],
                [
                    ["actions", json.dumps(cls["actions"], sort_keys=True)],
                    ["platform delete / timeout calls", json.dumps(cls["platform_calls"], sort_keys=True)],
                    [
                        "ambiguous mild banter flagged (not gating)",
                        f"{cls['ambiguous_flagged']['flagged']} / {cls['ambiguous_flagged']['n']}",
                    ],
                    [
                        "moderator-posted private data flagged but kept visible",
                        f"{cls['staff_dox_flagged']['flagged']} / {cls['staff_dox_flagged']['n']}",
                    ],
                    ["log records scanned", cls["log_scan"]["records"]],
                ],
            )
        )
        r.tables.append(
            rep.Table(
                "Missed or wrongly flagged message ids (first 25 each; ids only)",
                ["list", "ids"],
                [
                    [g + " missed", " ".join(cls[g]["missed_ids"][:25])]
                    for g in ("abuse", "scam", "fake_upi", "doxxing")
                ]
                + [["clean flagged", " ".join(cls["clean"]["fp_ids"][:25])]],
            )
        )
    llm = legs.get("classify_llm")
    if llm:
        r.tables.append(
            rep.Table(
                "classify_llm (informational, stand-in Groq with 5 % errors)",
                ["metric", "value"],
                [[g + " recall %", _pct(llm[g]["recall"])] for g in ("abuse", "scam", "fake_upi", "doxxing")]
                + [
                    ["abuse precision %", _pct(llm["abuse"]["precision"])],
                    ["clean false positives %", _pct(llm["clean"]["false_positive"])],
                ],
            )
        )
    ho = legs.get("heldout")
    if ho:
        _heldout_tables(r, ho)
    for name in ("youtube", "twitch"):
        d = legs.get(name)
        if d and d.get("numbers"):
            r.tables.append(
                rep.Table(
                    f"{name} numbers",
                    ["metric", "value"],
                    [
                        [k, v if not isinstance(v, (dict, list)) else json.dumps(v, sort_keys=True)]
                        for k, v in d["numbers"].items()
                    ],
                )
            )
    if results and results.exists():
        steps = json.loads(results.read_text(encoding="utf-8"))["steps"]
        r.tables.append(
            rep.Table(
                "Gate steps",
                ["step", "raw exit", "gate exit", "seconds"],
                [[s["name"], s["rc"], s["mapped_rc"], s["seconds"]] for s in steps],
            )
        )
        r.header["commands"] = [s["command"] for s in steps]
    r.notes = NOTES
    md, js = rep.write_report(r, report_dir or ROOT / "eval" / "reports")
    print(f"report: {md}")
    return 0
