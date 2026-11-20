"""Precision / recall / latency harness for the cascade on the SPECIMEN synthetic set.

    python -m chat.classify.bench            # rules-only (no API key needed)

Rules-only means no LLM: borderline messages are finalised locally as HOLD_FOR_MOD, so the numbers are
what the streamer gets with no Groq key. Nothing here prints message text.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from collections.abc import Sequence
from typing import Any

from chat.testing.synthetic_chat import PRIVATE_PHONES, PRIVATE_TERMS, Sample, build_set
from contracts import Category, ChatAction, ChatMessage, ChatVerdict
from contracts.config import ChatConfig, StreamerPrivateConfig

from .cascade import ChatClassifier

GROUPS: dict[str, frozenset[Category]] = {
    "abuse": frozenset({Category.CHAT_ABUSE}),
    "scam": frozenset({Category.CHAT_SCAM_LINK, Category.CHAT_FAKE_UPI}),
    "fake_upi": frozenset({Category.CHAT_FAKE_UPI}),
    "doxxing": frozenset({Category.CHAT_DOXXING}),
}


def make_messages(samples: Sequence[Sample]) -> list[ChatMessage]:
    return [
        ChatMessage("youtube", f"m{i}", "chan", f"author{i}", "viewer", s.text, 1_700_000_000.0)
        for i, s in enumerate(samples)
    ]


def bench_config() -> ChatConfig:
    return ChatConfig(
        streamer_private=StreamerPrivateConfig(terms=list(PRIVATE_TERMS), phones=list(PRIVATE_PHONES))
    )


def _flagged(v: ChatVerdict, group: frozenset[Category]) -> bool:
    return v.action is not ChatAction.ALLOW and any(c in group for c in v.categories)


def percentile(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))] if s else 0.0


def score(samples: Sequence[Sample], verdicts: Sequence[ChatVerdict]) -> dict[str, Any]:
    report: dict[str, Any] = {"n": len(samples)}
    clean = [(s, v) for s, v in zip(samples, verdicts, strict=True) if s.label == "clean"]
    report["clean_n"] = len(clean)
    report["clean_false_positive"] = sum(v.action is not ChatAction.ALLOW for _, v in clean) / max(
        1, len(clean)
    )
    for label, group in GROUPS.items():
        positives = [(s, v) for s, v in zip(samples, verdicts, strict=True) if s.label == label]
        if label == "scam":  # scam family: a fake-UPI hit on a scam line still counts as caught
            pass
        tp = sum(_flagged(v, group) for _, v in positives)
        predicted = [(s, v) for s, v in zip(samples, verdicts, strict=True) if _flagged(v, group)]
        if label == "scam":
            tp_pred = sum(s.label in ("scam", "fake_upi") for s, _ in predicted)
        elif label == "fake_upi":
            tp_pred = sum(s.label == "fake_upi" for s, _ in predicted)
        else:
            tp_pred = sum(s.label == label for s, _ in predicted)
        report[label] = {
            "n": len(positives),
            "recall": tp / max(1, len(positives)),
            "precision": tp_pred / max(1, len(predicted)),
        }
    by_kind: dict[str, list[bool]] = {}
    for s, v in zip(samples, verdicts, strict=True):
        if s.label != "clean":
            by_kind.setdefault(s.kind, []).append(v.action is not ChatAction.ALLOW)
    report["recall_by_kind"] = {k: round(sum(x) / len(x), 3) for k, x in sorted(by_kind.items())}
    report["delete_or_hide_actions"] = {a.value: sum(v.action is a for v in verdicts) for a in ChatAction}
    return report


async def run(seed: int = 4242) -> dict[str, Any]:
    samples = build_set(seed)
    msgs = make_messages(samples)
    clf = ChatClassifier(bench_config())  # no LLM transport: local rules fallback
    # latency: fast path per message, then in busy-chat batches of 20
    per_msg: list[float] = []
    verdicts: list[ChatVerdict] = []
    for m in msgs:
        t0 = time.perf_counter()
        verdicts.append(clf.classify_fast(m).verdict)
        per_msg.append((time.perf_counter() - t0) * 1000.0)
    batch_ms: list[float] = []
    for i in range(0, len(msgs), 20):
        t0 = time.perf_counter()
        await clf.classify(msgs[i : i + 20])
        batch_ms.append((time.perf_counter() - t0) * 1000.0)
    report = score(samples, verdicts)
    report["latency_ms"] = {
        "p50": round(statistics.median(per_msg), 3),
        "p95": round(percentile(per_msg, 0.95), 3),
        "p99": round(percentile(per_msg, 0.99), 3),
        "max": round(max(per_msg), 3),
        "batch20_p95": round(percentile(batch_ms, 0.95), 3),
    }
    await clf.aclose()
    return report


if __name__ == "__main__":
    import json

    print(json.dumps(asyncio.run(run()), indent=2))
