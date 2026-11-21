"""Gate 3 criteria and the ``eval/reports/<date>-phase3.md`` writer (reads the leg JSONs ``eval.phase3`` wrote)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .harness import report as rep

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("data", "main", "api_down", "local_only")
NOTES = [
    "THE RECOGNISER IS AN ORACLE (eval/phase3.py OracleStt): it answers from the ground-truth words with seeded "
    "drop/mishear/jitter errors. The offline TTS (English flite only) cannot be recognised by a real STT, so these "
    "numbers test the audio pipeline (VAD segmentation, timing, PII rules, merging, fail-safe) and the engine's mute "
    "application, not recognition accuracy. A native-TTS or recorded-audio run with a real STT is still open.",
    "Mute coverage is read from OUTPUT audio: spans are applied by engine.audio_mute.MuteBook to the 48 kHz clip as "
    "the delay buffer would (delay 4.0 s); a span ready after its samples left is late and leaks.",
    "The PII LLM (Groq) is absent in every leg (no key, offline): ambiguous spans use the rules' own fallback.",
    "Mouth blur is checked at the audio->engine hand-off (request windows plus engine.mouth.resolve_mouth_quads, "
    "perfect vision on every 3rd frame), not on composited pixels.",
    "SS_FORCE_API_DOWN is not read by the product code today; the api_down leg injects provider failures itself.",
]


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
    elif leg == "main":
        out += [
            rep.Check("mute coverage, PII words (duration-weighted)", res["pii"]["dur_coverage"], ">=", 0.90),
            rep.Check("mute coverage, spoken digit words", res["digits"]["dur_coverage"], ">=", 0.95),
            rep.Check("over-mute of non-PII speech", res["over_mute"], "<=", 0.15),
            rep.Check("audio pipeline latency p95", res["audio_latency_s"]["p95"], "<=", 3.3, "s"),
            rep.Check(
                "mouth blur on muted frames with an allowlisted face", res["mouth_blur"]["rate"], ">=", 0.95
            ),
        ]
    elif leg == "api_down":
        out += [
            rep.Check("API down: PII words muted (fail-safe)", res["pii"]["dur_coverage"], ">=", 1.0),
            rep.Check(
                "API down: all voiced speech muted",
                res["all_speech"]["dur_coverage"],
                ">=",
                0.99,
                note="plan: 100 %; 1 % slack for VAD onset slivers",
            ),
        ]
    elif leg == "local_only":
        out.append(
            rep.Check("API down, local recogniser: PII words muted", res["pii"]["dur_coverage"], ">=", 0.70)
        )
    return out


def _group_table(title: str, groups: dict[str, Any]) -> rep.Table:
    rows = [
        [k, v["n"], v["word_recall"], v["dur_coverage"], f"{v['ci'][0]:.3f}-{v['ci'][1]:.3f}"]
        for k, v in groups.items()
    ]
    return rep.Table(
        title, ["group", "PII words", "word recall", "duration coverage", "95% CI (words)"], rows
    )


def build_report(out_dir: Path, results: Path | None, phase: str, report_dir: Path | None) -> int:
    r = rep.Report(phase, time.strftime("%Y-%m-%d"))
    legs: dict[str, dict[str, Any]] = {}
    for p in sorted(out_dir.glob("phase3-*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        legs[d.get("leg", p.stem)] = d
    r.header = {
        "git_rev": rep.git_rev(),
        "profile": "dev",
        "hardware": rep.hardware_info(),
        "seed": legs.get("data", {}).get("seed", 1234),
        "dataset": legs.get("data", {}).get("totals", {}),
        "tts_engine": legs.get("data", {}).get("engine", "unknown"),
        "vad": legs.get("main", {}).get("vad", "not run"),
        "stt": "oracle recogniser (see notes)",
    }
    for name in REQUIRED:
        if name not in legs:
            r.checks.append(rep.Check(f"{name} leg ran", None, "==", 1.0, note="not run: no result file"))
            continue
        r.checks += evaluate(legs[name])
    main = legs.get("main")
    if main:
        r.tables += [
            _group_table("Mute coverage by language", main["by"]["lang"]),
            _group_table("Mute coverage by PII kind", main["by"]["kind"]),
            _group_table("Mute coverage by cue wording", main["by"]["cue"]),
            _group_table("Mute coverage by clip bucket", main["by"]["bucket"]),
            _group_table("Mute coverage by background noise", main["by"]["noise"]),
        ]
        lat = main["audio_latency_s"]
        rows = [
            ["audio pipeline (segment start to span ready)", lat["n"], lat["p50"], lat["p95"], lat["max"]]
        ]
        rows += [[f"stage {k}", v["n"], v["p50"], v["p95"], v["max"]] for k, v in main["stage_s"].items()]
        r.tables.append(
            rep.Table(
                "Latency (virtual time, modelled STT latency)",
                ["stage", "n", "p50 s", "p95 s", "max s"],
                rows,
            )
        )
        r.tables.append(
            rep.Table(
                "Other numbers (main leg)",
                ["metric", "value"],
                [
                    ["clips / audio seconds", f"{main['n_clips']} / {main['audio_s']}"],
                    [
                        "digit sequences fully muted",
                        f"{main['digit_sequences']['complete']} / {main['digit_sequences']['total']}",
                    ],
                    ["hard-negative number phrases muted", main["hard_negative_mute"]],
                    ["late or partly late spans", main["late_or_partly_late_spans"]],
                    ["leaked seconds inside the pipeline", main["leaked_seconds_in_pipeline"]],
                    [
                        "pipeline compute seconds per audio second (VAD+PII, CPU)",
                        main["compute_s_per_audio_s"],
                    ],
                    ["pipeline stats", json.dumps(main["pipeline_stats"], sort_keys=True)],
                ],
            )
        )
        r.tables.append(
            rep.Table(
                "Clips with the most missed PII seconds (ids only)",
                ["clip", "missed PII s"],
                [[w["clip"], w["missed_pii_s"]] for w in main["worst_clips"][:15]],
            )
        )
    for name in ("api_down", "local_only", "perfect"):
        d = legs.get(name)
        if d:
            r.tables.append(
                rep.Table(
                    f"Leg {name} ({d['n_clips']} clips)",
                    ["metric", "value"],
                    [
                        ["PII words muted (duration)", d["pii"]["dur_coverage"]],
                        ["all voiced speech muted (duration)", d["all_speech"]["dur_coverage"]],
                        ["over-mute", d["over_mute"]],
                        ["audio latency p95 s", d["audio_latency_s"]["p95"]],
                        ["fail-safe segments", d["pipeline_stats"].get("failsafe", 0)],
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
