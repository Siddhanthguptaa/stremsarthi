"""Gate 2 offline leg: detector loading (missing / broken / unavailable are NOT RUN), the runner with oracle and
silent fake detectors on a tiny hand-made dataset, and the checks built from the measurements. CPU, no model weights.
"""

from __future__ import annotations

import json
import textwrap
from types import SimpleNamespace
from typing import ClassVar

import cv2
import numpy as np
import pytest

from contracts import Category, Config, Detection, Device, Lane, Quad
from eval.phase2 import dataset, offline
from eval.phase2.detectors import (
    BROKEN,
    ERROR,
    MISSING,
    OK,
    UNAVAILABLE,
    DetectorSet,
    DetectorStatus,
    discover_each,
    load_detectors,
)
from eval.phase2.rows import EXPECTED_DETECTORS, ROW_BY_KEY
from eval.phase2.runner import Runner, RunOptions

CARD = (400.0, 200.0, 800.0, 400.0)  # x1 y1 x2 y2 of the synthetic card


def cfg() -> Config:
    return Config(profile="dev", _env_file=None)


# ---------------------------------------------------------------- loading
def write_pkg(root, name="fakedets"):
    pkg = root / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text('"""fake detectors"""\n')
    body = textwrap.dedent(
        """
        from contracts import Category, Device, Lane

        class {cls}:
            id = "{id}"
            lane = Lane.FAST
            categories = frozenset({{Category.AADHAAR}})
            device = Device.CPU
            vram_mb = 0
            def configure(self, cfg, res):
                {configure}
            def warmup(self): pass
            def detect(self, frame, ctx): return ()
            def close(self): pass

        DETECTOR = {cls}
        """
    )
    for sub, cls, configure in (
        ("good", "Good", "pass"),
        ("noweights", "NoWeights", "raise FileNotFoundError('w')"),
    ):
        d = pkg / sub
        d.mkdir()
        (d / "__init__.py").write_text(body.format(cls=cls, id=sub, configure=configure))
    bad = pkg / "broken"
    bad.mkdir()
    (bad / "__init__.py").write_text("raise ImportError('boom')\n")
    return name


