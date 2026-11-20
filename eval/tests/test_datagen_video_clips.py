"""Video clips (T2.1): per-frame ground truth, motion kinds, determinism, x264 encode, manifest and CLI wiring."""

import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest

from eval import run as eval_run
from eval.data_gen.common import specimen
from eval.data_gen.video import cli, clips, suite
from eval.data_gen.video import faces as faces_mod
from eval.sender.stamp import STAMP_EXCLUSION

ROOT = Path(__file__).resolve().parents[2]
FACES_CACHE = str(ROOT / suite.DEFAULT_FACES_CACHE)
HAVE_FFMPEG = shutil.which("ffmpeg") is not None
HAVE_FACES = not faces_mod.cache_problems(Path(FACES_CACHE))
SEC = 1.0  # 30 frames keeps the tests quick


def _render(bucket, index, seed=1234, seconds=SEC):
    frames, truth = clips.render_clip(bucket, index, seed, FACES_CACHE, seconds)
    recs = []
    last = None
    for img, rec in frames:
        recs.append(rec)
        last = img
    return recs, truth, last


def _centre(rec):
    q = np.array(rec["quad"])
    return q.mean(axis=0)


def test_plan_covers_levels_motions_and_crf_range():
    seen = {clips.plan_for(i) for i in range(len(clips.PLAN))}
    assert {lv for lv, _ in seen} == {"clean", "light", "heavy"}
    assert {m for _, m in seen} == set(clips.MOTIONS)
    assert clips.plan_for(0) == ("clean", "static") and clips.plan_for(1)[0] == "heavy"
    lo = min(v[0] for v in clips.CRF.values())
    hi = max(v[1] for v in clips.CRF.values())
    assert (lo, hi) == (23, 35)
    assert set(clips.CLIP_BUCKETS) <= set(suite.BUCKETS)


@pytest.mark.parametrize("bucket", [b for b in clips.CLIP_BUCKETS if not suite.SPECS[b].needs_faces])
def test_clip_truth_is_per_frame_and_follows_the_visibility_rule(bucket):
    sx1, sy1, sx2, sy2 = STAMP_EXCLUSION
    for index in (0, 1):
        recs, truth, img = _render(bucket, index)
        assert len(recs) == truth["n_frames"] == 30 and img.shape == (720, 1280, 3)
        assert [r["frame"] for r in recs] == list(range(30))
        assert truth["category"] == bucket and truth["specimen"] is True
        assert 23 <= truth["crf"] <= 35 and truth["fps"] == 30 and truth["aug"]["jpeg_quality"] == 0
        assert len(truth["targets"]) == len(recs[0]["targets"])
        assert len(truth["distractors"]) == len(recs[0]["distractors"])
        for r in recs:
            for key in ("targets", "distractors"):
                for j, t in enumerate(r[key]):
                    assert len(t["quad"]) == 4 and t["category"] == truth[key][j]["category"]
                    assert t["visible"] == (t["in_frame_fraction"] >= 0.6 and t["min_side_px"] >= 24)
                    assert 0.0 <= t["occlusion_fraction"] <= 1.0
                    xs, ys = [p[0] for p in t["quad"]], [p[1] for p in t["quad"]]
                    assert max(xs) < sx1 or min(xs) > sx2 or max(ys) < sy1 or min(ys) > sy2
        if suite.SPECS[bucket].group != "negative" and bucket != "qr_negative":
            assert truth["main_target_indices"] and any(t["visible"] for r in recs for t in r["targets"])


def test_static_clip_barely_moves_and_drift_clip_moves():
    recs, truth, _ = _render("aadhaar", 0)  # (clean, static)
    assert truth["motion"]["kind"] == "static"
    c = np.array([_centre(r["targets"][0]) for r in recs])
    assert np.ptp(c, axis=0).max() <= 4.0
    recs, truth, _ = _render("aadhaar", 4)  # (light, drift)
    assert truth["motion"]["kind"] == "drift"
    if truth["motion"]["amplitude"] > 0:
        c = np.array([_centre(r["targets"][0]) for r in recs])
        assert np.ptp(c, axis=0).max() > 8.0
    recs, truth, _ = _render("plate", 2)  # (light, wobble): the tilt stays within +-30 degrees
    assert truth["motion"]["kind"] == "wobble"


