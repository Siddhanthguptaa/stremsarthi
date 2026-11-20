"""T2.9 acceptance runner: OTP-screen and game-ID/room-credential recall, false alarms and ACCURATE-lane time of
the ``screens`` detector on SPECIMEN scenes.

    python -m vision.accept_screens                         # CPU, 60 OTP + 2x30 game scenes + negatives
    python -m vision.accept_screens --otp 80 --games 40 --json out.json
    python -m vision.accept_screens --ablate no_templates   # the OCR label route alone

Scenes come from ``eval.data_gen.video.suite.build_item`` (T2.1): ``otp_screen`` (phone on a desk: lock screen,
banner, SMS and OTP entry layouts, English / Hinglish / Hindi text, 25 % landscape), ``game_bgmi`` /
``game_freefire`` (SPECIMEN room lobbies; NOT the real games) and the negatives ``neg_screen`` (phone screens
with a six-digit order number but no OTP), ``neg_game`` (loading, inventory and result screens full of numbers).
Levels clean / light / heavy as in the other runners; ``heavy`` (blur, JPEG, glare, up to 40 % occlusion) is
largely unreadable by any OCR, so recall is reported per level and for clean+light together.
Recall: a visible target counts as masked when the union of the detections of its category covers >= 70 % of its
quad. Precision: share of detections that overlap a truth target. Numbers only: no text is printed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import defaultdict
from typing import Any

import numpy as np

from contracts import Category, Config, DetectContext, DetectorConfig, Device, Frame, Lane
from vision.accept_plates import build_item_bgr
from vision.detectors.screens import DETECTOR as SCREENS
from vision.resources import VisionResources
from vision.testing import percentile
from vision.testing_screens import coverage_of

TRUTH_CATEGORY = {
    "otp_banner": Category.OTP_SCREEN,
    "game_player_id": Category.GAME_ID,
    "game_room_id": Category.ROOM_CREDENTIAL,
    "game_room_password": Category.ROOM_CREDENTIAL,
}
POSITIVE = {"otp": ("otp_screen",), "game": ("game_bgmi", "game_freefire")}
NEGATIVE = ("neg_screen", "neg_game")


def build_scenes(n_otp: int, n_game: int, n_neg: int, seed: int) -> list[dict[str, Any]]:
    scenes: list[dict[str, Any]] = []

    def add(bucket: str, i: int) -> None:
        img, truth = build_item_bgr(bucket, i, seed)
        scenes.append({"bucket": bucket, "index": i, "image": img, "truth": truth})

    for i in range(n_otp):
        add("otp_screen", i)
    for bucket in POSITIVE["game"]:
        for i in range(n_game):
            add(bucket, i)
    for bucket in NEGATIVE:
        for i in range(max(1, n_neg // len(NEGATIVE))):
            add(bucket, i)
    return scenes


def _targets(truth: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        t for t in truth["targets"] if t["category"] in TRUTH_CATEGORY and t["visible"] and not t.get("aux")
    ]


def run(
    scenes: list[dict[str, Any]], config: Config | None = None, *, warm: int = 3, ablate: str = "none"
) -> dict[str, Any]:
    cfg = config or Config(profile="dev", _env_file=None)
    cfg.ocr.rec_budget_ms = max(
        cfg.ocr.rec_budget_ms, 5000
    )  # the budget is wall-clock: keep it out of the numbers
    res = VisionResources(cfg)
    det = SCREENS()
    params: dict[str, Any] = {"templates": []} if ablate == "no_templates" else {}
    det.configure(DetectorConfig(categories=cfg.categories, device=Device.CPU, params=params), res)
    det.warmup()
    ctx = DetectContext(lane=Lane.ACCURATE, categories={}, stride=1)
    for i in range(warm):
        det.detect(Frame(-1 - i, -float(i), 0.0, scenes[i % len(scenes)]["image"]), ctx)
    total_ms: list[float] = []
    own_ms: list[float] = []
    recall: dict[str, list[bool]] = defaultdict(list)  # per truth kind
    by_level: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    by_layout: dict[str, list[bool]] = defaultdict(list)
    by_lang: dict[str, list[bool]] = defaultdict(list)
    by_style: dict[str, list[bool]] = defaultdict(list)
    missed: list[str] = []
    dets_n = fp_n = 0
    shapes: dict[str, int] = defaultdict(int)
    sources: dict[str, int] = defaultdict(int)
    neg: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for seq, sc in enumerate(scenes):
        frame = Frame(seq, seq / 4.0, 0.0, sc["image"])
        t0 = time.perf_counter()
        dets = det.detect(frame, ctx)
        total_ms.append((time.perf_counter() - t0) * 1000.0)
        t1 = time.perf_counter()
        det.detect(frame, ctx)
        own_ms.append((time.perf_counter() - t1) * 1000.0)
        by_cat: dict[Category, list[np.ndarray]] = defaultdict(list)
        for d in dets:
            by_cat[d.category].append(np.array(d.quad.pts, np.float32))
            shapes[str(d.extra.get("shape", ""))] += 1 if d.category is Category.OTP_SCREEN else 0
            sources[str(d.extra.get("source", ""))] += 1 if d.category is not Category.OTP_SCREEN else 0
        truths = _targets(sc["truth"])
        meta = sc["truth"]["sprite_meta"]
        for t in truths:
            cat = TRUTH_CATEGORY[t["category"]]
            ok = coverage_of(t["quad"], by_cat[cat]) >= 0.7
            kind = "otp" if cat is Category.OTP_SCREEN else t["category"]
            recall[kind].append(ok)
            by_level[sc["truth"]["level"]]["otp" if cat is Category.OTP_SCREEN else "game"].append(ok)
            if cat is Category.OTP_SCREEN:
                by_layout[str(meta.get("layout"))].append(ok)
                by_lang[str(meta.get("lang"))].append(ok)
            else:
                by_style[str(t.get("style"))].append(ok)
            if not ok:
                missed.append(f"{sc['bucket']}{sc['index']}:{t['category']}/{sc['truth']['level']}")
        if truths:
            for d in dets:
                tc = [t for t in truths if TRUTH_CATEGORY[t["category"]] is d.category]
                dets_n += 1
                if not any(coverage_of(t["quad"], [np.array(d.quad.pts, np.float32)]) >= 0.3 for t in tc):
                    fp_n += 1
        else:
            neg[sc["bucket"]][0] += 1
            neg[sc["bucket"]][1] += 1 if dets else 0
    res.close()

    def rate(vs: list[bool]) -> float:
        return round(sum(vs) / max(1, len(vs)), 3)

    otp_all = recall["otp"]
    game_all = [v for k, vs in recall.items() if k != "otp" for v in vs]
    cl = lambda kind: by_level["clean"][kind] + by_level["light"][kind]  # noqa: E731
    return {
        "ablate": ablate,
        "images": len(scenes),
        "otp_targets": len(otp_all),
        "otp_recall": rate(otp_all),
        "otp_recall_clean_light": rate(cl("otp")),
        "otp_recall_by_level": {lv: rate(by_level[lv]["otp"]) for lv in sorted(by_level)},
        "otp_recall_by_layout": {k: rate(v) for k, v in sorted(by_layout.items())},
        "otp_recall_by_lang": {k: rate(v) for k, v in sorted(by_lang.items())},
        "otp_detection_shapes": dict(shapes),
        "game_targets": len(game_all),
        "game_recall": rate(game_all),
        "game_recall_clean_light": rate(cl("game")),
        "game_recall_by_level": {lv: rate(by_level[lv]["game"]) for lv in sorted(by_level)},
        "game_recall_by_field": {k: rate(v) for k, v in sorted(recall.items()) if k != "otp"},
        "game_recall_by_style": {k: rate(v) for k, v in sorted(by_style.items())},
        "game_detection_sources": dict(sources),
        "missed": missed,
        "detections_on_positive_scenes": dets_n,
        "false_detections_on_positive_scenes": fp_n,
        "precision": round(1.0 - fp_n / max(1, dets_n), 3),
        "negative_frames": sum(v[0] for v in neg.values()),
        "negative_frames_with_a_mask": sum(v[1] for v in neg.values()),
        "negative_by_bucket": {k: {"frames": v[0], "false": v[1]} for k, v in neg.items()},
        "detector_only_ms_median": round(statistics.median(own_ms), 2),
        "detector_only_ms_p95": round(percentile(own_ms, 95), 2),
        "ms_median": round(statistics.median(total_ms), 1),
        "ms_p95": round(percentile(total_ms, 95), 1),
        "ms_max": round(max(total_ms), 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--otp", type=int, default=60)
    ap.add_argument("--games", type=int, default=30, help="scenes per game style")
    ap.add_argument("--negatives", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--ablate", choices=("none", "no_templates"), default="none")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    report = run(build_scenes(args.otp, args.games, args.negatives, args.seed), ablate=args.ablate)
    text = json.dumps(report, indent=2)
    print(text)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(text)


if __name__ == "__main__":
    main()
