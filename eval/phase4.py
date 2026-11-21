"""Gate 4 (T4.1): the chat eval set through ``chat.main.build`` and the connectors against the mock platforms.

Legs (``python -m eval.phase4 --leg ...``; each writes ``<out>/phase4-<leg>.json``; exit 0 ok, 1 criterion failed,
2 environment/setup error, nothing measured):

* ``data``      the set exists, has about 2,000 messages, every label, the staff messages, a clean SPECIMEN lint.
* ``classify``  every message through ``chat.main.build`` (``ChatPipeline`` + ``ChatClassifier``) with NO LLM
                (offline: borderline messages become HOLD_FOR_MOD, never ALLOW). Per-category precision and
                recall with Wilson intervals, clean false-positive rate, staff messages never hidden, classifier
                and end-to-end latency, and a log scan: no message text and no private term in any log record.
* ``classify_llm`` informational: the same with a stand-in Groq (``OracleLlm``: truth labels with 5 % errors).
* ``youtube`` / ``twitch``  see ``eval.phase4_conn``.
* ``heldout``   informational, never gating (``--heldout``): the blind held-out chat set v2 (``eval.phase4_heldout``).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from .data_gen.chat.record import Msg
from .data_gen.chat.suite import DEFAULT_SEED, build_suite, load, manifest_hash
from .data_gen.common.specimen import scan_obj
from .harness import metrics

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = "eval/data/generated/chat"
EXIT_OK, EXIT_FAIL, EXIT_ENV = 0, 1, 2
BATCH = 40
DEFECTS = ("no_scam", "slow_classifier", "leak_logs")  # seeded into the reference classifier


# --------------------------------------------------------------------------- helpers shared with phase4_conn
class LogCapture(logging.Handler):
    """Every record at every level, rendered as the formatter would; scanned for sensitive text afterwards."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # a broken format string in a log call is itself worth knowing about
            msg = str(record.msg) + " " + str(record.args)
        extra = " ".join(
            str(v) for k, v in record.__dict__.items() if k not in _STD and not k.startswith("_")
        )
        exc = ""
        if record.exc_info and record.exc_info[1] is not None:
            exc = repr(record.exc_info[1])
        self.lines.append(f"{record.name} {msg} {extra} {exc}")


