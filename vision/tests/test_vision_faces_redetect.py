"""Weak-face re-search of the ``faces`` detector (full-resolution window around a masked track the 640x360 pass
missed), with a pixel-driven fake backend (CPU, no model files).

The fake sees a "strong" face on the 640x360 copy (scripted) and a "weak" face only in a small full-resolution
window: weak faces are painted as bright rectangles into the 1280x720 frame, the fake finds them in the
224x224 window by their pixels, so the window geometry (offset, scale) is checked end to end."""

from __future__ import annotations

import numpy as np
import pytest

from contracts import Category, Config, DetectContext, DetectorConfig, Device, Frame, Lane
from vision.detectors.faces import FacesDetector
from vision.detectors.faces import detector as faces_mod
from vision.detectors.faces.core import CROP_SIDE, EMBEDDING_DIM
from vision.resources import InMemoryAllowlist, VisionResources

W, H = 1280, 720
BOX = tuple[float, float, float, float]
WEAK_SCORE = 0.45  # above the re-search threshold (0.35), below the 640x360 one (0.5)
ALICE, BOB = 1, 2


def _vec(code: int) -> np.ndarray:
    v = np.zeros(EMBEDDING_DIM, np.float32)
    v[code] = 1.0
    return v


def _row(box: BOX, score: float) -> np.ndarray:
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    cx, cy = x1 + w / 2, y1 + h / 2
    lm = [
        (cx - 0.2 * w, cy - 0.15 * h),
        (cx + 0.2 * w, cy - 0.15 * h),
        (cx, cy + 0.05 * h),
        (cx - 0.15 * w, cy + 0.25 * h),
        (cx + 0.15 * w, cy + 0.25 * h),
    ]
    return np.array([x1, y1, w, h, *[c for p in lm for c in p], score], dtype=np.float32)


class PixelBackend:
    def __init__(self) -> None:
        self.strong: list[tuple[int, BOX]] = []  # (identity, box in 1280x720), seen on the 640x360 copy
        self.main_calls = 0
        self.crop_calls = 0
        self.crop_thresholds: list[float] = []
        self.embeds = 0

    def detect(self, image: np.ndarray, score_threshold: float) -> np.ndarray | None:
        h, w = image.shape[:2]
        if (w, h) == (640, 360):
            self.main_calls += 1
            rows = [_row(tuple(c / 2 for c in b), 0.9) for _, b in self.strong]
            return np.stack(rows) if rows else None
        assert (w, h) == (CROP_SIDE, CROP_SIDE), "a re-search window is a fixed square canvas"
        self.crop_calls += 1
        self.crop_thresholds.append(score_threshold)
        # window pixels: background 100, weak face 250, padding 114; find every bright blob (rows/cols)
        bright = image[..., 0] > 200
        if not bright.any() or WEAK_SCORE < score_threshold:
            return None
        ys, xs = np.nonzero(bright)
        return np.stack(
            [_row((float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())), WEAK_SCORE)]
        )

    def align(self, image: np.ndarray, row: np.ndarray) -> np.ndarray:
        cx, cy = row[0] + row[2] / 2, row[1] + row[3] / 2
        best = min(
            self.strong,
            key=lambda s: (cx - (s[1][0] + s[1][2]) / 2) ** 2 + (cy - (s[1][1] + s[1][3]) / 2) ** 2,
        )
        return np.full((112, 112, 3), best[0], np.uint8)

    def embed_aligned(self, aligned: np.ndarray) -> np.ndarray:
        self.embeds += 1
        return _vec(int(aligned[0, 0, 0]))

    def embed(self, image: np.ndarray, row: np.ndarray) -> np.ndarray:
        return self.embed_aligned(self.align(image, row))

    def warmup(self) -> None:
        pass


class Rig:
    def __init__(self, *, redetect: bool = True, allowed: tuple[int, ...] = (ALICE,), **cat: object) -> None:
        self.cfg = Config(profile="dev", _env_file=None)
        if cat:
            self.cfg.categories[Category.FACE] = self.cfg.categories[Category.FACE].model_copy(update=cat)
        self.backend = PixelBackend()
        allow = InMemoryAllowlist()
        allow.update([(f"p{code}", _vec(code)) for code in allowed])
        self.res = VisionResources(self.cfg, allowlist=allow, ocr=lambda f: None)  # type: ignore[arg-type]
        self.det = FacesDetector(self.backend)
        self.det.configure(
            DetectorConfig(
                categories=self.cfg.categories,
                device=Device.CPU,
                params={"embed_async": False, "redetect": redetect},
            ),
            self.res,
        )
        self.seq = 0

    def run(
        self,
        strong: list[tuple[int, BOX]] | None = None,
        weak: list[BOX] | None = None,
        deadline: float | None = None,
    ) -> list:
        self.backend.strong = strong or []
        image = np.full((H, W, 3), 100, np.uint8)
        for x1, y1, x2, y2 in weak or []:
            image[int(y1) : int(y2), int(x1) : int(x2)] = 250
        frame = Frame(self.seq, self.seq / 30.0, 0.0, image)
        self.seq += 3
        ctx = DetectContext(lane=Lane.FAST, categories=self.cfg.categories, stride=3, deadline=deadline)
        return self.det.detect(frame, ctx)

    def close(self) -> None:
        self.det.close()