def test_each_package_is_discovered_on_its_own(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    name = write_pkg(tmp_path)
    classes, bad = discover_each(name)
    assert set(classes) == {"good", "noweights"}  # one broken package does not hide the others
    assert bad["broken"].state == BROKEN and bad["broken"].detail == "RegistryError"


def test_load_detectors_states_and_no_exception_text(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    name = write_pkg(tmp_path)
    res = SimpleNamespace(allowlist=None, close=lambda: None)
    ds = load_detectors(
        cfg(), expected=("good", "noweights", "broken", "absent"), resources=res, package=name
    )
    st = {i: s.state for i, s in ds.status.items()}
    assert st == {"good": OK, "noweights": UNAVAILABLE, "broken": BROKEN, "absent": MISSING}
    assert ds.status["noweights"].detail == "FileNotFoundError"  # the type only, never the message
    assert set(ds.detectors) == {"good"}
    assert ds.status["absent"].reason().startswith("missing")


def test_a_missing_package_is_an_empty_registry_not_a_crash():
    assert discover_each("no_such_detector_package_zz") == ({}, {})


# ---------------------------------------------------------------- tiny dataset + fake detectors
class Oracle:
    """Reports the synthetic card on every frame, like a perfect cards detector."""

    id: ClassVar[str] = "cards"
    lane: ClassVar[Lane] = Lane.ACCURATE
    categories: ClassVar[frozenset[Category]] = frozenset({Category.AADHAAR})
    device: ClassVar[Device] = Device.CPU
    vram_mb: ClassVar[int] = 0
    quads: ClassVar[tuple[float, float, float, float]] = CARD

    def configure(self, cfg, res): ...
    def warmup(self): ...
    def close(self): ...

    def detect(self, frame, ctx):
        return (
            Detection(
                "cards", Category.AADHAAR, Quad.from_xyxy(*self.quads), 0.95, frame.seq, frame.pts, 0.0
            ),
        )


class Silent(Oracle):
    def detect(self, frame, ctx):
        return ()


class Exploding(Oracle):
    def detect(self, frame, ctx):
        raise RuntimeError("secret ocr text 1234 5678 9012")


def quad_pts(box):
    x1, y1, x2, y2 = box
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def frame_truth(j_visible=True):
    return {
        "targets": [
            {"category": "aadhaar", "quad": quad_pts(CARD), "visible": j_visible, "aux": False, "j": 0}
        ],
        "distractors": [],
        "context": [],
    }


def make_image(path):
    img = np.full((720, 1280, 3), 40, np.uint8)
    cv2.rectangle(img, (400, 200), (800, 400), (230, 230, 230), -1)
    assert cv2.imwrite(str(path), img)


def make_set(det_cls, status="ok"):
    c = cfg()
    det = det_cls()
    ds = DetectorSet(
        c,
        SimpleNamespace(allowlist=None, close=lambda: None),
        detectors={"cards": det},
        status={"cards": DetectorStatus("cards", status, lane="accurate", categories=("aadhaar",))},
    )
    return c, ds


def image_item(tmp_path, name="aadhaar_0000", level="clean"):
    p = tmp_path / f"{name}.png"
    make_image(p)
    truth = frame_truth() | {"level": level}
    return dataset.ImageItem(name, "aadhaar", level, p, truth)


def test_oracle_detector_is_fully_recalled_on_a_still(tmp_path):
    c, ds = make_set(Oracle)
    recs = Runner(ds, c, RunOptions(fast_still_runs=2)).run_image(image_item(tmp_path))
    t = next(r for r in recs if r["k"] == "t")
    assert t["cov"] == 1.0 and t["cov_cat"] == 1.0 and t["cat"] == "aadhaar"
    m = next(r for r in recs if r["k"] == "m")
    assert (
        m["cat"] == "aadhaar" and m["det"] == "cards" and m["inside"] >= 0.5
    )  # padded mask, mostly on the card


def test_silent_detector_misses_everything(tmp_path):
    c, ds = make_set(Silent)
    recs = Runner(ds, c).run_image(image_item(tmp_path))
    assert next(r for r in recs if r["k"] == "t")["cov"] == 0.0


def test_a_detector_exception_slates_the_frame_and_is_counted_without_its_text(tmp_path):
    c, ds = make_set(Exploding)
    r = Runner(ds, c)
    recs = r.run_image(image_item(tmp_path))
    assert (
        r.stats.detect_errors["cards"] == 1 and r.stats.failed_lane_runs == 1 and r.stats.protect_frames == 1
    )
    assert next(x for x in recs if x["k"] == "t")["cov"] == 1.0  # slated: nothing leaks, but it is reported
    assert ds.status["cards"].detail == "RuntimeError"
    assert "1234" not in json.dumps([ds.summary(), recs])


def test_every_call_raising_marks_the_detector_error_so_its_rows_are_not_run(tmp_path):
    c, ds = make_set(Exploding)
    mp = tmp_path / "manifest.json"
    item = image_item(tmp_path)
    manifest = {
        "name": "t",
        "items": [{"id": item.id, "bucket": "aadhaar", "file": item.path.name, "truth": item.truth}],
    }
    mp.write_text(json.dumps(manifest))
    Runner(ds, c).run(dataset.load(tmp_path))
    assert ds.status["cards"].state == ERROR


def write_clip(path, n=45):
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (1280, 720))
    if not vw.isOpened():
        pytest.skip("no mp4 encoder in this OpenCV build")
    img = np.full((720, 1280, 3), 40, np.uint8)
    cv2.rectangle(img, (400, 200), (800, 400), (230, 230, 230), -1)
    for _ in range(n):
        vw.write(img)
    vw.release()


def test_clip_with_the_oracle_is_fully_masked_every_frame_without_flicker(tmp_path):
    p = tmp_path / "aadhaar_clip00.mp4"
    write_clip(p, 45)
    ft = frame_truth()
    frames = [dict(ft, frame=k, t=k / 30) for k in range(45)]
    clip = dataset.ClipItem("aadhaar_clip00", "aadhaar", "clean", "static", 45, p, frames)
    c, ds = make_set(Oracle)
    r = Runner(ds, c)
    recs = r.run_clip(clip)
    ts = [x for x in recs if x["k"] == "t"]
    assert len(ts) == 45 and all(x["cov"] >= 0.99 for x in ts) and all(x["motion"] == "static" for x in ts)
    assert r.stats.protect_frames == 0 and r.stats.clip_frames == 45
    from eval.phase2 import scoring

    fl = scoring.flicker_stats(recs)
    assert fl["windows"] == 1 and fl["flickering"] == 0


def test_clip_with_a_silent_detector_is_never_masked_but_the_lane_still_covers_the_frame(tmp_path):
    p = tmp_path / "c.mp4"
    write_clip(p, 30)
    clip = dataset.ClipItem(
        "c", "aadhaar", "clean", "static", 30, p, [dict(frame_truth(), frame=k, t=k / 30) for k in range(30)]
    )
    c, ds = make_set(Silent)
    r = Runner(ds, c)
    recs = r.run_clip(clip)
    assert all(x["cov"] == 0.0 for x in recs if x["k"] == "t") and r.stats.protect_frames == 0


# ---------------------------------------------------------------- the dataset
def test_dataset_loader_merges_clip_attributes_and_validates(tmp_path):
    (tmp_path / "x.png").write_bytes(b"")
    clip_truth = [
        {
            "frame": 0,
            "t": 0.0,
            "targets": [{"category": "face", "quad": quad_pts(CARD), "visible": True}],
            "distractors": [],
            "context": [],
        }
    ]
    (tmp_path / "c.truth.jsonl").write_text("\n".join(json.dumps(r) for r in clip_truth) + "\n")
    manifest = {
        "name": "tiny",
        "seed": 1,
        "generator_version": "g",
        "items": [{"id": "i0", "bucket": "aadhaar", "file": "x.png", "truth": {"level": "heavy", "targets": [], "distractors": []}}],
        "clips": {
            "per_bucket": 1,
            "items": [
                {
                    "id": "c", "bucket": "faces_enrolled", "video": "c.mp4", "truth_file": "c.truth.jsonl", "level": "light",
                    "motion": "static", "n_frames": 1, "crf": 25,
                    "truth": {"targets": [{"category": "face", "allowlisted": True, "person_id": "P01"}], "distractors": []},
                }
            ],
        },
    }  # fmt: skip
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    ds = dataset.load(tmp_path)
    assert len(ds.images) == 1 and ds.images[0].level == "heavy"
    t = ds.clips[0].frames[0]["targets"][0]
    assert t["allowlisted"] is True and t["person_id"] == "P01" and t["j"] == 0 and t["visible"] is True
    problems = dataset.validate(ds)
    assert any("clip files missing" in p for p in problems)
    thin = dataset.load(tmp_path, buckets={"aadhaar"})
    assert len(thin.images) == 1 and not thin.clips


def test_missing_manifest_is_a_dataset_error(tmp_path):
    with pytest.raises(dataset.DatasetError, match="gen-video"):
        dataset.load(tmp_path)
    rc, info = offline.preflight(tmp_path)
    assert rc == 2 and info["problems"]


def test_dataset_matches_checks_generator_seed_and_size(tmp_path):
    from eval.data_gen.video import suite

    assert offline.dataset_matches(tmp_path, 1234, 40, 4) == (False, "no manifest.json")
    items = [{"id": f"{b}_{i}", "file": "f.png"} for b in suite.BUCKETS for i in range(2)]
    (tmp_path / "f.png").write_bytes(b"")
    (tmp_path / "v.mp4").write_bytes(b"")
    m = {
        "generator_version": suite.GENERATOR_VERSION, "seed": 7, "n_per_bucket": 2,
        "buckets": {b: {} for b in suite.BUCKETS}, "items": items,
        "clips": {"per_bucket": 3, "items": [{"video": "v.mp4"}]},
    }  # fmt: skip
    (tmp_path / "manifest.json").write_text(json.dumps(m))
    assert offline.dataset_matches(tmp_path, 7, 2, 3)[0] is True
    assert offline.dataset_matches(tmp_path, 8, 2, 3)[0] is False
    assert offline.dataset_matches(tmp_path, 7, 3, 3)[0] is False
    assert offline.dataset_matches(tmp_path, 7, 2, 4)[0] is False


# ---------------------------------------------------------------- checks
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


def statuses(**over: str) -> dict[str, DetectorStatus]:
    out = {i: DetectorStatus(i, OK, lane="fast") for i in EXPECTED_DETECTORS}
    for i, state in over.items():
        out[i] = DetectorStatus(i, state, "x")
    return out


def measure(records, det=None, **kw):
    opts = offline.OfflineOptions(data=None, out=None, **kw)  # type: ignore[arg-type]
    m = offline.build_measurements(
        records,
        det or statuses(),
        {"fast": [10.0], "accurate": [200.0]},
        {},
        {"clip_frames": 0, "detect_errors": {}},
        opts,
    )
    return m, offline.analyse_offline(m, opts), opts


def by_name(checks):
    return {c.name: c for c in checks}


def test_recall_checks_pass_and_fail_against_the_plan_targets():
    recs = [trec(f"a{i}", "aadhaar", 1.0) for i in range(20)] + [
        trec(f"p{i}", "pan", 1.0 if i < 18 else 0.0) for i in range(20)
    ]
    _m, checks, _ = measure(recs)
    c = by_name(checks)
    assert c["Aadhaar card recall [stills]"].passed and c["Aadhaar card recall [stills]"].target == 0.95
    pan = c["PAN card recall [stills]"]
    assert not pan.passed and not pan.not_run and pan.value == 0.9
    assert c[
        "Phone number (on-screen) recall [stills]"
    ].not_run  # no targets in the data: never a silent pass
    assert c["UPI QR recall [stills]"].target == 0.98 and c["Number plate recall [stills]"].target == 0.85


def test_missing_detector_rows_are_not_run_and_the_summary_check_fails():
    recs = [trec(f"a{i}", "plate", 1.0) for i in range(30)]  # a perfect-looking plate recall ...
    _m, checks, _ = measure(recs, statuses(plates=MISSING))
    c = by_name(checks)
    plate = c["Number plate recall [stills]"]
    assert (
        plate.not_run and not plate.passed and plate.value is None
    )  # ... must not pass when the detector is not there
    assert "plates" in plate.note and "missing" in plate.note
    summary = c["all expected detectors loaded and ran"]
    assert (
        not summary.passed
        and "plates (missing)" in summary.note
        and summary.value == len(EXPECTED_DETECTORS) - 1
    )


def test_min_n_makes_a_thin_row_not_run():
    _m, checks, _ = measure([trec("a", "aadhaar", 1.0)], min_n=5)
    assert by_name(checks)["Aadhaar card recall [stills]"].not_run


def test_clip_checks_only_exist_where_the_dataset_has_clips():
    recs = [trec(f"c{k}", "aadhaar", 1.0, src="clip", frame=k, motion="static") for k in range(40)]
    _m, checks, _ = measure(recs, gate_sources=("img", "clip"))
    names = by_name(checks)
    assert "Aadhaar card recall [clips]" in names and "PAN card recall [clips]" not in names
    _m, checks, _ = measure(recs, gate_sources=("img",))
    assert "Aadhaar card recall [clips]" not in by_name(checks)


def test_gate_levels_scope_is_noted_and_applied():
    recs = [trec(f"c{i}", "plate", 1.0, "clean") for i in range(25)] + [
        trec(f"h{i}", "plate", 0.0, "heavy") for i in range(25)
    ]
    _m, all_checks, _ = measure(recs, gate_levels=("clean", "light", "heavy"))
    _m, clean_checks, _ = measure(recs, gate_levels=("clean",))
    assert not by_name(all_checks)["Number plate recall [stills]"].passed
    cc = by_name(clean_checks)["Number plate recall [stills]"]
    assert cc.passed and "levels clean" in cc.note


def test_flicker_check_and_its_minimum_window_count():
    steady = [
        trec(f"c{i}", "aadhaar", 1.0, src="clip", frame=f, motion="static")
        for i in range(30)
        for f in range(30)
    ]
    _m, checks, _ = measure(steady, min_windows=20)
    fl = by_name(checks)["mask flicker on static targets"]
    assert fl.passed and fl.value == 0.0 and fl.target == 0.02
    _m, thin, _ = measure(steady[:30], min_windows=20)
    assert by_name(thin)["mask flicker on static targets"].not_run


def test_detector_exceptions_fail_the_gate():
    opts = offline.OfflineOptions(data=None, out=None)  # type: ignore[arg-type]
    m = offline.build_measurements(
        [], statuses(), {}, {}, {"detect_errors": {"text": 3}, "failed_lane_runs": 3}, opts
    )
    c = by_name(offline.analyse_offline(m, opts))["detector exceptions during the run"]
    assert not c.passed and c.value == 3.0


def test_failing_ids_list_only_missed_items_of_failed_rows():
    recs = [trec(f"p{i}", "pan", 0.0 if i < 5 else 1.0) for i in range(20)] + [
        trec(f"a{i}", "aadhaar", 1.0) for i in range(20)
    ]
    m, checks, _ = measure(recs)
    ids = offline.failing_ids(m, checks)
    assert ids == [f"pan:p{i}" for i in range(5)]
    assert all(not i.startswith("aadhaar") for i in ids)


def test_records_round_trip_and_rescoring_gives_identical_numbers(tmp_path):
    recs = [trec(f"a{i}", "aadhaar", 1.0 if i % 3 else 0.0) for i in range(30)]
    opts = offline.OfflineOptions(data=tmp_path, out=tmp_path)
    m = offline.build_measurements(
        recs, statuses(), {"fast": [5.0]}, {}, {"clip_frames": 0, "detect_errors": {}}, opts
    )
    offline.write_records(tmp_path / "offline-records.jsonl", recs)
    rc1 = offline._write_leg(opts, offline.analyse_offline(m, opts), m, "")
    first = json.loads((tmp_path / "offline.json").read_text())
    rc2 = offline.rescore(opts)
    second = json.loads((tmp_path / "offline.json").read_text())
    assert rc1 == rc2 == 1  # most rows have no data: NOT RUN is never a pass
    assert first["measurements"]["rows"] == second["measurements"]["rows"]
    assert [c["value"] for c in first["checks"]] == [c["value"] for c in second["checks"]]


def test_row_status_names_the_detector_state():
    ok, why = offline.row_status(ROW_BY_KEY["plate"], statuses(plates=UNAVAILABLE))
    assert not ok and "plates" in why and "unavailable" in why
    assert offline.row_status(ROW_BY_KEY["plate"], statuses()) == (True, "")
