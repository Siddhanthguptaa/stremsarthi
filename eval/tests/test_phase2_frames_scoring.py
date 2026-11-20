"""Gate 2 scoring: one frame's masks against truth (frames.py), the nearest-result timeline and the pure
aggregation (scoring.py), on toy data with exactly known answers. CPU only, no models."""

from __future__ import annotations

import pytest

from eval.harness import metrics
from eval.phase2 import frames, scoring
from eval.phase2.rows import ROW_BY_KEY
from eval.phase2.timeline import LaneRun, masks_for_frame

Q = ((100.0, 100.0), (300.0, 100.0), (300.0, 200.0), (100.0, 200.0))  # 200 x 100 target


def tgt(cat="aadhaar", quad=Q, **kw):
    return {"category": cat, "quad": [list(p) for p in quad], "visible": True, "aux": False, "j": 0, **kw}


def ft(targets=(), distractors=(), context=()):
    return {"targets": list(targets), "distractors": list(distractors), "context": list(context)}


def box(x1, y1, x2, y2, cat="aadhaar", det="cards"):
    return frames.Mask(cat, ((x1, y1), (x2, y1), (x2, y2), (x1, y2)), det)


def score(truth, masks, **kw):
    base = dict(src="img", item="it0", bucket="aadhaar", level="clean")
    base.update(kw)
    return frames.score_frame(truth, masks, **base)


# ---------------------------------------------------------------- score_frame
def test_full_cover_and_no_cover():
    full = score(ft([tgt()]), [box(90, 90, 310, 210)])
    assert [r["k"] for r in full if r["k"] == "t"] == ["t"] and full[0]["cov"] == 1.0
    none = score(ft([tgt()]), [])
    assert none[0]["cov"] == 0.0 and none[0]["cov_cat"] == 0.0


def test_partial_cover_is_the_exact_area_fraction():
    half = score(ft([tgt()]), [box(100, 100, 200, 200)])  # left half of the quad
    assert half[0]["cov"] == pytest.approx(0.5, abs=0.02)


def test_category_attributed_cover_ignores_masks_of_other_categories():
    recs = score(ft([tgt("aadhaar")]), [box(90, 90, 310, 210, cat="pan")])
    t = recs[0]
    assert t["cov"] == 1.0 and t["cov_cat"] == 0.0  # hidden, but not by the aadhaar mask


def test_aux_invisible_and_unknown_targets_are_not_scored():
    truth = ft(
        [
            tgt(aux=True),
            tgt(visible=False),
            tgt("card_non_pii"),
            tgt("aadhaar_number"),
        ]
    )
    assert [r for r in score(truth, []) if r["k"] == "t"] == []


def test_protect_covers_everything_and_is_flagged_on_negatives():
    recs = score(ft([tgt()]), [], protect=True, bucket="neg_card")
    assert recs[0]["cov"] == 1.0
    neg = next(r for r in recs if r["k"] == "n")
    assert neg["protect"] is True and neg["masks"] == 0


def test_face_allow_flag_is_carried():
    truth = ft([tgt("face", allowlisted=True), tgt("face", allowlisted=False, j=1)])
    recs = [r for r in score(truth, [], bucket="faces_enrolled") if r["k"] == "t"]
    assert [r["allow"] for r in recs] == [True, False]


def test_distractor_cover_and_negative_bucket_counts_only_the_detector_under_test():
    truth = ft([], [{"category": "non_plate_text", "quad": [list(p) for p in Q], "visible": True, "j": 0}])
    recs = score(
        truth,
        [box(100, 100, 300, 200, cat="plate", det="plates"), box(0, 0, 10, 10, "pan", "cards")],
        bucket="neg_plate",
    )
    d = next(r for r in recs if r["k"] == "d")
    n = next(r for r in recs if r["k"] == "n")
    assert d["cov"] == 1.0
    assert n["masks"] == 1 and n["any"] == 2  # neg_plate tests the plates detector only


def test_mask_precision_records_inside_and_iou():
    truth = ft([tgt()])
    recs = score(truth, [box(100, 100, 300, 200), box(500, 500, 600, 600)])
    inside = [r["inside"] for r in recs if r["k"] == "m"]
    iou = [r["iou"] for r in recs if r["k"] == "m"]
    assert inside == [1.0, 0.0] and iou == [1.0, 0.0]
    assert [r["jt"] for r in recs if r["k"] == "m"] == [1, 1]


def test_signboard_mask_all_is_justified_by_the_board_context():
    board = {
        "category": "signboard_board",
        "quad": [[0, 0], [600, 0], [600, 400], [0, 400]],
        "visible": True,
        "j": 0,
    }
    line = tgt("signboard_text", quad=((50.0, 50.0), (150.0, 50.0), (150.0, 90.0), (50.0, 90.0)))
    recs = score(
        ft([line], context=[board]),
        [box(0, 0, 600, 400, cat="signboard", det="text")],
        bucket="signboard_latin",
    )
    assert next(r for r in recs if r["k"] == "m")["inside"] == 1.0


