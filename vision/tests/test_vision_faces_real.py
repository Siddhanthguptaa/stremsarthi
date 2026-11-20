"""The faces detector with the real YuNet + SFace weights on public-domain portraits (CPU).

Skipped when the model weights or the pinned NASA portraits (``faces_testdata``) are unavailable offline. The
full acceptance run is ``python -m vision.detectors.faces.evaluate`` (see its docstring)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from contracts import Category, Config, DetectContext, Frame, Lane
from vision.detectors.faces import evaluate as ev
from vision.detectors.faces.core import load_models
from vision.detectors.faces.enroll import Enroller

_spec = importlib.util.spec_from_file_location(
    "faces_testdata", Path(__file__).with_name("faces_testdata.py")
)
_td = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_td)  # type: ignore[union-attr]
CACHE, ensure_photos = _td.CACHE, _td.ensure_photos

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def world():
    try:
        models = load_models()
    except Exception as exc:  # noqa: BLE001 - missing weights: run `python models/prepare.py --profile dev`
        pytest.skip(f"face models unavailable: {type(exc).__name__}")
    if ensure_photos() is None:
        pytest.skip("portraits unavailable offline")
    models.warmup()
    people = ev.load_people(CACHE, models)
    return models, people


def _scene_run(det, tiles, enrolled_flags, runs=24):
    cfg = Config(profile="dev", _env_file=None)
    rng = np.random.default_rng(3)
    bg = ev._background(rng, (1280, 720))
    xs = [640.0] if len(tiles) == 1 else [460.0, 820.0]
    placements = [
        ev.Placement(t, xs[i], 340.0, 200.0, enrolled_flags[i], phase=i) for i, t in enumerate(tiles)
    ]
    out = []
    for run in range(runs):
        image, truth = ev.render(placements, 1.0, run, rng, bg)
        frame = Frame(run * 3, run * 0.1, 0.0, image)
        dets = det.detect(frame, DetectContext(lane=Lane.FAST, categories=cfg.categories, stride=3))
        out.append((dets, truth))
    return out


def test_enrolled_person_visible_and_stranger_beside_them_masked(world) -> None:
    models, people = world
    cfg = Config(profile="dev", _env_file=None)
    names = [p for p in ev.ENROLLED if p in people and len(people[p]) > ev.ENROL_PHOTOS]
    if not names:
        pytest.skip("no person with enough usable portraits")
    me = names[0]
    stranger = next(p for p in sorted(people) if p not in names)
    emb = ev.enrol_person(Enroller(backend=models), people[me], np.random.default_rng(7))
    det = ev.make_detector(cfg, ev._Allow([(me, emb)]), async_embed=False)
    runs = _scene_run(det, [people[me][ev.ENROL_PHOTOS], people[stranger][0]], [True, False])
    for dets, truth in runs[10:]:
        assert len(dets) == 2
        for p, gt, _vis in truth:
            hit = next(d for d in dets if ev._coverage(gt, [d.quad.bbox()]) >= 0.5)
            assert hit.category is Category.FACE
            assert hit.allow == p.enrolled, "the enrolled face is allowed, the stranger is masked"
    det.close()


def test_empty_allowlist_masks_everyone(world) -> None:
    models, people = world
    cfg = Config(profile="dev", _env_file=None)
    person = sorted(people)[0]
    det = ev.make_detector(cfg, ev._Allow([]), async_embed=False)
    for dets, _truth in _scene_run(det, [people[person][0]], [False], runs=8):
        assert dets and not any(d.allow for d in dets)
    det.close()
