"""Gate 2 harness fixes: the still-run default, the allowlisted-face warm-up rule, the neg_text / neg_scene
negatives, the production-budget latency check, seeded defects and the dataset parameter check. CPU, no weights."""

from __future__ import annotations

import json
from types import SimpleNamespace

from contracts import Config
from eval.harness.report import Check
from eval.phase2 import acceptance, offline, scoring, seeded
from eval.phase2.detectors import OK, DetectorSet, DetectorStatus
from eval.phase2.rows import EXPECTED_DETECTORS, ROW_BY_KEY
from eval.phase2.runner import Runner, RunOptions, resolve_fast_still_runs


def cfg() -> Config:
    return Config(profile="dev", _env_file=None)


def trec(item, cat, cov, level="clean", src="img", **kw):
    r = {
        "k": "t", "src": src, "item": item, "bucket": cat, "level": level, "frame": 0, "t": 0.0, "cat": cat,
        "j": 0, "cov": cov, "cov_cat": cov,
    }  # fmt: skip
    r.update(kw)
    return r


# ---------------------------------------------------------------- (1) still runs
def test_fast_still_runs_default_is_the_face_vote_minimum_plus_two_from_the_config():
    c = cfg()
    assert resolve_fast_still_runs(None, c) == c.vision.face_vote_min + 2 == 7
    c.vision.face_vote_min = 3
    assert resolve_fast_still_runs(None, c) == 5
    assert resolve_fast_still_runs(2, c) == 2  # an explicit value wins (and is reported as an override)
    assert RunOptions().fast_still_runs is None
    ds = DetectorSet(c, SimpleNamespace(allowlist=None, close=lambda: None))
    assert Runner(ds, c).fast_still_runs == 5
    assert offline.OfflineOptions(data=None, out=None).fast_still_runs is None  # type: ignore[arg-type]


# ---------------------------------------------------------------- (2) warm-up rule
def allowed_clip(masked_frames: int, n: int = 90, item="faces_enrolled_clip00", j=0, start=0, level="clean"):
    """An allowlisted face track: masked for the first ``masked_frames`` frames after it appears, visible after."""
    return [
        trec(item, "face", 0.95 if k < masked_frames else 0.0, level, "clip", frame=start + k, t=(start + k) / 30, j=j, allow=True)
        for k in range(n)
    ]  # fmt: skip


def test_settled_steady_and_all_frames_views_of_an_allowlisted_face_clip():
    row = ROW_BY_KEY["face_allowed"]
    recs = allowed_clip(masked_frames=25)  # 25 frames = 0.83 s of warm-up masking, 90 frames = 3 s
    st = scoring.row_stats(recs, row)["clip"]
    wu = st["warmup"]
    assert wu["settled"]["gate"]["n"] == 60 and wu["settled"]["gate"]["value"] == 1.0  # frames 30..89
    assert wu["steady"]["gate"]["n"] == 30 and wu["steady"]["gate"]["value"] == 1.0  # frames 60..89
    assert wu["all"]["gate"]["n"] == 90 and abs(wu["all"]["gate"]["value"] - 65 / 90) < 1e-9
    assert st["gate"]["n"] == 60 and st["gate"]["value"] == 1.0  # the gate row IS the settled view
    assert st["all"]["n"] == 90  # the unscoped block still holds every frame
    assert "1 s after the track first appears" in wu["rule"] or "1.0 s" in wu["rule"] or "1 s" in wu["rule"]


def test_a_face_masked_for_longer_than_the_warmup_still_fails_the_settled_row():
    row = ROW_BY_KEY["face_allowed"]
    st = scoring.row_stats(allowed_clip(masked_frames=45), row)["clip"]  # masked for 1.5 s
    assert (
        st["warmup"]["settled"]["gate"]["value"] == 45 / 60
    )  # frames 30..44 (15 of the 60 settled frames) are still masked
    assert st["gate"]["value"] < 0.95
    assert st["warmup"]["steady"]["gate"]["value"] == 1.0