def test_masks_from_requests_keeps_region_masks_only():
    from contracts import Category, MaskKind, MaskRequest, MaskStyle, Quad

    reg = MaskRequest(
        "a",
        "cards",
        Category.AADHAAR,
        MaskKind.REGION,
        (Quad.from_xyxy(1, 2, 30, 40),),
        0.0,
        0.0,
        1.0,
        MaskStyle.BLUR,
        0.9,
        "x",
        0.0,
    )
    full = MaskRequest(
        "b", "hold", Category.HOLD, MaskKind.FULL_FRAME, (), 0.0, 0.0, 1.0, MaskStyle.SOLID, 1.0, "x", 0.0
    )
    ms = frames.masks_from_requests([reg, full])
    assert len(ms) == 1 and ms[0].category == "aadhaar" and ms[0].detector == "cards"


# ---------------------------------------------------------------- timeline
def run(seq, masks=(), ok=True, valid=()):
    return LaneRun(seq, seq / 30.0, ok, tuple(masks), tuple(valid))


def test_nearest_result_within_max_age_paints_the_frame():
    m_a, m_b = box(0, 0, 10, 10), box(20, 20, 30, 30)
    runs = {"fast": [run(0, [m_a]), run(3, [m_b])]}
    masks, protect = masks_for_frame(runs, 2, 2 / 30, {"fast": 6})
    assert not protect and masks == [m_b]  # frame 2 is nearer to the run at 3 than to the run at 0


def test_lane_without_a_usable_result_protects_the_frame():
    runs = {"fast": [run(0, [box(0, 0, 5, 5)])], "accurate": [run(0, ok=False)]}
    masks, protect = masks_for_frame(runs, 1, 1 / 30, {"fast": 6, "accurate": 12})
    assert protect  # the accurate lane failed: the engine would slate
    far, protect2 = masks_for_frame({"fast": [run(0, [box(0, 0, 5, 5)])]}, 20, 20 / 30, {"fast": 6})
    assert protect2 and far == []


def test_older_result_still_paints_while_its_request_is_valid():
    old = run(3, [box(0, 0, 5, 5)], valid=[(0.0, 1.0)])
    new = run(9, [box(50, 50, 60, 60)], valid=[(0.3, 0.7)])
    masks, protect = masks_for_frame({"accurate": [old, new]}, 8, 8 / 30, {"accurate": 12})
    assert not protect and len(masks) == 2  # nearest (9) plus the older one whose window covers pts 0.267


# ---------------------------------------------------------------- scoring
def trec(item, cat, cov, level="clean", src="img", **kw):
    r = {
        "k": "t",
        "src": src,
        "item": item,
        "bucket": cat,
        "level": level,
        "frame": 0,
        "t": 0.0,
        "cat": cat,
        "j": 0,
        "cov": cov,
        "cov_cat": cov,
    }
    r.update(kw)
    return r


def test_recall_per_level_with_wilson_interval():
    recs = [trec(f"a{i}", "aadhaar", 1.0 if i < 9 else 0.0, "clean") for i in range(10)]
    recs += [trec(f"h{i}", "aadhaar", 0.0, "heavy") for i in range(5)]
    st = scoring.row_stats(recs, ROW_BY_KEY["aadhaar"], gate_levels=("clean", "light", "heavy"))
    img = st["img"]
    assert img["all"]["k"] == 9 and img["all"]["n"] == 15
    assert img["levels"]["clean"]["value"] == 0.9 and img["levels"]["heavy"]["value"] == 0.0
    lo, hi = metrics.wilson_interval(9, 15)
    assert img["all"]["ci_lo"] == pytest.approx(lo, abs=1e-4) and img["all"]["ci_hi"] == pytest.approx(
        hi, abs=1e-4
    )
    assert st["img"]["missed"] == sorted({f"h{i}" for i in range(5)} | {"a9"})


def test_gate_levels_restrict_the_gated_block_but_not_the_reported_levels():
    recs = [trec("c", "plate", 1.0, "clean"), trec("h", "plate", 0.0, "heavy")]
    st = scoring.row_stats(recs, ROW_BY_KEY["plate"], gate_levels=("clean",))
    assert st["img"]["gate"]["n"] == 1 and st["img"]["gate"]["value"] == 1.0
    assert st["img"]["levels"]["heavy"]["n"] == 1


def test_face_rows_split_on_the_allowlist_and_use_opposite_success_rules():
    recs = [
        trec("s1", "face", 1.0, allow=False),
        trec("s2", "face", 0.5, allow=False),
        trec("e1", "face", 0.0, allow=True),
        trec("e2", "face", 0.6, allow=True),
    ]
    masked = scoring.row_stats(recs, ROW_BY_KEY["face_masked"])["img"]["all"]
    allowed = scoring.row_stats(recs, ROW_BY_KEY["face_allowed"])["img"]["all"]
    assert (masked["k"], masked["n"]) == (1, 2)  # only full cover masks a stranger
    assert (allowed["k"], allowed["n"]) == (1, 2)  # an allowlisted face must stay under 10 % covered