def test_clip_is_deterministic_and_seed_sensitive():
    a_recs, a_truth, _ = _render("pan", 1)
    b_recs, b_truth, _ = _render("pan", 1)
    c_recs, c_truth, _ = _render("pan", 1, seed=99)
    assert a_truth["frame_sha256"] == b_truth["frame_sha256"] != c_truth["frame_sha256"]
    assert a_recs == b_recs and a_recs != c_recs


def test_screen_clip_zoom_keeps_targets_in_frame():
    recs, truth, _ = _render("text_phone", 0)  # clean screen: identity zoom path
    assert truth["geometry"]["mode"] == "identity"
    assert all(t["in_frame_fraction"] >= 0.9 for r in recs for t in r["targets"])


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not on PATH")
def test_encode_writes_a_720p30_h264_file_with_the_right_frame_count(tmp_path):
    item = clips.clip_task(("pan", 1, 5, str(tmp_path), FACES_CACHE, SEC))
    mp4 = tmp_path / item["video"]
    assert mp4.stat().st_size > 5000
    cap = cv2.VideoCapture(str(mp4))
    try:
        assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 30
        assert (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))) == (
            1280,
            720,
        )
        assert abs(cap.get(cv2.CAP_PROP_FPS) - 30.0) < 0.01
        ok, frame = cap.read()
        assert ok and frame.shape == (720, 1280, 3)
    finally:
        cap.release()
    lines = (tmp_path / item["truth_file"]).read_text().splitlines()
    assert len(lines) == 30 and json.loads(lines[0])["frame"] == 0
    meta = json.loads((tmp_path / item["meta_file"]).read_text())
    assert meta["frame_sha256"] == item["frame_sha256"] and meta["targets"][0]["number"]


def test_suite_manifest_lists_clips_deterministically_and_stays_clean():
    kw = {"workers": 1, "faces_cache": FACES_CACHE, "clips_per_bucket": 1, "clip_seconds": SEC}
    a = suite.build_suite(None, 1234, 1, ("aadhaar", "plate", "neg_card"), **kw)
    b = suite.build_suite(None, 1234, 1, ("aadhaar", "plate", "neg_card"), **kw)
    assert a["manifest_sha256"] == b["manifest_sha256"]
    assert {c["bucket"] for c in a["clips"]["items"]} == {"aadhaar", "plate"}  # neg_card has no clip bucket
    assert a["clips"]["fps"] == 30 and all(c["n_frames"] == 30 for c in a["clips"]["items"])
    assert all(c["visible_target_frames"] > 0 for c in a["clips"]["items"])
    assert specimen.scan_obj({k: v for k, v in a.items() if not k.startswith("_")}) == []
    no_clips = suite.build_suite(None, 1234, 1, ("aadhaar",), workers=1, faces_cache=FACES_CACHE)
    assert "clips" not in no_clips


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not on PATH")
def test_gen_video_command_writes_images_clips_and_manifest(tmp_path):
    out = tmp_path / "v"
    argv = ["--out", str(out), "--n", "1", "--buckets", "otp_screen", "--clips", "1", "--clip-seconds", "1"]
    argv += ["--workers", "1", "--seed", "3"]
    assert eval_run.main(["gen-video", *argv]) == 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert len(manifest["items"]) == 1 and len(manifest["clips"]["items"]) == 1
    clip = manifest["clips"]["items"][0]
    assert (out / clip["video"]).exists() and (out / clip["truth_file"]).exists()
    assert (out / manifest["items"][0]["file"]).exists()
    with pytest.raises(ValueError):
        cli.main(["--out", str(out), "--n", "1", "--buckets", "nonsense", "--clips", "0"])


@pytest.mark.skipif(not HAVE_FACES, reason="portraits not cached (run gen-video once with network)")
def test_face_clip_marks_allowlisted_faces():
    recs, truth, _ = _render("faces_enrolled", 0)
    flags = {t["allowlisted"] for t in truth["targets"]}
    assert True in flags
    assert all(len(r["targets"]) == len(truth["targets"]) for r in recs)
