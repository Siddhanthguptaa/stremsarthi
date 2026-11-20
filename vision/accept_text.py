"""T2.4 acceptance runner: text PII + signboard recall/precision and the ACCURATE-lane time of ``cards`` + ``text``.

    python -m vision.accept_text                          # CPU, 48 PII + 40 sign + 25 negative SPECIMEN scenes
    python -m vision.accept_text --pii 60 --sign 40 --json out.json

Every scene is synthetic (``vision.testing_text``: fake numbers, SPECIMEN tag): notes and panels carrying a
phone number, PIN code, UPI ID or e-mail (Latin, Devanagari, Tamil and Telugu text and digits), signboards in
Devanagari, Kannada, Tamil, Telugu and Bengali, and hard negatives (order ids, 10-digit numbers starting 1-5,
prices, dates, versions, @mentions, URLs, IPs, plates, a long sentence with a bare 6-digit number, empty desks).
The real OCR (CPU) and the Indic enricher run; detectors are driven directly (one shared OCR pass per frame, so
the time reported for ``cards`` + ``text`` together is the ACCURATE-lane service time without planner/tracker).
Recall: a sensitive substring counts when a same-category detection covers >= 70 % of its box. Signboard text
recall: a drawn text line counts when the union of masks covers >= 70 % of it. Precision: share of PII
detections that overlap a truth item of their category.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import defaultdict
from typing import Any

from contracts import Category, Config, DetectContext, DetectorConfig, Device, Frame, Lane
from vision.detectors.cards import DETECTOR as CARDS
from vision.detectors.text import DETECTOR as TEXT
from vision.resources import VisionResources

from . import testing_text as tt
from .testing import percentile

PII = (Category.PHONE_NUMBER, Category.PIN_CODE, Category.UPI_ID, Category.EMAIL)
RECIPES: list[dict[str, Any]] = [{}, {"blur": 0.9}, {"brightness": 0.75}, {"scale": 0.85}, {"noise": 6.0}]


def build_scenes(n_pii: int, n_sign: int, n_neg: int, seed: int) -> list[tt.TextScene]:
    scenes: list[tt.TextScene] = []
    for i in range(n_pii):
        scenes.append(tt.make_pii_scene(i, seed, **RECIPES[(i // 4) % len(RECIPES)]))
    for i in range(n_sign):
        scenes.append(tt.make_sign_scene(i, seed, **RECIPES[(i // 5) % len(RECIPES)]))
    for i in range(n_neg):
        scenes.append(tt.make_negative_scene(i, seed))
    return scenes


def run(scenes: list[tt.TextScene], config: Config | None = None, *, warm: int = 3) -> dict[str, Any]:
    cfg = config or Config(profile="dev", _env_file=None)
    res = VisionResources(cfg)
    dcfg = DetectorConfig(categories=cfg.categories, device=Device.CPU)
    cards, text = CARDS(), TEXT()
    cards.configure(dcfg, res)
    text.configure(dcfg, res)
    cards.warmup()
    text.warmup()
    ctx = DetectContext(lane=Lane.ACCURATE, categories={}, stride=1)
    for i in range(warm):  # steady state: first ORT runs allocate
        fr = Frame(-1 - i, -float(i), 0.0, scenes[i % len(scenes)].image)
        cards.detect(fr, ctx)
        text.detect(fr, ctx)
    total_ms: list[float] = []
    text_ms: list[float] = []
    outcomes: list[tuple[tt.TextScene, tt.SceneOutcome, list[Any]]] = []
    for seq, sc in enumerate(scenes):
        fr = Frame(seq, seq / 4.0, 0.0, sc.image)
        t0 = time.perf_counter()
        card_dets = cards.detect(fr, ctx)
        t1 = time.perf_counter()
        text_dets = list(text.detect(fr, ctx))
        t2 = time.perf_counter()
        total_ms.append((t2 - t0) * 1000.0)
        text_ms.append((t2 - t1) * 1000.0)
        outcomes.append((sc, tt.score_text_scene(sc, text_dets), [*card_dets, *text_dets]))

    # ---- recall by category and script
    by_cat: dict[str, list[bool]] = defaultdict(list)
    by_script: dict[str, list[bool]] = defaultdict(list)
    masked_any: list[bool] = []
    core: list[
        bool
    ] = []  # everything except Tamil/Telugu numerals (the PP-OCR mobile models read them badly)
    for sc, out, _ in outcomes:
        for item, ok, any_ok in zip(sc.items, out.item_hit, out.item_masked, strict=True):
            native = item.digits == "native"
            by_cat[item.category.value].append(ok)
            by_script[f"{item.category.value}/{item.script}" + ("/native_digits" if native else "")].append(
                ok
            )
            masked_any.append(any_ok)
            if not (native and item.script in ("tamil", "telugu")):
                core.append(ok)
    all_items = [v for vs in by_cat.values() for v in vs]
    missed = [
        f"{sc.kind}{n}:{item.category.value}/{item.script}/{item.digits}"
        for n, (sc, out, _) in enumerate(outcomes)
        for item, ok in zip(sc.items, out.item_hit, strict=True)
        if not ok
    ]
    false_dets = [
        f"{sc.kind}{n}:{d.category.value}:{d.extra.get('reason')}"
        for n, (sc, _, dets) in enumerate(outcomes)
        for d in dets
        if d.category in PII and not tt.is_true_pii(sc, d)
    ]
    # ---- signboard text recall (sign scenes) and any-text recall (all scenes with lines)
    sign_lines: dict[str, list[bool]] = defaultdict(list)
    for sc, out, _ in outcomes:
        if sc.kind == "sign":
            for covered, script in zip(out.line_covered, sc.line_scripts, strict=True):
                sign_lines[script].append(covered)
    all_sign = [v for vs in sign_lines.values() for v in vs]
    # ---- precision
    pii_dets = fp = 0
    neg_frames = fp_frames = 0
    fp_reasons: dict[str, int] = defaultdict(int)
    for sc, out, dets in outcomes:
        n_pii = sum(1 for d in dets if d.category in PII)
        pii_dets += n_pii
        fp += out.false_pii
        if sc.kind == "negative":
            neg_frames += 1
            if any(d.category in PII for d in dets):
                fp_frames += 1
                for d in dets:
                    if d.category in PII:
                        fp_reasons[f"{d.category.value}:{d.extra.get('reason')}"] += 1
    sign_dets = sum(1 for _, _, dets in outcomes for d in dets if d.category is Category.SIGNBOARD)
    false_sign = sum(out.false_sign for _, out, _ in outcomes)

    def rate(vs: list[bool]) -> float:
        return round(sum(vs) / max(1, len(vs)), 3)

    return {
        "images": len(scenes),
        "pii_items": len(all_items),
        "pii_recall": rate(all_items),
        "pii_recall_excluding_tamil_telugu_numerals": rate(core),
        "pii_masked_by_any_category": rate(masked_any),
        "recall_by_category": {k: {"n": len(v), "recall": rate(v)} for k, v in sorted(by_cat.items())},
        "recall_by_category_script": {
            k: {"n": len(v), "recall": rate(v)} for k, v in sorted(by_script.items())
        },
        "missed_items": missed,
        "false_pii_detections": false_dets,
        "pii_precision": round((pii_dets - fp) / max(1, pii_dets), 3),
        "pii_detections": pii_dets,
        "pii_false_detections": fp,
        "hard_negative_frames": neg_frames,
        "hard_negative_frames_with_pii_mask": fp_frames,
        "hard_negative_fp_reasons": dict(fp_reasons),
        "signboard_text_lines": len(all_sign),
        "signboard_text_recall": rate(all_sign),
        "signboard_recall_by_script": {
            k: {"n": len(v), "recall": rate(v)} for k, v in sorted(sign_lines.items())
        },
        "signboard_detections": sign_dets,
        "signboard_false_detections": false_sign,
        "cards_plus_text_ms": {
            "p50": round(statistics.median(total_ms), 1),
            "p95": round(percentile(total_ms, 95), 1),
            "max": round(max(total_ms), 1),
            "n": len(total_ms),
        },
        "text_detector_ms": {
            "p50": round(statistics.median(text_ms), 1),
            "p95": round(percentile(text_ms, 95), 1),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pii", type=int, default=48)
    ap.add_argument("--sign", type=int, default=40)
    ap.add_argument("--neg", type=int, default=25)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    report = run(build_scenes(args.pii, args.sign, args.neg, args.seed))
    print(json.dumps(report, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)


if __name__ == "__main__":
    main()