def test_cover_threshold_is_a_parameter():
    recs = [trec("x", "upi_qr", 0.85)]
    assert scoring.row_stats(recs, ROW_BY_KEY["upi_qr"], cover_min=0.9)["img"]["all"]["k"] == 0
    assert scoring.row_stats(recs, ROW_BY_KEY["upi_qr"], cover_min=0.8)["img"]["all"]["k"] == 1


def test_sources_are_kept_apart():
    recs = [trec("i", "pan", 1.0), trec("c", "pan", 0.0, src="clip"), trec("c", "pan", 1.0, src="clip")]
    st = scoring.row_stats(recs, ROW_BY_KEY["pan"])
    assert st["img"]["all"]["n"] == 1 and st["clip"]["all"]["n"] == 2 and st["clip"]["all"]["value"] == 0.5


def test_by_attr_groups_signboard_scripts():
    recs = [
        trec("s1", "signboard_text", 1.0, attr={"script": "tamil"}),
        trec("s2", "signboard_text", 0.0, attr={"script": "kannada"}),
    ]
    attrs = scoring.row_stats(recs, ROW_BY_KEY["signboard"])["img"]["attrs"]["script"]
    assert attrs["tamil"]["value"] == 1.0 and attrs["kannada"]["value"] == 0.0


def test_precision_distractors_and_negatives():
    recs = [
        {"k": "m", "src": "img", "cat": "aadhaar", "inside": 1.0, "iou": 0.9, "jt": 1},
        {"k": "m", "src": "img", "cat": "aadhaar", "inside": 0.4, "iou": 0.2, "jt": 1},
        {"k": "m", "src": "img", "cat": "pan", "inside": 0.0, "iou": 0.0, "jt": 0},
        {"k": "m", "src": "clip", "cat": "aadhaar", "inside": 1.0, "iou": 1.0, "jt": 1},
        {"k": "m", "src": "img", "cat": "risky_text", "inside": 0.0, "iou": 0.0, "jt": 0},
        {"k": "d", "src": "img", "cat": "x", "cov": 0.9},
        {"k": "d", "src": "img", "cat": "x", "cov": 0.1},
        {"k": "n", "src": "img", "bucket": "neg_card", "masks": 1, "any": 1, "protect": False},
        {"k": "n", "src": "img", "bucket": "neg_card", "masks": 0, "any": 0, "protect": False},
    ]
    p = scoring.precision_stats(recs)
    a = p["by_category"]["aadhaar/img"]
    assert a["precision_inside"]["value"] == 0.5 and a["stray"] == 0 and p["masks_of_other_categories"] == 1
    assert (
        p["by_category"]["aadhaar/clip"]["precision_iou50"]["value"] == 1.0
    )  # clips are kept apart from stills
    assert p["by_category"]["pan/img"]["stray"] == 1 and p["by_category"]["pan/img"]["stray_rate"] == 1.0
    assert p["all"]["img"]["masks"] == 3 and p["all"]["img"]["stray"] == 1
    assert scoring.distractor_stats(recs)["all"]["k"] == 1
    neg = scoring.negative_stats(recs)["neg_card/img"]
    assert neg["n"] == 2 and neg["false"] == 1 and neg["rate"] == 0.5


def flick_records(pattern, item="c0", level="clean"):
    return [
        trec(item, "aadhaar", 1.0 if on else 0.0, level, src="clip", frame=i, motion="static")
        for i, on in enumerate(pattern)
    ]


def test_flicker_counts_on_off_on_inside_one_second():
    steady = [1] * 60
    blink = [1] * 10 + [0] * 3 + [1] * 17 + [1] * 30  # on, off, on inside window 0
    st = scoring.flicker_stats(flick_records(steady) + flick_records(blink, "c1"))
    assert st["windows"] == 4 and st["flickering"] == 1 and st["fraction"] == 0.25
    assert st["static_clips"] == 2 and st["flickering_ids"] == ["c1#0"]


def test_flicker_ignores_moving_clips_allowlisted_faces_and_never_masked_targets():
    moving = [dict(r, motion="drift") for r in flick_records([1, 0, 1] * 10)]
    assert scoring.flicker_stats(moving)["windows"] == 0
    off = flick_records([0] * 60)  # never masked: a recall problem, not flicker
    assert scoring.flicker_stats(off)["flickering"] == 0
    allow = [
        trec("f", "face", 1.0 if i % 2 else 0.0, src="clip", frame=i, motion="static", allow=True)
        for i in range(30)
    ]
    assert scoring.flicker_stats(allow)["windows"] == 0


def test_flicker_window_needs_the_target_visible_on_every_frame():
    recs = [
        r for r in flick_records([1, 0, 1] * 10) if r["frame"] != 4
    ]  # one frame not visible: window skipped
    assert scoring.flicker_stats(recs)["windows"] == 0


def test_lane_latency_marks_the_budget():
    t = scoring.lane_latency({"fast": [10.0] * 19 + [30.0], "accurate": [100.0, 200.0, 900.0]})
    assert t["fast"]["budget_ms"] == 25.0 and t["fast"]["within_budget"] is True
    assert t["accurate"]["within_budget"] is False and t["accurate"]["n"] == 3
