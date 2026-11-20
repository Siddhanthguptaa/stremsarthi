"""Faces provenance and enrolment set (T2.1): public-domain NASA portraits only, hash-pinned."""

import random
from pathlib import Path

import numpy as np
import pytest

from eval.data_gen.video import faces as faces_mod
from eval.data_gen.video import suite

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / suite.DEFAULT_FACES_CACHE
HAVE = not faces_mod.cache_problems(CACHE)


def test_every_face_source_has_licence_url_hash_credit_and_a_sane_box():
    sources = faces_mod.load_sources()
    assert len(sources) >= 40
    assert faces_mod.provenance_problems(sources) == []
    for s in sources:
        assert s.url.startswith("https://images-assets.nasa.gov/") or "nasa.gov" in s.url
        assert s.page_url == f"https://images.nasa.gov/details/{s.nasa_id}"
        assert "ublic domain" in s.licence


def test_provenance_check_rejects_bad_records():
    good = faces_mod.load_sources()[0]
    from dataclasses import replace

    bad = [
        replace(good, sha256="xyz"),
        replace(good, url="http://example.com/a.jpg"),
        replace(good, licence="CC-BY-NC"),
        replace(good, credit=""),
        replace(good, box=(0.0, 0.0, 5.0, 5.0)),
    ]
    for rec in bad:
        assert faces_mod.provenance_problems((rec,)), rec


def test_enrolled_people_have_enough_distinct_portraits_and_disjoint_test_sets():
    for pid in faces_mod.ENROLLED_PERSONS:
        enroll = faces_mod.sources_for(use="enroll", person_id=pid)
        test = faces_mod.sources_for(use="test", person_id=pid)
        assert len(enroll) >= 3 and len(test) >= 2
        assert {s.nasa_id for s in enroll}.isdisjoint({s.nasa_id for s in test})
        assert len({s.person for s in enroll + test}) == 1
    strangers = faces_mod.sources_for(role="stranger")
    assert len({s.person_id for s in strangers}) >= 12
    assert not {s.person_id for s in strangers} & set(faces_mod.ENROLLED_PERSONS)


@pytest.mark.skipif(not HAVE, reason="portraits not cached (run gen-video once with network)")
def test_cached_portraits_match_pinned_hashes_and_sizes():
    assert faces_mod.cache_problems(CACHE) == []
    for s in faces_mod.load_sources()[:8]:
        img = faces_mod.load_image(str(CACHE), s.nasa_id)
        assert (img.shape[1], img.shape[0]) == s.size


@pytest.mark.skipif(not HAVE, reason="portraits not cached (run gen-video once with network)")
def test_enrolment_frames_are_enough_and_come_only_from_enrol_portraits():
    sets = suite.build_enroll_sets(None, 1234, str(CACHE))
    assert set(sets) == set(faces_mod.ENROLLED_PERSONS)
    enroll_ids = {s.nasa_id for s in faces_mod.sources_for(use="enroll")}
    for entry in sets.values():
        assert len(entry["frames"]) >= 20  # vision spec 5.5: at least 20 frames per enrolment
        assert {f["source"] for f in entry["frames"]} <= enroll_ids
        for f in entry["frames"]:
            xs = [p[0] for p in f["face_quad"]]
            ys = [p[1] for p in f["face_quad"]]
            assert 0 <= min(xs) and max(xs) <= 1280 and 0 <= min(ys) and max(ys) <= 720
            w = max(xs) - min(xs)
            assert w >= 150  # the face is large enough to enrol from


@pytest.mark.skipif(not HAVE, reason="portraits not cached (run gen-video once with network)")
def test_face_tile_quads_follow_the_pixels():
    """A mirrored, rotated tile keeps the face box on the face: the quad centre stays near the tile centre band."""
    src = faces_mod.sources_for(use="test", person_id="P01")[0]
    for seed in range(5):
        tile, quad = faces_mod.render_tile(str(CACHE), src, (400, 300), 0.3, random.Random(seed), angle=4.0)
        assert tile.shape == (300, 400, 3) and tile.dtype == np.uint8
        cx = sum(p[0] for p in quad) / 4
        cy = sum(p[1] for p in quad) / 4
        assert 0.3 * 400 <= cx <= 0.7 * 400 and 0.25 * 300 <= cy <= 0.62 * 300


@pytest.mark.skipif(not HAVE, reason="portraits not cached (run gen-video once with network)")
def test_face_negatives_do_not_use_the_face_region():
    sc = faces_mod.render_face_negative(random.Random(3), str(CACHE))
    assert not sc.targets and sc.distractors
    assert sc.image.shape == (720, 1280, 3)
