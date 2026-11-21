"""CLI of the red-team suites: ``python -m eval.run redteam-audio|redteam-chat [options]``.

Exit codes: 0 pass, 1 a criterion failed (or ``--record-only`` unset and a miss-rate ceiling was exceeded), 2 environment
error (nothing measured). ``--record-only`` records miss rates without failing on the ceilings; hard invariants (crash,
leak, ``!hold`` from non-staff, replay) still fail.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .common import ROOT


def _common(p: argparse.ArgumentParser, phase: str) -> None:
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--out", default="")
    p.add_argument("--report-dir", default="")
    p.add_argument("--phase-name", default=phase)
    p.add_argument("--category", action="append", help="only this category (repeatable)")
    p.add_argument(
        "--record-only", action="store_true", help="do not fail on miss-rate ceilings (invariants still fail)"
    )
    p.add_argument(
        "--reference",
        action="store_true",
        help="stand-in module that is right by construction (validates the harness)",
    )
    p.add_argument("--defect", default="", help="with --reference: seed a defect; the run must then FAIL")
    p.add_argument("--show", default=None, help="print one case (text or params) by id and exit")


def chat_main(argv: list[str]) -> int:
    from .chat import run as chat_run

    p = argparse.ArgumentParser(prog="eval.run redteam-chat", description=chat_run.__doc__.split("\n\n")[0])
    _common(p, "redteam-chat")
    p.add_argument(
        "--per-label", type=int, default=12, help="base messages per label (abuse, scam, fake_upi, doxxing)"
    )
    p.add_argument("--data", default=chat_run.DEFAULT_DATA)
    a = p.parse_args(argv)
    if a.defect and not a.reference:
        print("ERROR: --defect needs --reference", file=sys.stderr)
        return 2
    if a.defect and a.defect not in chat_run.DEFECTS:
        print(f"ERROR: --defect must be one of {chat_run.DEFECTS}", file=sys.stderr)
        return 2
    data = Path(a.data) if Path(a.data).is_absolute() else ROOT / a.data
    return chat_run.run(
        per_label=a.per_label, seed=a.seed if a.seed is not None else chat_run.SEED, data=data,
        out=Path(a.out) if a.out else ROOT / ".tmp" / "redteam-chat", report_dir=Path(a.report_dir) if a.report_dir else None,
        categories=a.category, defect=a.defect, reference=a.reference, record_only=a.record_only, phase=a.phase_name, show=a.show,
    )  # fmt: skip


def audio_main(argv: list[str]) -> int:
    from .audio import run as audio_run

    p = argparse.ArgumentParser(prog="eval.run redteam-audio", description=audio_run.__doc__.split("\n\n")[0])
    _common(p, "redteam-audio")
    p.add_argument("--per-technique", type=int, default=2, help="clips per technique")
    p.add_argument("--technique", action="append", help="only this technique (repeatable)")
    p.add_argument("--engine", default="auto", choices=("auto", "flite", "tone"))
    p.add_argument(
        "--wordcache", default="eval/data/generated/audio/wordcache.npz", help="read-only TTS word cache"
    )
    a = p.parse_args(argv)
    if a.defect and not a.reference:
        print("ERROR: --defect needs --reference", file=sys.stderr)
        return 2
    if a.defect and a.defect not in audio_run.DEFECTS:
        print(f"ERROR: --defect must be one of {audio_run.DEFECTS}", file=sys.stderr)
        return 2
    wc = Path(a.wordcache) if Path(a.wordcache).is_absolute() else ROOT / a.wordcache
    return audio_run.run(
        per_technique=a.per_technique, seed=a.seed if a.seed is not None else audio_run.SEED, engine=a.engine, wordcache=wc,
        out=Path(a.out) if a.out else ROOT / ".tmp" / "redteam-audio", report_dir=Path(a.report_dir) if a.report_dir else None,
        categories=a.category, techniques=a.technique, defect=a.defect, reference=a.reference, record_only=a.record_only,
        phase=a.phase_name, show=a.show,
    )  # fmt: skip
