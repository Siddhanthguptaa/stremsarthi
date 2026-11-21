"""Gate 5 criteria and the ``eval/reports/<date>-phase5.md`` writer (reads the leg JSONs ``eval.phase5`` wrote)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .harness import report as rep

ROOT = Path(__file__).resolve().parents[1]
LEGS = ("data", "music", "fingerprint", "profanity", "meter", "captions_render", "captions_timing")
NOTES = [
    "ALL AUDIO IS SYNTHETIC. Songs are seeded numpy synthesis (pads, lead, bass, kick and hats; eval/data_gen/guard/songs.py, "
    "independent of guard.music.synth) and speech is offline English flite TTS. Music recall and fingerprint numbers "
    "therefore say nothing about real commercial music, covers or live versions; they test the pipeline and thresholds.",
    "Profanity is scored at transcript level: the offline TTS cannot say Hinglish or regional profanity and the guard reads "
    "the transcript anyway. Word times are exact by construction. The regional and variant groups are informational.",
    "The risk-meter leg injects signals on a virtual clock (no real time passes): it measures the aggregator and its hysteresis, "
    "not the audio path before it. Music-ON latency (clip start to ON) is reported separately and is informational.",
    "Caption shaping compares the engine mask with an independent Pillow+libraqm reference on separate Noto font files; "
    "the arrival leg runs the real CaptionService with a stand-in translation provider (modelled latency 0.15 to 0.5 s) and a "
    "monotonic virtual capture clock, not a live Sarvam call.",
    "No GPU is used by any leg of this gate (the guard is CPU only), so no GPU lock is taken.",
]


def _nr(res: dict[str, Any], name: str, target: float, op: str = ">=") -> rep.Check:
    return rep.Check(name, None, op, target, not_run=True, note=f"NOT RUN: {res.get('not_run', 'no result')}")


def evaluate(res: dict[str, Any]) -> list[rep.Check]:
    leg = res.get("leg")
    C = rep.Check
    if leg == "data":
        return [
            C(
                "data: problems found",
                float(len(res.get("problems", []))),
                "==",
                0.0,
                note="; ".join(res.get("problems", [])),
            )
        ]
    if res.get("not_run"):
        return {
            "music": [
                _nr(res, "music detect recall", 0.90),
                _nr(res, "music detect false positives on speech", 0.05, "<="),
            ],
            "fingerprint": [_nr(res, "fingerprint match, noisy 5 s snippets", 0.90)],
            "profanity": [_nr(res, "Hinglish profanity recall", 0.85)],
            "meter": [_nr(res, "risk meter reaction time", 2.0, "<=")],
            "captions_render": [_nr(res, "captions render in all 5 scripts", 5.0)],
            "captions_timing": [_nr(res, "captions arrive before display (late published)", 0.0, "==")],
        }.get(str(leg), [])
    if leg == "music":
        return [
            C("music detect recall (music only + under speech)", res["recall"]["value"], ">=", 0.90),
            C(
                "music detect false positives on speech-only clips",
                res["false_positive"]["value"],
                "<=",
                0.05,
            ),
        ]
    if leg == "fingerprint":
        return [
            C("fingerprint match, correct id, noisy 5 s snippets", res["match_correct"]["value"], ">=", 0.90),
            C("fingerprint wrong id on library snippets", res["match_wrong_id"]["value"], "<=", 0.0),
            C(
                "fingerprint false match on unrelated music",
                res["false_match"]["value"],
                "<=",
                0.05,
                note="spec test 3; eval-chosen bound",
            ),
        ]
    if leg == "profanity":
        return [
            C("Hinglish profanity recall (Latin + Devanagari)", res["hinglish_recall"]["value"], ">=", 0.85),
            C(
                "profanity false positives on clean speech",
                res["false_positive"]["value"],
                "<=",
                0.03,
                note="eval-chosen bound (as chat)",
            ),
            C(
                "profanity false positives on harmless look-alikes",
                res["trap_false_positive"]["value"],
                "<=",
                0.0,
                note="spec test 5: benign_allow gives zero hits",
            ),
            C("profanity signal within 0.6 s of the word", res["signal_time_ok"]["value"], ">=", 0.95),
            C(
                "profanity processing p95",
                (res["process_ms"]["p95"] or 0.0) / 1000.0,
                "<=",
                0.5,
                "s",
                note="spec 10",
            ),
        ]
    if leg == "meter":
        mx = res["reaction_s"]["max"]
        return [
            C(
                "risk meter reaction to an injected signal, max over scenarios",
                99.0 if mx is None else mx,
                "<=",
                2.0,
                "s",
            ),
            C("risk meter reaches the expected level", res["level_ok"]["value"], ">=", 1.0),
            C(
                "risk meter publication rate while a signal is live",
                res["publish_hz"],
                ">=",
                1.8,
                "Hz",
                note="spec test 9: 2 Hz within 10 %",
            ),
            C("risk meter publication rate upper bound", res["publish_hz"], "<=", 2.2, "Hz"),
        ]
    if leg == "captions_render":
        return [
            C(
                "captions render and shape correctly in scripts",
                float(res["scripts_ok"]),
                ">=",
                5.0,
                note=f"of {res['scripts_total']}: shaping vs raqm reference, and burn-in placement/window/toggle",
            ),
            C("libraqm in use", 1.0 if res.get("raqm") else 0.0, ">=", 1.0),
        ]
    if leg == "captions_timing":
        return [
            C(
                "captions published after their display time",
                float(res["late_published"]),
                "==",
                0.0,
                note=f"{res['n_published']} published for {res['n_segments']} segments",
            ),
            C(
                "segments that got a caption (not skipped as late)",
                res["covered"]["value"],
                ">=",
                0.75,
                note="eval-chosen bound; a service that skips everything must not pass",
            ),
            C("muted words shown in a caption", float(res["secret_word_leaks"]), "==", 0.0),
        ]
    return []


def _r(d: dict[str, Any]) -> str:
    return f"{d['k']}/{d['n']} = {d['value']} (CI {d['ci'][0]}-{d['ci'][1]})"


def build_report(out_dir: Path, results: Path | None, phase: str, report_dir: Path | None) -> int:
    r = rep.Report(phase, time.strftime("%Y-%m-%d"))
    legs: dict[str, dict[str, Any]] = {}
    for p in sorted(out_dir.glob("phase5-*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        legs[d.get("leg", p.stem)] = d
    d0 = legs.get("data", {})
    r.header = {
        "git_rev": rep.git_rev(), "profile": "dev", "hardware": rep.hardware_info(), "gpu": "not used (CPU only)", "seed": d0.get("seed", 1234),
        "dataset": d0.get("totals", {}), "tts_engine": d0.get("engine", "unknown"),
        "music_backend": legs.get("music", {}).get("backend", "not run"),
        "fingerprint_backend": legs.get("fingerprint", {}).get("backend", "not run"),
        "stand_ins": "reference stand-ins (harness validation, not product numbers)" if any(v.get("reference") for v in legs.values()) else "none: product components",
    }  # fmt: skip
    for name in LEGS:
        if name not in legs:
            r.checks.append(
                rep.Check(f"{name} leg ran", None, "==", 1.0, not_run=True, note="NOT RUN: no result file")
            )
            continue
        r.checks += evaluate(legs[name])
    if (m := legs.get("music")) and not m.get("not_run"):
        rows = [
            ["music only", _r(m["recall_music_only"])],
            ["music under speech (0 dB)", _r(m["recall_under_speech"])],
            ["speech only (false positives)", _r(m["false_positive"])],
        ]
        rows += [[f"  speech, noise={k}", _r(v)] for k, v in m["fp_by_noise"].items()]
        L = m["on_latency_s"]
        rows += [
            [
                "music ON latency from music start (informational)",
                f"n={L['n']} p50={L['p50']} p95={L['p95']} max={L['max']} s",
            ]
        ]
        r.tables.append(rep.Table("Music detection", ["set", "result"], rows))
        r.failing_ids += [f"music missed: {i}" for i in m["missed_ids"]] + [
            f"music false positive: {i}" for i in m["fp_ids"]
        ]
    if (f := legs.get("fingerprint")) and not f.get("not_run"):
        rows = [
            ["correct id", _r(f["match_correct"])],
            ["wrong id", _r(f["match_wrong_id"])],
            ["false match (unrelated)", _r(f["false_match"])],
        ]
        rows += [[f"  codec {k}", _r(v)] for k, v in f["by_codec"].items()]
        rows += [
            ["15 % tempo change matched (documented non-match)", _r(f["tempo_15pct_matched"])],
            ["match time ms p50 / p95", f"{f['match_ms']['p50']} / {f['match_ms']['p95']}"],
        ]
        r.tables.append(rep.Table("Fingerprint (5 s snippets, 10 dB pink noise)", ["set", "result"], rows))
        r.failing_ids += [f"fingerprint missed: {i}" for i in f["missed_ids"]]
    if (pf := legs.get("profanity")) and not pf.get("not_run"):
        rows = [[g, _r(v)] for g, v in pf["by_group"].items()] + [
            [f"Hinglish severity {k}", _r(v)] for k, v in pf["by_level"].items()
        ]
        rows += [
            ["clean false positives", _r(pf["false_positive"])],
            ["harmless look-alike false positives", _r(pf["trap_false_positive"])],
            ["signal time ok", _r(pf["signal_time_ok"])],
        ]
        r.tables.append(rep.Table("Profanity (transcript level)", ["group", "recall / rate"], rows))
        r.failing_ids += [f"profanity missed: {i}" for i in pf["missed_ids"][:30]] + [
            f"profanity false positive: {i}" for i in pf["fp_ids"]
        ]
    if (mt := legs.get("meter")) and not mt.get("not_run"):
        r.tables.append(rep.Table("Risk meter (virtual clock)", ["scenario", "expected", "peak", "reaction s", "back to green s", "warnings"],
                                  [[s["scenario"], s["expect"], s["peak"], s["reaction_s"], s["back_to_green_s"], s["warnings"]] for s in mt["scenarios"]]))  # fmt: skip
    if (cr := legs.get("captions_render")) and not cr.get("not_run"):
        r.tables.append(rep.Table("Caption rendering", ["lang", "script", "shaping vs raqm reference", "burn-in checks"],
                                  [[k, v["script"], _r(v["shaping_ok"]), _r(v["frame_ok"])] for k, v in cr["by_lang"].items()]))  # fmt: skip
        r.failing_ids += [f"caption render: {b}" for b in cr["bad"]]
    if (ct := legs.get("captions_timing")) and not ct.get("not_run"):
        r.tables.append(rep.Table("Caption arrival", ["metric", "value"], [["segments / published", f"{ct['n_segments']} / {ct['n_published']}"], ["covered segments", _r(ct["covered"])],
                                  ["margin s (display minus publish) min / p5 / p50", f"{ct['margin_s']['min']} / {ct['margin_s']['p5']} / {ct['margin_s']['p50']}"], ["service stats", json.dumps(ct["service_stats"], sort_keys=True)]]))  # fmt: skip
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
    md, _ = rep.write_report(r, report_dir or ROOT / "eval" / "reports")
    print(f"report: {md}")
    return 0