_STD = set(logging.LogRecord("x", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def install_capture() -> LogCapture:
    cap = LogCapture()
    root = logging.getLogger()
    root.addHandler(cap)
    root.setLevel(logging.DEBUG)
    return cap


def scan_logs(cap: LogCapture, secrets: list[str]) -> dict[str, Any]:
    """Count (never print) log lines that contain any of ``secrets`` (message texts, private terms, phones)."""
    blob = [ln.lower() for ln in cap.lines]
    hits = 0
    for s in {x.lower() for x in secrets if len(x) >= 6}:
        hits += sum(s in ln for ln in blob)
    return {"records": len(blob), "leaks": hits}


def percentile(xs: list[float], q: float) -> float | None:
    return round(float(np.percentile(np.asarray(xs), q)), 3) if xs else None


def chat_message(m: Msg, i: int = 0) -> Any:
    from contracts import ChatMessage  # noqa: PLC0415

    return ChatMessage(
        platform=m.platform,  # type: ignore[arg-type]
        message_id=m.id,
        channel_id="chan_specimen",
        author_id=m.author,
        author_display="viewer",
        text=m.text,
        received_at=1_700_000_000.0 + i,
        is_broadcaster=m.staff == "broadcaster",
        is_moderator=m.staff == "moderator",
    )


def chat_config(private: dict[str, Any], *, llm: bool) -> Any:
    from contracts.config import ChatConfig, ClassifyConfig, StreamerPrivateConfig  # noqa: PLC0415

    return ChatConfig(
        classify=ClassifyConfig(llm_enabled=llm),
        streamer_private=StreamerPrivateConfig(terms=list(private["terms"]), phones=list(private["phones"])),
    )


class RecordingActions:
    """A platform adapter that records what the pipeline asked for (the classify legs have no platform)."""

    allow_timeout = True

    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.timed_out: list[str] = []

    async def delete(self, message: Any) -> Any:
        from chat.common import ActionResult  # noqa: PLC0415

        self.deleted.append(message.message_id)
        return ActionResult("done", "ok")

    async def timeout(self, message: Any, seconds: int) -> Any:
        from chat.common import ActionResult  # noqa: PLC0415

        self.timed_out.append(message.message_id)
        return ActionResult("done", "ok")


class ReferenceClassifier:
    """A stand-in classifier that is right by construction (answers from the truth labels): validates the harness, the
    way eval/refengine.py does for gate 1. Seeded defects: ``no_scam`` (scam and fake-UPI lines are allowed),
    ``slow_classifier`` (0.35 s per message), ``leak_logs`` (the message text is logged)."""

    def __init__(self, msgs: list[Msg], defect: str = "") -> None:
        self.truth = {m.id: m for m in msgs}
        self.defect = defect

    async def classify(self, batch: Any) -> list[Any]:
        from contracts import Category, ChatAction, ChatVerdict  # noqa: PLC0415

        out = []
        t_batch = time.perf_counter()
        if self.defect == "slow_classifier":
            await asyncio.sleep(0.35)  # the whole batch is late
        for msg in batch:
            t0 = t_batch if self.defect == "slow_classifier" else time.perf_counter()
            truth = self.truth[msg.message_id]
            if self.defect == "leak_logs":
                logging.getLogger("chat.classify").warning("classified %s", msg.text)
            cat = {
                "abuse": Category.CHAT_ABUSE,
                "scam": Category.CHAT_SCAM_LINK,
                "fake_upi": Category.CHAT_FAKE_UPI,
                "doxxing": Category.CHAT_DOXXING,
                "doxxing_staff": Category.CHAT_DOXXING,
            }.get(truth.label)
            action, reason, cats = ChatAction.ALLOW, "clean", ()
            if cat is not None and not (self.defect == "no_scam" and truth.label in ("scam", "fake_upi")):
                cats = (cat,)
                if truth.label == "doxxing_staff":
                    reason = "staff_dox_flag"
                else:
                    action, reason = (
                        (ChatAction.HIDE_FROM_STREAMER if truth.label == "abuse" else ChatAction.DELETE),
                        "reference",
                    )
            out.append(
                ChatVerdict(
                    msg.platform,
                    msg.message_id,
                    action,
                    cats,
                    0.95,
                    reason,
                    "rules",
                    (time.perf_counter() - t0) * 1000.0,
                )
            )
        return out


class NoLlm:
    """A transport that always fails: the offline legs never reach Groq, whatever the environment holds."""

    async def complete(self, *, system: str, user: str, model: str, timeout_s: float, max_tokens: int) -> str:
        from chat.classify import LlmError  # noqa: PLC0415

        raise LlmError("offline")


class OracleLlm:
    """Stand-in for Groq: answers from the truth labels with a seeded error rate (informational leg only)."""

    def __init__(self, msgs: list[Msg], error: float = 0.05) -> None:
        self.truth = {m.text: m.label for m in msgs}
        self.error = error
        self.calls = 0

    async def complete(self, *, system: str, user: str, model: str, timeout_s: float, max_tokens: int) -> str:
        self.calls += 1
        items = json.loads(user)["messages"]
        out = []
        for it in items:
            label = self.truth.get(it["t"], "clean")
            flip = int.from_bytes(hashlib.sha256(it["t"].encode()).digest()[:2], "big") / 65536.0 < self.error
            labels = [] if label in ("clean", "doxxing_staff") else [label]
            if flip:
                labels = ["abuse"] if not labels else []
            out.append({"i": it["i"], "labels": labels or ["clean"], "confidence": 0.9, "target": "none"})
        return json.dumps({"results": out})


# --------------------------------------------------------------------------- the classify legs
async def run_pipeline(
    msgs: list[Msg], private: dict[str, Any], *, llm: Any | None, defect: str = "", reference: bool = False
) -> dict[str, Any]:
    from chat.main import build  # noqa: PLC0415
    from contracts import ChatAction, Topic  # noqa: PLC0415
    from contracts.config import Config  # noqa: PLC0415
    from contracts.fakes import InProcessBus  # noqa: PLC0415

    t_submit: dict[str, float] = {}
    t_verdict: dict[str, float] = {}

    class TimedBus(InProcessBus):
        def publish(self, topic: Any, event: Any) -> None:
            if topic is Topic.CHAT_VERDICT:
                t_verdict[event.message_id] = time.perf_counter()
            super().publish(topic, event)

    bus = TimedBus()
    chat_cfg = chat_config(private, llm=llm is not None)
    cfg = Config(chat=chat_cfg)
    yt, tw = RecordingActions(), RecordingActions()
    extra: dict[str, Any] = {"classifier": ReferenceClassifier(msgs, defect)} if reference else {}
    pipe = build(cfg, bus, youtube=yt, twitch=tw, llm=llm if llm is not None else NoLlm(), **extra)  # type: ignore[arg-type]
    pipe.start()
    cm = [chat_message(m, i) for i, m in enumerate(msgs)]
    t0 = time.perf_counter()
    for i in range(0, len(cm), BATCH):
        for m in cm[i : i + BATCH]:
            t_submit[m.message_id] = time.perf_counter()
            pipe.submit(m)
        await pipe.idle(120.0)
        await asyncio.sleep(0)
    clf_tasks = getattr(pipe.classifier, "_tasks", set())
    for _ in range(2000):
        if not clf_tasks:
            break
        await asyncio.sleep(0.005)
    await pipe.idle(60.0)
    wall = time.perf_counter() - t0
    final: dict[str, Any] = {}
    first: dict[str, Any] = {}
    for v in bus.of(Topic.CHAT_VERDICT):
        first.setdefault(v.message_id, v)
        final[v.message_id] = v
    stats = pipe.stats()
    allowed_ids = {m.message_id for m in bus.of(Topic.CHAT_ALLOWED)}
    queued = {i.message.message_id for i in bus.of(Topic.CHAT_MODQUEUE)}
    await pipe.stop()
    return {
        "final": final,
        "first": first,
        "e2e_ms": [(t_verdict[k] - t_submit[k]) * 1000.0 for k in t_submit if k in t_verdict],
        "stats": stats,
        "allowed": allowed_ids,
        "queued": queued,
        "deleted": list(yt.deleted + tw.deleted),
        "timed_out": list(yt.timed_out + tw.timed_out),
        "wall_s": wall,
        "missing": [k for k in t_submit if k not in final],
        "ChatAction": ChatAction,
    }


GROUPS = {
    "abuse": ("CHAT_ABUSE",),
    "scam": ("CHAT_SCAM_LINK", "CHAT_FAKE_UPI"),
    "fake_upi": ("CHAT_FAKE_UPI",),
    "doxxing": ("CHAT_DOXXING",),
}


def score(msgs: list[Msg], run: dict[str, Any]) -> dict[str, Any]:
    from contracts import Category  # noqa: PLC0415

    allow = run["ChatAction"].ALLOW
    final = run["final"]
    cats = {g: {getattr(Category, n) for n in names} for g, names in GROUPS.items()}

    def flagged(m: Msg, group: str) -> bool:
        v = final.get(m.id)
        return v is not None and v.action is not allow and any(c in cats[group] for c in v.categories)

    def any_flag(m: Msg) -> bool:
        v = final.get(m.id)
        return v is None or v.action is not allow  # an unclassified message counts as not-allowed (hidden)

    gated = [m for m in msgs if m.gate and not m.staff]
    out: dict[str, Any] = {"n_messages": len(msgs), "classified": len(final), "missing": len(run["missing"])}
    fam = {"abuse": {"abuse"}, "scam": {"scam", "fake_upi"}, "fake_upi": {"fake_upi"}, "doxxing": {"doxxing"}}
    truth_label = {"abuse": "abuse", "scam": "scam", "fake_upi": "fake_upi", "doxxing": "doxxing"}
    for g in GROUPS:
        pos = [m for m in gated if m.label == truth_label[g]]
        tp = sum(flagged(m, g) for m in pos)
        predicted = [m for m in gated if flagged(m, g)]
        tp_pred = sum(m.label in fam[g] for m in predicted)
        r, p = metrics.ratio(tp, len(pos)), metrics.ratio(tp_pred, len(predicted))
        out[g] = {
            "n": len(pos),
            "recall": r.value,
            "recall_ci": [round(r.ci_lo, 4), round(r.ci_hi, 4)],
            "predicted": len(predicted),
            "precision": p.value,
            "missed_ids": [m.id for m in pos if not flagged(m, g)][:60],
        }
    clean = [m for m in gated if m.label == "clean"]
    fp = [m for m in clean if any_flag(m)]
    fr = metrics.ratio(len(fp), len(clean))
    out["clean"] = {
        "n": len(clean),
        "false_positive": fr.value,
        "ci": [round(fr.ci_lo, 4), round(fr.ci_hi, 4)],
        "fp_ids": [m.id for m in fp][:60],
        "fp_by_kind": dict(Counter(m.kind for m in fp)),
    }
    ungated = [m for m in msgs if not m.gate and m.label == "clean"]
    out["ambiguous_flagged"] = {"n": len(ungated), "flagged": sum(any_flag(m) for m in ungated)}
    staff = [m for m in msgs if m.staff]
    hidden_staff = [m for m in staff if final.get(m.id) is not None and final[m.id].action is not allow]
    out["staff"] = {
        "n": len(staff),
        "hidden": len(hidden_staff),
        "hidden_ids": [m.id for m in hidden_staff][:20],
    }
    sd = [m for m in msgs if m.label == "doxxing_staff"]
    out["staff_dox_flagged"] = {
        "n": len(sd),
        "flagged": sum(
            any(c is Category.CHAT_DOXXING for c in final[m.id].categories) for m in sd if m.id in final
        ),
    }
    by_kind: dict[str, list[bool]] = defaultdict(list)
    by_lang: dict[str, list[bool]] = defaultdict(list)
    obf: dict[str, list[bool]] = defaultdict(list)
    for m in gated:
        if m.label in truth_label:
            hit = flagged(m, m.label)
            by_kind[f"{m.label}/{m.kind}"].append(hit)
            by_lang[f"{m.label}/{m.lang}"].append(hit)
            obf["obfuscated" if "obfuscated" in m.tags else "plain"].append(hit)
        elif m.label == "clean":
            by_kind[f"clean/{m.kind}"].append(not any_flag(m))
    out["by_kind"] = {k: {"n": len(v), "rate": sum(v) / len(v)} for k, v in sorted(by_kind.items())}
    out["by_lang"] = {k: {"n": len(v), "rate": sum(v) / len(v)} for k, v in sorted(by_lang.items())}
    out["obfuscation"] = {k: {"n": len(v), "recall": sum(v) / len(v)} for k, v in sorted(obf.items())}
    verdict_ms = [v.latency_ms for v in run["first"].values()]
    out["latency_ms"] = {
        "classifier_p50": percentile(verdict_ms, 50), "classifier_p95": percentile(verdict_ms, 95), "classifier_max": round(max(verdict_ms), 3) if verdict_ms else None,
        "end_to_end_p50": percentile(run["e2e_ms"], 50), "end_to_end_p95": percentile(run["e2e_ms"], 95), "batch": BATCH,
    }  # fmt: skip
    out["actions"] = {a.value: sum(1 for v in final.values() if v.action is a) for a in run["ChatAction"]}
    out["platform_calls"] = {"deleted": len(run["deleted"]), "timed_out": len(run["timed_out"])}
    out["hidden_from_overlay_flagged"] = sum(
        1 for m in msgs if m.label in truth_label and m.id not in run["allowed"]
    )
    out["flagged_text_on_overlay"] = sum(
        1
        for m in msgs
        if m.label in truth_label
        and m.gate
        and m.id in run["allowed"]
        and final.get(m.id) is not None
        and final[m.id].action is not allow
    )
    out["pipeline_stats"] = {k: v for k, v in run["stats"].items() if isinstance(v, int | float)}
    out["wall_s"] = round(run["wall_s"], 2)
    return out


def leg_classify(data: Path, llm: bool, defect: str, reference: bool = False) -> dict[str, Any]:
    manifest, msgs = load(data)
    cap = install_capture()
    oracle = OracleLlm(msgs) if llm else None
    run = asyncio.run(
        run_pipeline(msgs, manifest["streamer_private"], llm=oracle, defect=defect, reference=reference)
    )
    res = score(msgs, run)
    secrets = (
        [m.text for m in msgs if m.label != "clean" or m.staff]
        + list(manifest["streamer_private"]["terms"])
        + list(manifest["streamer_private"]["phones"])
    )
    res["log_scan"] = scan_logs(cap, secrets)
    res["leg"] = "classify_llm" if llm else "classify"
    res["defect"] = defect
    res["reference"] = reference
    res["llm"] = "OracleLlm (5 % errors)" if llm else "none (rules and local fallback only)"
    res["dataset"] = {
        "seed": manifest["seed"],
        "generator_version": manifest["generator_version"],
        "totals": manifest["totals"],
        "sha256": manifest_hash(manifest),
    }
    return res


def leg_data(data: Path) -> tuple[dict[str, Any], int]:
    try:
        manifest, msgs = load(data)
    except (OSError, ValueError, KeyError):
        return {
            "leg": "data",
            "error": f"no dataset at {data}; run: python -m eval.data_gen.chat --out {data}",
        }, EXIT_ENV
    t = manifest["totals"]
    problems: list[str] = []
    if t["messages"] < 1900:
        problems.append(f"{t['messages']} messages < 1900")
    for label, floor in (
        ("abuse", 450),
        ("scam", 200),
        ("fake_upi", 180),
        ("doxxing", 220),
        ("clean", 650),
        ("doxxing_staff", 10),
    ):
        if t["by_label"].get(label, 0) < floor:
            problems.append(f"{label}: {t['by_label'].get(label, 0)} < {floor}")
    if t["staff"] < 40:
        problems.append("fewer than 40 staff messages")
    if len({m.text.strip().lower() for m in msgs}) != len(msgs):
        problems.append("duplicate message texts")
    if len({m.id for m in msgs}) != len(msgs):
        problems.append("duplicate ids")
    forbidden = scan_obj(manifest)
    if forbidden:
        problems.append(f"{len(forbidden)} forbidden (possibly real) patterns")
    return {
        "leg": "data",
        "seed": manifest["seed"],
        "totals": t,
        "problems": problems,
        "sha256": manifest_hash(manifest),
    }, (EXIT_FAIL if problems else EXIT_OK)


def run_heldout(a: argparse.Namespace) -> int:
    """The held-out leg: aggregates only on stdout and in the run directory; exit 0 once scored (it never gates),
    2 when the private set is missing (informational rows then read NOT RUN)."""
    from . import phase4_heldout as h  # noqa: PLC0415

    data = Path(a.heldout_data) if Path(a.heldout_data).is_absolute() else ROOT / a.heldout_data
    out = Path(a.out) if a.out else ROOT / ".tmp" / "phase4"
    out.mkdir(parents=True, exist_ok=True)
    if not (data / "manifest.json").exists():
        print(
            f"ENV ERROR: no held-out set at {data} (private, not in git; ask the eval owner)", file=sys.stderr
        )
        return EXIT_ENV
    try:
        res = h.leg_heldout(data, a.reference)
    except ImportError as exc:
        print(f"ENV ERROR: cannot import the chat modules ({exc.name})", file=sys.stderr)
        return EXIT_ENV
    h.write_private_failures(data, res)
    (out / "phase4-heldout.json").write_text(
        json.dumps(res, indent=1, sort_keys=True, default=str, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    keys = (
        "leg",
        "dataset",
        "abuse",
        "scam",
        "fake_upi",
        "doxxing",
        "clean",
        "staff",
        "holds",
        "latency_ms",
        "log_scan",
    )
    print(json.dumps({k: res[k] for k in keys if k in res}, indent=1, default=str, ensure_ascii=False))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.phase4", description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--leg", choices=("data", "classify", "classify_llm", "youtube", "twitch", "report", "heldout")
    )
    p.add_argument(
        "--heldout",
        action="store_true",
        help="informational: score the blind held-out chat set v2 (same as --leg heldout; never gating)",
    )
    p.add_argument("--heldout-data", default="eval/data/private/chat_heldout_v2")
    p.add_argument("--data", default=DEFAULT_DATA)
    p.add_argument("--out", default="")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--generate", action="store_true")
    p.add_argument(
        "--reference",
        action="store_true",
        help="classify legs: use the stand-in classifier that is right by construction",
    )
    p.add_argument(
        "--defect",
        choices=DEFECTS,
        default="",
        help="with --reference: seed a defect to prove the gate fails",
    )
    p.add_argument("--results", default="")
    p.add_argument("--report-dir", default="")
    p.add_argument("--phase-name", default="phase4")
    a = p.parse_args(argv)
    if a.heldout:
        a.leg = "heldout"
    if not a.leg:
        p.error("one of --leg or --heldout is required")
    if a.leg == "heldout":
        return run_heldout(a)
    data = Path(a.data) if Path(a.data).is_absolute() else ROOT / a.data
    out = Path(a.out) if a.out else ROOT / ".tmp" / "phase4"
    out.mkdir(parents=True, exist_ok=True)
    if a.leg == "report":
        from .phase4_report import build_report  # noqa: PLC0415

        return build_report(
            out,
            Path(a.results) if a.results else None,
            a.phase_name,
            Path(a.report_dir) if a.report_dir else None,
        )
    if a.generate and not (data / "manifest.json").exists():
        build_suite(data, a.seed)
    if a.leg == "data":
        res, rc = leg_data(data)
    else:
        if not (data / "manifest.json").exists():
            print(
                f"ENV ERROR: no dataset at {data} (python -m eval.data_gen.chat --out {data})",
                file=sys.stderr,
            )
            return EXIT_ENV
        try:
            if a.defect and not a.reference:
                print(
                    "ERROR: --defect needs --reference (defects are seeded into the stand-in classifier)",
                    file=sys.stderr,
                )
                return EXIT_ENV
            if a.leg in ("classify", "classify_llm"):
                res = leg_classify(data, a.leg == "classify_llm", a.defect, a.reference)
            else:
                from . import phase4_conn  # noqa: PLC0415

                res = asyncio.run(phase4_conn.run(a.leg, data, a.defect))
        except ImportError as exc:
            print(f"ENV ERROR: cannot import the chat modules ({exc.name})", file=sys.stderr)
            return EXIT_ENV
        rc = EXIT_OK
    (out / f"phase4-{a.leg}.json").write_text(
        json.dumps(res, indent=1, sort_keys=True, default=str, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    keys = (
        "leg",
        "totals",
        "problems",
        "error",
        "abuse",
        "scam",
        "fake_upi",
        "doxxing",
        "clean",
        "staff",
        "latency_ms",
        "log_scan",
        "checks",
    )
    brief = {k: res[k] for k in keys if k in res}
    for sec in ("abuse", "scam", "fake_upi", "doxxing"):
        if sec in brief:
            brief[sec] = {k: v for k, v in brief[sec].items() if k != "missed_ids"}
    if "clean" in brief:
        brief["clean"] = {k: v for k, v in brief["clean"].items() if k != "fp_ids"}
    print(json.dumps(brief, indent=1, default=str, ensure_ascii=False))
    if rc == EXIT_OK and a.leg != "data":
        from .phase4_report import evaluate  # noqa: PLC0415

        failed = [c for c in evaluate(res) if not c.passed]
        for c in failed:
            print(f"FAIL {c.name}: {c.value} {c.op} {c.target}", file=sys.stderr)
        rc = EXIT_FAIL if failed else EXIT_OK
    return rc


if __name__ == "__main__":
    sys.exit(main())