@pytest.fixture(autouse=True)
def _no_time_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The search skips itself on a slow run; the tests must not depend on how busy the machine is."""
    monkeypatch.setattr(faces_mod, "REDETECT_START_BY_S", 1e9)


BOX_A: BOX = (500, 250, 600, 370)
BOX_B: BOX = (900, 100, 1000, 220)


def _iou(a: BOX, b: BOX) -> float:
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def _seed_stranger(rig: Rig, runs: int = 3) -> None:
    for _ in range(runs):
        (d,) = rig.run([(BOB, BOX_A)])
        assert not d.allow


def test_face_lost_on_the_small_copy_is_kept_masked_from_the_full_resolution_frame() -> None:
    rig = Rig()
    _seed_stranger(rig)
    for _ in range(12):  # 1.2 s: longer than the planner's 0.8 s hold
        (d,) = rig.run(weak=[BOX_A])
        assert d.category is Category.FACE and not d.allow
        assert d.extra == {"reason": "face_redetected"}
        assert d.aux_quads == {}
        assert d.confidence == pytest.approx(WEAK_SCORE, abs=1e-3)
        # the window geometry (offset and scale) is right: the box lands on the painted face
        x1, y1, x2, y2 = d.quad.bbox()
        w, h = BOX_A[2] - BOX_A[0], BOX_A[3] - BOX_A[1]
        painted = (BOX_A[0] - 0.1 * w, BOX_A[1] - 0.1 * h, BOX_A[2] + 0.1 * w, BOX_A[3] + 0.1 * h)
        assert _iou((x1, y1, x2, y2), painted) > 0.8  # the painted box, dilated by the detector's 20 %
    assert rig.backend.crop_calls == 12
    rig.close()


def test_without_the_search_the_same_scene_loses_the_face() -> None:
    rig = Rig(redetect=False)
    _seed_stranger(rig)
    assert rig.run(weak=[BOX_A]) == []
    assert rig.backend.crop_calls == 0
    rig.close()


def test_search_uses_the_planner_floor_as_threshold() -> None:
    sure = Rig(mask_when_unsure=False)
    unsure = Rig(mask_when_unsure=True)
    for rig in (sure, unsure):
        _seed_stranger(rig)
        rig.run(weak=[BOX_A])
    assert sure.backend.crop_thresholds[-1] == pytest.approx(faces_mod.REDETECT_SCORE)
    assert unsure.backend.crop_thresholds[-1] == pytest.approx(faces_mod.REDETECT_SCORE_UNSURE)
    sure.close()
    unsure.close()


def test_nothing_is_searched_while_the_small_copy_sees_the_face() -> None:
    rig = Rig()
    for _ in range(20):
        rig.run([(BOB, BOX_A)])
    assert rig.backend.crop_calls == 0
    rig.close()


def test_a_found_face_is_never_allowed_never_embedded_and_does_not_prove_identity() -> None:
    """Alice's track is still unverified when she turns weak: the re-found face stays masked, costs no SFace pass,
    and her next normal sighting still has to pass the forced check before it is shown."""
    rig = Rig()
    rig.run([(ALICE, BOX_A)])
    embeds = rig.backend.embeds
    for _ in range(10):
        dets = rig.run(weak=[BOX_A])
        assert len(dets) == 1 and not dets[0].allow and dets[0].extra["reason"] == "face_redetected"
    assert rig.backend.embeds == embeds, "weak faces are not embedded"
    (d,) = rig.run([(ALICE, BOX_A)])
    assert not d.allow, "back on the small copy after a gap: masked until a fresh check passes"
    assert rig.backend.embeds == embeds + 1, "the gap owes (and gets) a forced check"
    for _ in range(14):
        (d,) = rig.run([(ALICE, BOX_A)])
    assert d.allow, "...and shown once the checks pass"
    rig.close()


def test_a_stranger_stepping_into_the_search_window_of_an_unverified_track_stays_masked() -> None:
    rig = Rig()
    rig.run([(ALICE, BOX_A)])
    for _ in range(6):
        (d,) = rig.run(weak=[BOX_A])
        assert not d.allow
    for _ in range(40):
        (d,) = rig.run([(BOB, BOX_A)])
        assert not d.allow
    rig.close()


def test_an_allowlisted_track_is_left_alone() -> None:
    """Allowed tracks are not searched: an enrolled face that goes weak is neither masked nor re-found."""
    rig = Rig()
    for _ in range(14):
        (d,) = rig.run([(ALICE, BOX_A)])
    assert d.allow
    crops = rig.backend.crop_calls
    assert rig.run(weak=[BOX_A]) == []
    assert rig.backend.crop_calls == crops
    rig.close()


def test_a_face_that_really_left_is_not_invented_and_the_search_stops() -> None:
    rig = Rig()
    _seed_stranger(rig)
    for _ in range(40):
        assert rig.run() == []
    # the tracker forgets the track after 1.5 s (15 runs): no more searching after that
    calls = rig.backend.crop_calls
    assert 0 < calls <= 16
    for _ in range(10):
        rig.run()
    assert rig.backend.crop_calls == calls
    rig.close()


def test_a_track_seen_once_gives_up_after_a_few_empty_searches() -> None:
    rig = Rig()
    rig.run([(BOB, BOX_A)])  # one sighting only
    for _ in range(10):
        rig.run()
    assert rig.backend.crop_calls == faces_mod.REDETECT_GIVE_UP
    rig.close()


def test_a_second_nearby_face_is_not_taken_for_the_lost_one() -> None:
    """The window of a lost track holds only a face that overlaps the track's own box."""
    rig = Rig()
    _seed_stranger(rig)
    far: BOX = (BOX_A[0] + 150, BOX_A[1], BOX_A[2] + 150, BOX_A[3])  # inside the padded window, no overlap
    assert rig.run(weak=[far]) == []
    rig.close()