def test_each_track_has_its_own_warmup_and_other_rows_are_not_split():
    row = ROW_BY_KEY["face_allowed"]
    recs = allowed_clip(25, 60, j=0) + allowed_clip(25, 60, j=1, start=60)  # second face appears at t = 2 s
    st = scoring.row_stats(recs, row)["clip"]
    assert st["warmup"]["settled"]["gate"]["n"] == 60  # 30 settled frames per track
    still = scoring.row_stats([trec("a", "face", 0.0, allow=True)], row)["img"]
    assert "warmup" not in still  # stills have no warm-up
    masked = scoring.row_stats([trec("c", "face", 1.0, src="clip", allow=False)], ROW_BY_KEY["face_masked"])
    assert "warmup" not in masked["clip"]  # masked faces are scored on every frame


def test_gate_check_names_the_warmup_rule_and_the_other_two_numbers():
    recs = allowed_clip(25)
    opts = offline.OfflineOptions(data=None, out=None, gate_sources=("clip",), min_n=20)  # type: ignore[arg-type]
    st = {i: DetectorStatus(i, OK, lane="fast") for i in EXPECTED_DETECTORS}
    m = offline.build_measurements(recs, st, {}, {}, {"clip_frames": 0, "detect_errors": {}}, opts)
    c = next(
        x
        for x in offline.analyse_offline(m, opts)
        if x.name == "Allowlisted faces left visible recall [clips]"
    )
    assert c.passed and "warm-up rule" in c.note and "steady" in c.note and "all frames 0.722" in c.note
    assert "warmup_rule" in m["scoring"] and "1 s" in m["scoring"]["warmup_rule"]


# ---------------------------------------------------------------- (3) negatives
def nrec(bucket, bycat, src="img"):
    return {
        "k": "n", "src": src, "item": "x", "bucket": bucket, "level": "clean", "frame": 0, "t": 0.0,
        "masks": sum(bycat.values()), "any": sum(bycat.values()), "bycat": bycat, "protect": False,
    }  # fmt: skip


def test_neg_text_counts_pii_masks_only_and_neg_scene_counts_signboards_as_info():
    recs = [
        nrec("neg_text", {"signboard": 3}),  # by design: not a false mask
        nrec("neg_text", {"signboard": 1, "phone_number": 1}),  # false
        nrec("neg_text", {"pan": 1}),  # false
        nrec("neg_text", {}),
        nrec("neg_scene", {"signboard": 1}),
        nrec("neg_scene", {}),
        nrec("neg_plate", {"plate": 1}),
    ]
    neg = scoring.negative_stats(recs)
    t = neg["neg_text/img"]
    assert (t["n"], t["false"], t["rate"]) == (4, 2, 0.5)
    assert t["info"] == 2 and t["info_cats"] == ["signboard"] and t["counted_cats"][0] == "phone_number"
    s = neg["neg_scene/img"]
    assert (s["n"], s["false"], s["info"], s["info_rate"]) == (2, 1, 1, 0.5)
    assert neg["neg_plate/img"]["false"] == 1  # other buckets keep 'any mask of the detector'


def test_old_records_without_bycat_still_score():
    r = nrec("neg_text", {"signboard": 2})
    r.pop("bycat")
    assert scoring.negative_stats([r])["neg_text/img"]["false"] == 1


# ---------------------------------------------------------------- (4) latency at the production budget
def lane(p95, n=30):
    return {"n": n, "p50_ms": p95 / 2, "p95_ms": p95, "max_ms": p95 * 1.5}


def test_accurate_lane_check_is_judged_on_the_production_budget_pass():
    prod = {"budget_ms": 150, "images": 40, "lanes_ms": {"accurate": lane(300.0)}}
    c = offline._accurate_latency_check(prod)
    assert c.passed and c.target == 450.0 and "150" in c.note and "recall pass only" in c.note
    assert not offline._accurate_latency_check({**prod, "lanes_ms": {"accurate": lane(600.0)}}).passed
    none = offline._accurate_latency_check(None)
    assert none.not_run and not none.passed  # no pass, no number: never a silent pass


def test_measurements_carry_both_latency_views_and_rescore_keeps_them():
    prod = {
        "budget_ms": 150,
        "lifted_budget_ms": 5000,
        "images": 5,
        "lanes_ms": {"accurate": lane(200.0)},
        "rows": {},
    }
    opts = offline.OfflineOptions(data=None, out=None)  # type: ignore[arg-type]
    st = {i: DetectorStatus(i, OK, lane="fast") for i in EXPECTED_DETECTORS}
    m = offline.build_measurements(
        [], st, {"accurate": [900.0, 1000.0]}, {}, {"detect_errors": {}}, opts, None, prod
    )
    assert m["latency"]["production"]["lanes_ms"]["accurate"]["p95_ms"] == 200.0
    assert (
        m["latency"]["lanes_ms"]["accurate"]["p95_ms"] > 900.0
    )  # the lifted pass is the ceiling, reported apart
    assert "RECALL pass" in m["latency"]["note"]


