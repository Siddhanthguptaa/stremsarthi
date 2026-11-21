"""Gate 4 held-out leg (informational, never gating): the blind chat set v2 through ``chat.main.build``.

The set lives in ``eval/data/private/chat_heldout_v2/`` (gitignored). It is written in a different style and with a
different seed from the Gate 4 set, and the chat engineer has never seen it. It must never be read by ``chat/`` code,
tests or agents, and its messages must never reach a report: everything this module writes is an aggregate (counts and
rates by label, kind, language and difficulty). The few ids kept for diagnosis go to the private directory only.

Same metrics as the gated classify leg, plus the split that the gate hides: ``HOLD_FOR_MOD`` (borderline, the message
is withheld for a moderator) is counted apart from a hard flag (hide, delete, timeout). A held message counts as flagged
in ``recall`` (as in the gate) but not in ``recall_hard``, so the gap between them is how much of the score relied on
holds.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .data_gen.chat.record import Msg
from .data_gen.chat.suite import load, manifest_hash
from .harness import metrics
from .phase4 import GROUPS, install_capture, percentile, run_pipeline, scan_logs

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HELDOUT = "eval/data/private/chat_heldout_v2"
FAMILY = {"abuse": {"abuse"}, "scam": {"scam", "fake_upi"}, "fake_upi": {"fake_upi"}, "doxxing": {"doxxing"}}
POSITIVE = ("abuse", "scam", "fake_upi", "doxxing")
DIFFS = ("easy", "medium", "hard")


def difficulty(m: Msg) -> str:
    for t in m.tags:
        if t.startswith("d:"):
            return t[2:]
    return "unrated"


def _rate(k: int, n: int) -> dict[str, Any]:
    r = metrics.ratio(k, n)
    return {"k": k, "n": n, "rate": r.value, "ci": [round(r.ci_lo, 4), round(r.ci_hi, 4)]}


class _Outcome:
    """What the pipeline finally did with one message."""

    __slots__ = ("allow", "hold", "hard", "cats")

    def __init__(self, verdict: Any, allow_action: Any, hold_action: Any, cats_of: Any) -> None:
        self.allow = verdict is None or verdict.action is allow_action
        self.hold = verdict is not None and verdict.action is hold_action
        self.hard = not self.allow and not self.hold
        self.cats = cats_of(verdict) if verdict is not None else set()


_STRONG = re.compile(
    r"\b(bc|mc|bkl|bsdk|mkc|tmkc|chut\w*|chum\w*|madarch\w*|bhosd\w*|bhosad\w*|gaand\w*|gandu\w*|bhenchod\w*|bhaichod\w*"
    r"|lund|lund\w*|lavd\w*|lawd\w*|randi|randw\w*|bhadw\w*|fuck\w*|shit\w*|bastard|bitch\w*)\b",
    re.IGNORECASE,
)
_BANTER_KINDS = ("banter_friends", "sarcasm", "ambiguous")


def banter_split(msgs: list[Msg], oc: dict[str, Any]) -> dict[str, Any]:
    """Clean friendly banter and sarcasm (gated or not), split by whether the line contains real profanity
    (bc, mc, bsdk, chutiya-type words) or only mild insults. Counts only."""
    groups: dict[str, list[Msg]] = {"strong_profanity": [], "mild": []}
    for m in msgs:
        if m.label == "clean" and not m.staff and m.kind in _BANTER_KINDS:
            groups["strong_profanity" if _STRONG.search(m.text) else "mild"].append(m)
    return {
        k: {
            "n": len(ms),
            "flagged_any": sum(not oc[m.id].allow for m in ms),
            "hard": sum(oc[m.id].hard for m in ms),
            "held": sum(oc[m.id].hold for m in ms),
        }
        for k, ms in groups.items()
    }


def profile(msgs: list[Msg], run: dict[str, Any]) -> dict[str, Any]:
    """Aggregate scores of one pipeline run over a labelled set. Works for any set that carries ``Msg`` labels."""
    from contracts import Category, ChatAction  # noqa: PLC0415

    final = run["final"]
    cat_sets = {g: {getattr(Category, n) for n in names} for g, names in GROUPS.items()}
    oc = {
        m.id: _Outcome(
            final.get(m.id), ChatAction.ALLOW, ChatAction.HOLD_FOR_MOD, lambda v: set(v.categories)
        )
        for m in msgs
    }
    gated = [m for m in msgs if m.gate and not m.staff]

    def flagged(m: Msg, g: str, *, hard_only: bool = False) -> bool:
        o = oc[m.id]
        return (not o.allow) and (o.hard or not hard_only) and bool(o.cats & cat_sets[g])

    out: dict[str, Any] = {"n_messages": len(msgs), "classified": len(final)}
    for g in POSITIVE:
        pos = [m for m in gated if m.label == g]
        pred = [m for m in gated if flagged(m, g)]
        pred_hard = [m for m in gated if flagged(m, g, hard_only=True)]
        tp = sum(flagged(m, g) for m in pos)
        tp_hard = sum(flagged(m, g, hard_only=True) for m in pos)
        held_right = sum(oc[m.id].hold and bool(oc[m.id].cats & cat_sets[g]) for m in pos)
        held_nocat = sum(oc[m.id].hold and not (oc[m.id].cats & cat_sets[g]) for m in pos)
        wrong_hard = sum(oc[m.id].hard and not (oc[m.id].cats & cat_sets[g]) for m in pos)
        allowed = sum(oc[m.id].allow for m in pos)
        out[g] = {
            "n": len(pos),
            "recall": _rate(tp, len(pos)),
            "recall_hard": _rate(tp_hard, len(pos)),
            "held_with_right_category": held_right,
            "held_without_right_category": held_nocat,
            "hard_flag_wrong_category": wrong_hard,
            "allowed_missed": allowed,
            "withheld_any": len(pos) - allowed,
            "precision": metrics.ratio(sum(m.label in FAMILY[g] for m in pred), len(pred)).value,
            "precision_hard": metrics.ratio(
                sum(m.label in FAMILY[g] for m in pred_hard), len(pred_hard)
            ).value,
            "predicted": len(pred),
        }
    clean = [m for m in gated if m.label == "clean"]
    fp_any = [m for m in clean if not oc[m.id].allow]
    fp_hold = [m for m in clean if oc[m.id].hold]
    fp_hard = [m for m in clean if oc[m.id].hard]
    out["clean"] = {
        "n": len(clean),
        "false_positive": _rate(len(fp_any), len(clean)),
        "false_positive_hard": _rate(len(fp_hard), len(clean)),
        "held": _rate(len(fp_hold), len(clean)),
    }
    amb = [m for m in msgs if not m.gate and not m.staff]
    out["not_gating"] = {
        "n": len(amb),
        "flagged_hard": sum(oc[m.id].hard for m in amb),
        "held": sum(oc[m.id].hold for m in amb),
    }
    staff = [m for m in msgs if m.staff]
    sd = [m for m in msgs if m.label == "doxxing_staff"]
    out["staff"] = {
        "n": len(staff),
        "hidden": sum(
            not oc[m.id].allow for m in staff
        ),  # staff messages are never hidden, even when flagged
        "moderator_dox_flagged": sum(bool(oc[m.id].cats & cat_sets["doxxing"]) for m in sd),
        "moderator_dox_n": len(sd),
    }
    out["profane_banter"] = banter_split(msgs, oc)
    out["holds"] = {
        "total": sum(oc[m.id].hold for m in msgs),
        "of_messages": len(msgs),
        "by_true_label": {
            lab: sum(oc[m.id].hold for m in msgs if m.label == lab) for lab in sorted({m.label for m in msgs})
        },
        "hard_flags_total": sum(oc[m.id].hard for m in msgs),
        "allowed_total": sum(oc[m.id].allow for m in msgs),
    }
    # slices: recall (any flag) and hard-only recall for positives, false-positive rate for clean
    for name, keyf in (
        ("by_difficulty", difficulty),
        ("by_lang", lambda m: m.lang),
        ("by_kind", lambda m: m.kind),
    ):
        buckets: dict[str, list[Msg]] = defaultdict(list)
        for m in gated:
            buckets[
                f"{m.label}/{keyf(m)}"
                if name == "by_kind"
                else f"{'pos' if m.label in POSITIVE else 'clean'}/{keyf(m)}"
            ].append(m)
        sl: dict[str, Any] = {}
        for k, ms in sorted(buckets.items()):
            if k.startswith("clean/") or (name == "by_kind" and ms[0].label == "clean"):
                sl[k] = {
                    "n": len(ms),
                    "false_positive": sum(not oc[m.id].allow for m in ms) / len(ms),
                    "false_positive_hard": sum(oc[m.id].hard for m in ms) / len(ms),
                }
            else:
                sl[k] = {
                    "n": len(ms),
                    "recall": sum(flagged(m, m.label) for m in ms) / len(ms),
                    "recall_hard": sum(flagged(m, m.label, hard_only=True) for m in ms) / len(ms),
                    "held": sum(oc[m.id].hold for m in ms) / len(ms),
                }
        out[name] = sl
    verdict_ms = [v.latency_ms for v in run["first"].values()]
    out["latency_ms"] = {
        "classifier_p50": percentile(verdict_ms, 50),
        "classifier_p95": percentile(verdict_ms, 95),
        "end_to_end_p95": percentile(run["e2e_ms"], 95),
    }
    out["unclassified"] = len(run["missing"])
    out["flagged_text_on_overlay"] = sum(
        1 for m in gated if m.label in POSITIVE and m.id in run["allowed"] and not oc[m.id].allow
    )
    return out


def failure_ids(msgs: list[Msg], run: dict[str, Any]) -> dict[str, list[str]]:
    """Ids only (no text), for the private directory: positives allowed or held without the right category,
    clean messages flagged."""
    from contracts import Category, ChatAction  # noqa: PLC0415

    final = run["final"]
    cat_sets = {g: {getattr(Category, n) for n in names} for g, names in GROUPS.items()}
    miss: dict[str, list[str]] = defaultdict(list)
    for m in msgs:
        if m.staff or not m.gate:
            continue
        v = final.get(m.id)
        allowed = v is None or v.action is ChatAction.ALLOW
        if m.label in POSITIVE:
            if allowed or not (set(v.categories) & cat_sets[m.label]):
                miss[f"missed/{m.label}"].append(m.id)
        elif m.label == "clean" and not allowed:
            miss["clean_flagged"].append(m.id)
    return dict(miss)


def leg_heldout(data: Path, reference: bool = False) -> dict[str, Any]:
    import asyncio  # noqa: PLC0415

    manifest, msgs = load(data)
    cap = install_capture()
    run = asyncio.run(run_pipeline(msgs, manifest["streamer_private"], llm=None, reference=reference))
    res = profile(msgs, run)
    secrets = (
        [m.text for m in msgs if m.label != "clean" or m.staff]
        + list(manifest["streamer_private"]["terms"])
        + list(manifest["streamer_private"]["phones"])
    )
    res["log_scan"] = scan_logs(cap, secrets)
    res["leg"] = "heldout"
    res["informational"] = True
    res["reference"] = reference
    res["llm"] = "none (rules and local fallback only)"
    res["dataset"] = {
        "name": data.name,
        "seed": manifest["seed"],
        "generator_version": manifest["generator_version"],
        "totals": {k: v for k, v in manifest["totals"].items() if k not in ("by_kind",)},
        "sha256": manifest_hash(manifest),
    }
    res["_failure_ids"] = failure_ids(msgs, run)  # popped by the caller: never written to the run directory
    return res


def write_private_failures(data: Path, res: dict[str, Any]) -> None:
    ids = res.pop("_failure_ids", None)
    if ids is not None:
        (data / "last_run_failure_ids.json").write_text(
            json.dumps(ids, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