def test_the_number_of_searched_tracks_per_run_is_capped() -> None:
    rig = Rig()
    boxes = [(100.0 + 250 * i, 300.0, 200.0 + 250 * i, 420.0) for i in range(4)]
    for _ in range(3):
        assert len(rig.run([(BOB, b) for b in boxes])) == 4
    found = rig.run(weak=boxes)
    assert len(found) == faces_mod.REDETECT_MAX_TRACKS
    assert rig.backend.crop_calls == faces_mod.REDETECT_MAX_TRACKS
    rig.close()


def test_not_run_when_the_lane_is_late_or_the_category_is_off() -> None:
    late = Rig()
    _seed_stranger(late)
    assert late.run(weak=[BOX_A], deadline=0.0) == [], "past the deadline: the lane skips the optional work"
    assert late.backend.crop_calls == 0
    late.close()
    off = Rig(allowed=(), enabled=False)
    for _ in range(3):
        off.run([(BOB, BOX_A)])
    assert off.run(weak=[BOX_A]) == []
    assert off.backend.crop_calls == 0
    off.close()


def test_search_window_is_clamped_at_the_frame_edge() -> None:
    rig = Rig()
    corner: BOX = (1180, 600, 1279, 719)
    for _ in range(3):
        rig.run([(BOB, corner)])
    (d,) = rig.run(weak=[corner])
    x1, y1, x2, y2 = d.quad.bbox()
    assert 0 <= x1 < x2 <= W and 0 <= y1 < y2 <= H
    rig.close()


def test_a_slow_run_skips_the_search_and_leaves_the_face_to_the_planner_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig = Rig()
    _seed_stranger(rig)
    monkeypatch.setattr(faces_mod, "REDETECT_START_BY_S", 0.0)  # every run is "too late"
    assert rig.run(weak=[BOX_A]) == []
    assert rig.backend.crop_calls == 0
    monkeypatch.setattr(faces_mod, "REDETECT_START_BY_S", 1e9)
    (d,) = rig.run(weak=[BOX_A])
    assert d.extra["reason"] == "face_redetected", "the next run searches again"
    rig.close()


def test_the_longest_unsearched_track_goes_first_when_the_cap_is_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(faces_mod, "REDETECT_MAX_TRACKS", 1)
    rig = Rig()
    two = [BOX_A, BOX_B]
    for _ in range(3):
        rig.run([(BOB, b) for b in two])
    seen: list[BOX] = []
    for _ in range(4):
        (d,) = rig.run(weak=two)
        seen.append(d.quad.bbox())
    left = [b[0] < 700 for b in seen]
    assert left.count(True) == 2 and left.count(False) == 2 and left[0] != left[1], "round robin"
    rig.close()