# ---------------------------------------------------------------- (5) seeded defects
class _Det:
    id = "cards"

    def __init__(self):
        self.closed = False

    def detect(self, frame, ctx):
        return ("real",)

    def close(self):
        self.closed = True


def _set():
    c = cfg()
    ds = DetectorSet(c, SimpleNamespace(allowlist=None, close=lambda: None))
    ds.detectors["cards"] = _Det()
    ds.status["cards"] = DetectorStatus("cards", OK, lane="accurate")
    return ds


def test_seeded_defect_kinds():
    assert seeded.parse("blind:cards") == ("blind", "cards")
    for bad in ("cards", "blind:", "oops:cards"):
        try:
            seeded.parse(bad)
        except seeded.SeededDefectError:
            continue
        raise AssertionError(bad)
    ds = _set()
    assert "no detections" in seeded.apply(ds, "blind:cards")
    assert ds.detectors["cards"].detect(None, None) == ()
    ds = _set()
    seeded.apply(ds, "crash:cards")
    try:
        ds.detectors["cards"].detect(None, None)
    except RuntimeError:
        pass
    else:
        raise AssertionError("did not raise")
    ds = _set()
    det = ds.detectors["cards"]
    seeded.apply(ds, "absent:cards")
    assert "cards" not in ds.detectors and ds.status["cards"].state == "missing" and det.closed
    try:
        seeded.apply(_set(), "blind:plates")
    except seeded.SeededDefectError as exc:
        assert "plates" in str(exc)
    else:
        raise AssertionError("an unloaded detector cannot be broken")


def _leg(checks):
    return {
        "passed": all(c.passed for c in checks),
        "checks": [c.__dict__ | {"passed": c.passed} for c in checks],
    }


def _chk(name, value, op=">=", target=0.95, not_run=False):
    return Check(name, value, op, target, not_run=not_run)


def test_acceptance_compare_accepts_a_caught_defect_and_flags_a_missed_one():
    clean = _leg([_chk("Aadhaar card recall [stills]", 0.97), _chk("PAN card recall [stills]", 0.96)])
    caught = _leg([_chk("Aadhaar card recall [stills]", 0.0), _chk("PAN card recall [stills]", 0.0)])
    assert acceptance.compare(clean, caught, "blind:cards") == []
    missed = _leg([_chk("Aadhaar card recall [stills]", 0.96), _chk("PAN card recall [stills]", 0.96)])
    assert acceptance.compare(
        clean, missed, "blind:cards"
    )  # a blind detector that still 'passes' is a broken gate
    phone = "Phone number (on-screen) recall [stills]"
    crashed = _leg(
        [_chk("detector exceptions during the run", 3.0, "==", 0.0), _chk(phone, None, not_run=True)]
    )
    clean_ok = _leg(
        [_chk("detector exceptions during the run", 0.0, "==", 0.0), _chk(phone, 0.95, target=0.9)]
    )
    assert acceptance.compare(clean_ok, crashed, "crash:text") == []
    assert acceptance.compare(clean_ok, clean_ok, "crash:text")
    absent = _leg(
        [
            _chk("Number plate recall [stills]", None, not_run=True),
            _chk("all expected detectors loaded and ran", 5.0, "==", 6.0),
        ]
    )
    clean_p = _leg(
        [
            _chk("Number plate recall [stills]", 0.9, target=0.85),
            _chk("all expected detectors loaded and ran", 6.0, "==", 6.0),
        ]
    )
    assert acceptance.compare(clean_p, absent, "absent:plates") == []


