"""Markdown + JSON report of a video red-team run: ``eval/reports/<date>-redteam-video.{md,json}``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import score
from .attacks import CATALOGUE


def _row(*cells: Any) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def build_markdown(res: dict[str, Any]) -> str:
    h = res["header"]
    L: list[str] = []
    L.append(f"# Video red team ({h['date']})")
    L.append("")
    L.append(
        f"- git rev: `{h['git_rev']}`  profile: `{h['profile']}`  seed: {h['seed']}  hardware: {h['hardware']}"
    )
    L.append(
        f"- selection: {h['selection']}  cases run: {h['cases_run']}  exposed-target frames scored: {h['frames_scored']}"
    )
    L.append(f"- detectors: {h['detectors']}")
    L.append(f"- read-back: {h['readback']}")
    L.append(f"- command: `{h['command']}`")
    L.append("")
    L.append("## How to read this")
    L.append("")
    L.append(
        "Each case is an adversarial SPECIMEN clip. Every delivered frame with an exposed target is scored twice. "
        "**Proxy leak**: the masks the production planner produced cover less than 90 % of the target quad (the Gate 2 "
        "`cov` metric of `eval/phase2`). **Confirmed leak**: the frame is painted with the production painter in the "
        "category's configured style and the target is still recoverable from the OUTPUT pixels (zxing decodes the QR, or "
        "RapidOCR reads a long run of the card number / phone / UPI id / plate in the target region). **Leak** = proxy or "
        "confirmed (conservative headline). `src-unreadable` counts proxy leaks whose target is not recoverable even from "
        "the unmasked input (the attack destroyed legibility for OCR; a person may still read it). Read-back runs on every "
        f"{h['readback_stride']}th frame, so the confirmed rate is over checked frames. Faces are proxy only. "
        f"A target is *exposed* when at least {int(h['exposed_in_frame'] * 100)} % of its quad is in the frame and its "
        f"shortest side is at least {h['exposed_side']:.0f} px (the dataset rule is 60 % / 24 px; the red team is stricter "
        "on purpose). PROTECT (slate) frames cover everything and never leak; they are reported as `protect`."
    )
    L.append("")
    L.append("## Leak rate per group")
    L.append("")
    L.append(_row("group", "frames", "leak", "proxy", "confirmed (checked)"))
    L.append(_row("---", "---", "---", "---", "---"))
    for g, s in res["groups"].items():
        L.append(
            _row(
                g,
                s["frames"]["n"],
                score.fmt(s["frames"]),
                score.fmt(s["proxy"]),
                score.fmt(s["confirmed"]) + f" ({s['checked']})",
            )
        )
    L.append("")
    L.append("## Leak rate per attack")
    L.append("")
    L.append(_row("attack", "group", "frames", "leak", "proxy", "confirmed", "src-unreadable", "protect"))
    L.append(_row("---", "---", "---", "---", "---", "---", "---", "---"))
    for name, s in res["attacks"].items():
        L.append(
            _row(
                name,
                s["group"],
                s["frames"]["n"],
                score.fmt(s["frames"]),
                score.fmt(s["proxy"]),
                score.fmt(s["confirmed"]),
                s["src_unreadable"],
                res["protect_by_attack"].get(name, 0),
            )
        )
    L.append("")
    L.append("## Ranked misses (worst first)")
    L.append("")
    if not res["ranked"]:
        L.append("No leaks in the cases run.")
    else:
        L.append(
            "`confirmed` = target recoverable from the output frame. `proxy_readable` = uncovered and readable in the unmasked input. `proxy_unchecked` = uncovered, no read-back ran on the leaking frames. `proxy_unreadable` = uncovered but not OCR/decode-readable even unmasked (blur, distance); a person may still read it."
        )
        L.append("")
        L.append(
            _row("#", "attack[param]", "kind", "leak", "confirmed", "worst frame", "repro (in / out / meta)")
        )
        L.append(_row("---", "---", "---", "---", "---", "---", "---"))
        for i, m in enumerate(res["ranked"], 1):
            w = m["worst"]
            L.append(
                _row(
                    i,
                    f"{m['attack']}[{m['param']}]",
                    m["kind"],
                    score.fmt(m["leak"]),
                    score.fmt(m["confirmed"]),
                    f"f{w['frame']} {w['cat']} cov={w['cov']}",
                    m.get("repro", "n/a"),
                )
            )
    L.append("")
    L.append("## Per parameter")
    L.append("")
    L.append(_row("attack[param]", "frames", "leak", "proxy", "confirmed", "src-unreadable"))
    L.append(_row("---", "---", "---", "---", "---", "---"))
    for name, a in res["attacks"].items():
        for p, s in a["params"].items():
            L.append(
                _row(
                    f"{name}[{p}]",
                    s["frames"]["n"],
                    score.fmt(s["frames"]),
                    score.fmt(s["proxy"]),
                    score.fmt(s["confirmed"]),
                    s["src_unreadable"],
                )
            )
    tagged = {n: a["tags"] for n, a in res["attacks"].items() if len(a["tags"]) > 1}
    if tagged:
        L.append("")
        L.append("## By frame tag (scene cuts: frames since the cut; flash: visible)")
        L.append("")
        L.append(_row("attack", "tag", "frames", "leak"))
        L.append(_row("---", "---", "---", "---"))
        for n, tg in tagged.items():
            for t, s in tg.items():
                L.append(_row(n, t, s["frames"]["n"], score.fmt(s["frames"])))
    L.append("")
    L.append("## Not run / limits")
    L.append("")
    for n, why in res["skipped"].items():
        L.append(f"- NOT RUN `{n}`: {why}")
    for n in res["empty"]:
        L.append(f"- `{n}`: no exposed target frame was produced (nothing scored)")
    for line in res["limits"]:
        L.append(f"- {line}")
    L.append("")
    L.append("## Attack catalogue")
    L.append("")
    for name, at in CATALOGUE.items():
        if name in res["attacks"]:
            L.append(f"- `{name}` ({at.group}): {at.note or 'parameter grid: ' + ', '.join(at.grid)}")
    if res.get("regressions") is not None:
        L.append("")
        L.append("## Regression vs baseline")
        L.append("")
        L.append("none" if not res["regressions"] else "\n".join(f"- {r}" for r in res["regressions"]))
    if res.get("seeded"):
        L.append("")
        L.append("## Seeded-defect self-check")
        L.append("")
        for line in res["seeded"]:
            L.append(f"- {line}")
    L.append("")
    return "\n".join(L)


def write(res: dict[str, Any], report_dir: Path, name: str = "redteam-video") -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    md = report_dir / f"{res['header']['date']}-{name}.md"
    js = report_dir / f"{res['header']['date']}-{name}.json"
    md.write_text(build_markdown(res), encoding="utf-8")
    js.write_text(json.dumps(res, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return md, js