# ---------------------------------------------------------------- dataset parameters
def test_dataset_matches_checks_the_face_clip_overrides(tmp_path):
    from eval.data_gen.video import suite

    (tmp_path / "f.png").write_bytes(b"")
    (tmp_path / "v.mp4").write_bytes(b"")
    clips = {
        "per_bucket": 4,
        "seconds": 5.0,
        "items": [{"video": "v.mp4"}],
        "by_bucket": {"faces_enrolled": {"clips": 6, "seconds": 10.0}},
    }
    m = {
        "generator_version": suite.GENERATOR_VERSION, "seed": 1, "n_per_bucket": 2,
        "buckets": {b: {} for b in suite.BUCKETS},
        "items": [{"id": f"{b}_{i}", "file": "f.png"} for b in suite.BUCKETS for i in range(2)],
        "clips": clips,
    }  # fmt: skip
    (tmp_path / "manifest.json").write_text(json.dumps(m))
    assert offline.dataset_matches(tmp_path, 1, 2, 4, 6, 10.0)[0] is True
    assert offline.dataset_matches(tmp_path, 1, 2, 4, 6, 5.0)[0] is False
    assert offline.dataset_matches(tmp_path, 1, 2, 4, 4, 10.0)[0] is False
    ok, why = offline.dataset_matches(tmp_path, 1, 2, 4)  # no override expected but one is there
    assert not ok and "faces_enrolled" in why


def test_neg_scene_is_a_registered_bucket_with_no_targets():
    from eval.data_gen.video import suite
    from eval.phase2.rows import NEGATIVE_BUCKETS

    assert "neg_scene" in suite.BUCKETS and NEGATIVE_BUCKETS["neg_scene"] == "text"
    for i in (0, 1):
        img, truth = suite.build_item("neg_scene", i, 1234)
        assert img.shape == (720, 1280, 3)
        assert truth["targets"] == [] and truth["n_visible_targets"] == 0
        assert [d["category"] for d in truth["distractors"]] == ["non_text_scene"]


# ---------------------------------------------------------------- D-48 gate scope
def test_default_gate_levels_are_clean_and_light_and_heavy_is_a_stress_row():
    from eval.phase2 import report
    from eval.phase2.rows import GATE_LEVELS

    assert GATE_LEVELS == ("clean", "light")
    recs = [trec(f"a{i}", "aadhaar", 1.0, "clean") for i in range(12)] + [
        trec(f"h{i}", "aadhaar", 0.0, "heavy") for i in range(12)
    ]
    opts = offline.OfflineOptions(data=None, out=None, min_n=5)  # type: ignore[arg-type]
    assert opts.gate_levels == GATE_LEVELS
    st = {i: DetectorStatus(i, OK, lane="fast") for i in EXPECTED_DETECTORS}
    m = offline.build_measurements(recs, st, {}, {}, {"clip_frames": 0, "detect_errors": {}}, opts)
    c = next(x for x in offline.analyse_offline(m, opts) if x.name == "Aadhaar card recall [stills]")
    assert c.passed and c.value == 1.0  # the heavy misses do not gate
    tables, _data, _notes = report.offline_tables({"measurements": m, "checks": []})
    stress = next(t for t in tables if t.title.startswith("Stress levels (heavy)"))
    assert "NOT gated" in stress.title
    assert ["Aadhaar card", "stills", "heavy", 12, 0.0, 0.95, "no"] in stress.rows
    # --gate-levels clean,light,heavy gates heavy again
    full = offline.OfflineOptions(data=None, out=None, min_n=5, gate_levels=("clean", "light", "heavy"))  # type: ignore[arg-type]
    m2 = offline.build_measurements(recs, st, {}, {}, {"clip_frames": 0, "detect_errors": {}}, full)
    c2 = next(x for x in offline.analyse_offline(m2, full) if x.name == "Aadhaar card recall [stills]")
    assert not c2.passed and c2.value == 0.5


def test_report_banner_states_the_gate_scope(tmp_path):
    from eval.phase2 import report

    recs = [trec(f"a{i}", "aadhaar", 1.0) for i in range(6)]
    opts = offline.OfflineOptions(data=tmp_path, out=tmp_path, min_n=5)
    st = {i: DetectorStatus(i, OK, lane="fast") for i in EXPECTED_DETECTORS}
    m = offline.build_measurements(recs, st, {}, {}, {"clip_frames": 0, "detect_errors": {}}, opts)
    offline._write_leg(opts, offline.analyse_offline(m, opts), m, "")
    rep = report.build_report(tmp_path, None, "2026-01-01", baseline_path=tmp_path / "none.json")
    assert rep.banner == "Gate scope: clean+light (D-48); heavy-level recall is reported but not gated"
    from eval.harness.report import to_markdown

    assert to_markdown(rep).splitlines()[4] == f"**{rep.banner}**"
