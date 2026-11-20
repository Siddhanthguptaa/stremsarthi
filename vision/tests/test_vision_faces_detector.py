"""The ``faces`` detector with a scripted fake backend (CPU, no model files): per-track votes, allowlist
changes, async embedding, mouth quad, padding, privacy. Real-model checks live in test_vision_faces_real.py."""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
import pytest

from contracts import (
    Category,
    Config,
    DetectContext,
    DetectorConfig,
    Device,
    Frame,
    Lane,
)
from vision import registry
from vision.detectors.faces import DETECTOR, FacesDetector
from vision.detectors.faces.core import EMBEDDING_DIM, mouth_quad
from vision.resources import InMemoryAllowlist, VisionResources

W, H = 1280, 720
BOX = tuple[float, float, float, float]


def _vec(code: int) -> np.ndarray:
    v = np.zeros(EMBEDDING_DIM, np.float32)
    v[code] = 1.0
    return v


IDENT = {"alice": 1, "bob": 2, "carol": 3}


class FakeBackend:
    """YuNet/SFace stand-in. ``scene`` is a list of ``(identity, box in 1280x720)``; the embedding of a face is
    the unit vector of its identity (cos 1.0 with the same person, 0.0 with anyone else)."""

    def __init__(self) -> None:
        self.scene: list[tuple[str, BOX]] = []
        self.thresholds: list[float] = []
        self.embeds = 0
        self.fail_embed = False

    @staticmethod
    def _row(box: BOX, sx: float, sy: float) -> np.ndarray:
        x1, y1, x2, y2 = box
        w, h = x2 - x1, y2 - y1
        cx, cy = x1 + w / 2, y1 + h / 2
        lm = [  # right eye, left eye, nose, right mouth corner, left mouth corner (subject's right = image left)
            (cx - 0.2 * w, cy - 0.15 * h),
            (cx + 0.2 * w, cy - 0.15 * h),
            (cx, cy + 0.05 * h),
            (cx - 0.15 * w, cy + 0.25 * h),
            (cx + 0.15 * w, cy + 0.25 * h),
        ]
        row = np.array([x1, y1, w, h, *[c for p in lm for c in p], 0.9], dtype=np.float32)
        out = row.copy()
        out[[0, 2, 4, 6, 8, 10, 12]] *= sx
        out[[1, 3, 5, 7, 9, 11, 13]] *= sy
        return out

    def detect(self, image: np.ndarray, score_threshold: float) -> np.ndarray | None:
        self.thresholds.append(score_threshold)
        if not self.scene:
            return None
        h, w = image.shape[:2]
        return np.stack([self._row(b, w / W, h / H) for _, b in self.scene])

    def align(self, image: np.ndarray, row: np.ndarray) -> np.ndarray:
        cx, cy = row[0] + row[2] / 2, row[1] + row[3] / 2  # 1280x720 coordinates
        best = min(
            self.scene,
            key=lambda s: (cx - (s[1][0] + s[1][2]) / 2) ** 2 + (cy - (s[1][1] + s[1][3]) / 2) ** 2,
        )
        return np.full((112, 112, 3), IDENT[best[0]], np.uint8)

    def embed_aligned(self, aligned: np.ndarray) -> np.ndarray:
        self.embeds += 1
        if self.fail_embed:
            raise RuntimeError("boom")
        return _vec(int(aligned[0, 0, 0]))

    def embed(self, image: np.ndarray, row: np.ndarray) -> np.ndarray:
        return self.embed_aligned(self.align(image, row))

    def warmup(self) -> None:
        pass


class Rig:
    def __init__(
        self, *, allowed: tuple[str, ...] = ("alice",), async_embed: bool = False, **cat: Any
    ) -> None:
        self.cfg = Config(profile="dev", _env_file=None)
        if cat:
            self.cfg.categories[Category.FACE] = self.cfg.categories[Category.FACE].model_copy(update=cat)
        self.backend = FakeBackend()
        self.allow = InMemoryAllowlist()
        self.allow.update([(n, _vec(IDENT[n])) for n in allowed])
        self.res = VisionResources(self.cfg, allowlist=self.allow, ocr=lambda f: None)  # type: ignore[arg-type]
        self.det = FacesDetector(self.backend)
        self.det.configure(
            DetectorConfig(
                categories=self.cfg.categories, device=Device.CPU, params={"embed_async": async_embed}
            ),
            self.res,
        )
        self.image = np.full((H, W, 3), 100, np.uint8)
        self.seq = 0

    def run(self, scene: list[tuple[str, BOX]] | None = None) -> list:
        if scene is not None:
            self.backend.scene = scene
        frame = Frame(self.seq, self.seq / 30.0, 0.0, self.image)
        self.seq += 3  # dev stride: a detection run every 0.1 s
        ctx = DetectContext(lane=Lane.FAST, categories=self.cfg.categories, stride=3)
        return self.det.detect(frame, ctx)

    def close(self) -> None:
        self.det.close()


def _by_x(dets: list) -> list:
    return sorted(dets, key=lambda d: d.quad.bbox()[0])


LEFT: BOX = (300, 200, 420, 340)
RIGHT: BOX = (800, 200, 920, 340)


# --------------------------------------------------------------------------- plugin interface
def test_registry_finds_faces_detector() -> None:
    found = registry.discover()
    assert found["faces"] is DETECTOR
    assert DETECTOR.lane is Lane.FAST
    assert DETECTOR.categories == frozenset({Category.FACE})
    assert DETECTOR.device is Device.CPU and DETECTOR.vram_mb == 0


def test_detect_before_configure_raises() -> None:
    with pytest.raises(RuntimeError):
        FacesDetector(FakeBackend()).detect(
            Frame(0, 0.0, 0.0, np.zeros((H, W, 3), np.uint8)),
            DetectContext(lane=Lane.FAST, categories={}, stride=3),
        )


# --------------------------------------------------------------------------- allowlist behaviour
def test_enrolled_face_masked_until_confirmed_then_allowed() -> None:
    rig = Rig()
    allowed_at = None
    for i in range(1, 16):
        dets = rig.run([("alice", LEFT)])
        assert len(dets) == 1 and dets[0].category is Category.FACE
        if dets[0].allow:
            allowed_at = i
            break
        assert dets[0].extra["reason"] in {"face_unverified", "face_unmatched"}
    assert allowed_at is not None and allowed_at >= 5, "needs 5 matching samples (spec 5.5.3)"
    assert allowed_at <= 7
    assert dets[0].extra["reason"] == "face_allowlisted"
    assert "mouth" in dets[0].aux_quads
    # and it stays allowed
    for _ in range(30):
        assert rig.run()[0].allow
    rig.close()


def test_stranger_is_never_allowed() -> None:
    rig = Rig()
    for _ in range(40):
        (d,) = rig.run([("bob", LEFT)])
        assert not d.allow
        assert d.aux_quads == {}
    assert d.extra["reason"] == "face_unmatched"
    rig.close()


def test_empty_allowlist_masks_everybody_with_reason() -> None:
    rig = Rig(allowed=())
    for _ in range(12):
        dets = rig.run([("alice", LEFT), ("bob", RIGHT)])
        assert len(dets) == 2 and not any(d.allow for d in dets)
    assert {d.extra["reason"] for d in dets} == {"face_no_allowlist"}
    assert rig.backend.embeds == 0, "nothing to compare against: no SFace passes"
    rig.close()


@pytest.mark.parametrize("stranger_on_left", [False, True])
def test_stranger_beside_enrolled_never_inherits_votes(stranger_on_left: bool) -> None:
    """Regression for PrivaStream's shared vote buffer: the neighbour's matches must not unmask a stranger."""
    rig = Rig()
    scene = [("bob", LEFT), ("alice", RIGHT)] if stranger_on_left else [("alice", LEFT), ("bob", RIGHT)]
    seen_allowed = 0
    for _ in range(60):
        dets = _by_x(rig.run(scene))
        assert len(dets) == 2
        bob, alice = (dets[0], dets[1]) if stranger_on_left else (dets[1], dets[0])
        assert not bob.allow
        seen_allowed += alice.allow
        assert bob.track_id != alice.track_id
    assert seen_allowed >= 50
    rig.close()


def test_two_strangers_and_enrolled_among_them() -> None:
    rig = Rig()
    mid: BOX = (550, 200, 670, 340)
    for _ in range(40):
        dets = _by_x(rig.run([("bob", LEFT), ("alice", mid), ("carol", RIGHT)]))
    assert [d.allow for d in dets] == [False, True, False]
    rig.close()


def test_track_flipping_to_a_stranger_is_masked_again() -> None:
    rig = Rig()
    for _ in range(12):
        (d,) = rig.run([("alice", LEFT)])
    assert d.allow
    flipped = None
    for i in range(1, 25):
        (d,) = rig.run([("bob", LEFT)])  # a stranger takes over the very same box
        if not d.allow:
            flipped = i
            break
    assert flipped is not None and flipped <= 6, "periodic re-check must catch a swap within a few runs"
    for _ in range(30):
        assert not rig.run()[0].allow
    rig.close()


def test_face_returning_after_a_gap_is_verified_again_before_it_is_shown() -> None:
    """The track id survives a short absence, but the face on it may not: it owes a fresh embedding check."""
    rig = Rig()
    for _ in range(12):
        (d,) = rig.run([("alice", LEFT)])
    assert d.allow
    before = rig.backend.embeds
    for _ in range(8):  # gone for 0.8 s (the tracker keeps the track for 1.5 s)
        rig.run([])
    (d,) = rig.run([("alice", LEFT)])
    assert rig.backend.embeds == before + 1, "an immediate forced re-check, not blind trust in the old votes"
    assert d.allow  # same person: the check passes
    rig.close()


def test_stranger_appearing_on_a_lost_track_is_masked_at_once() -> None:
    rig = Rig()
    for _ in range(12):
        (d,) = rig.run([("alice", LEFT)])
    assert d.allow
    for _ in range(8):
        rig.run([])
    for _ in range(30):
        (d,) = rig.run([("bob", LEFT)])  # a stranger steps into the same spot
        assert not d.allow
    rig.close()


def test_async_returning_face_is_masked_until_the_forced_check_lands() -> None:
    rig = Rig(async_embed=True)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not rig.run([("alice", LEFT)])[0].allow:
        time.sleep(0.005)
    for _ in range(8):
        rig.run([])
    (d,) = rig.run([("alice", LEFT)])
    assert not d.allow, "the check is in flight: masked meanwhile"
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not d.allow:
        time.sleep(0.005)
        (d,) = rig.run()
    assert d.allow
    rig.close()


def test_allowlist_change_resets_votes() -> None:
    rig = Rig()
    for _ in range(12):
        (d,) = rig.run([("alice", LEFT)])
    assert d.allow
    rig.allow.update([("bob", _vec(IDENT["bob"]))])  # alice is removed
    (d,) = rig.run()
    assert not d.allow, "a removed face is masked on the very next run"
    for _ in range(30):
        (d,) = rig.run()
        assert not d.allow
    rig.allow.update([("alice", _vec(IDENT["alice"]))])  # re-enrolled: verified from scratch
    (d,) = rig.run()
    assert not d.allow
    for _ in range(12):
        (d,) = rig.run()
    assert d.allow
    rig.close()


def test_malformed_allowlist_vectors_are_ignored() -> None:
    rig = Rig()
    rig.allow.update([("bad1", [1.0, 2.0]), ("bad2", [float("nan")] * EMBEDDING_DIM)])
    for _ in range(10):
        (d,) = rig.run([("alice", LEFT)])
    assert not d.allow and d.extra["reason"] == "face_no_allowlist"
    rig.close()


# --------------------------------------------------------------------------- async embedding pool
def test_async_embedding_confirms_and_never_shows_before_the_vote_lands() -> None:
    rig = Rig(async_embed=True)
    first = rig.run([("alice", LEFT)])
    assert not first[0].allow, "the first run has no embedding yet: masked"
    deadline = time.monotonic() + 5.0
    allowed = False
    while time.monotonic() < deadline and not allowed:
        time.sleep(0.01)
        allowed = rig.run()[0].allow
    assert allowed
    rig.close()


def test_async_stranger_next_to_enrolled_stays_masked() -> None:
    rig = Rig(async_embed=True)
    deadline = time.monotonic() + 5.0
    ok = False
    while time.monotonic() < deadline and not ok:
        time.sleep(0.005)
        dets = _by_x(rig.run([("alice", LEFT), ("bob", RIGHT)]))
        assert not dets[1].allow
        ok = dets[0].allow
    assert ok
    rig.close()


def test_embedding_failure_keeps_the_face_masked() -> None:
    # async mode swallows a failed embedding as a miss (sync mode lets it reach the lane, which PROTECTs)
    arig = Rig(async_embed=True)
    arig.backend.fail_embed = True
    end = time.monotonic() + 0.5
    while time.monotonic() < end:
        (d,) = arig.run([("alice", LEFT)])
        assert not d.allow
        time.sleep(0.005)
    arig.close()


def test_close_with_pending_jobs_does_not_hang() -> None:
    rig = Rig(async_embed=True)
    rig.run([("alice", LEFT)])
    t0 = time.monotonic()
    rig.close()
    assert time.monotonic() - t0 < 1.0


# --------------------------------------------------------------------------- category config
def test_disabled_category_without_allowlist_yields_nothing() -> None:
    rig = Rig(allowed=(), enabled=False)
    assert rig.run([("alice", LEFT)]) == []
    rig.close()


def test_disabled_category_with_allowlist_still_gives_mouth_for_enrolled_only() -> None:
    rig = Rig(enabled=False)
    for _ in range(14):
        dets = rig.run([("alice", LEFT), ("bob", RIGHT)])
    assert len(dets) == 1 and dets[0].allow and "mouth" in dets[0].aux_quads
    rig.close()


def test_mask_when_unsure_lowers_the_score_threshold() -> None:
    sure = Rig(mask_when_unsure=False)
    sure.run([("alice", LEFT)])
    assert sure.backend.thresholds[-1] == pytest.approx(0.6)
    unsure = Rig(mask_when_unsure=True)
    unsure.run([("alice", LEFT)])
    assert unsure.backend.thresholds[-1] == pytest.approx(0.5)
    sure.close()
    unsure.close()


# --------------------------------------------------------------------------- geometry
def test_box_is_dilated_20_percent_in_frame_coordinates() -> None:
    rig = Rig()
    (d,) = rig.run([("bob", (400, 200, 500, 300))])  # 100 x 100 in the 1280x720 frame
    x1, y1, x2, y2 = d.quad.bbox()
    assert (x2 - x1, y2 - y1) == pytest.approx((120.0, 120.0), abs=0.5)
    assert ((x1 + x2) / 2, (y1 + y2) / 2) == pytest.approx((450.0, 250.0), abs=0.5)
    rig.close()


def test_dilated_box_is_clamped_to_the_canvas() -> None:
    rig = Rig()
    (d,) = rig.run([("bob", (1200, 640, 1280, 720))])
    x1, y1, x2, y2 = d.quad.bbox()
    assert 0 <= x1 and x2 <= W and 0 <= y1 and y2 <= H
    rig.close()


def test_mouth_quad_geometry() -> None:
    lm = np.array([[40, 40], [80, 40], [60, 60], [45, 80], [75, 80]], dtype=np.float32)
    q = mouth_quad(lm)
    assert q is not None
    x1, y1, x2, y2 = q.bbox()
    d = 30.0
    assert (x2 - x1) == pytest.approx(1.4 * d, abs=0.01) and (y2 - y1) == pytest.approx(1.2 * d, abs=0.01)
    assert ((x1 + x2) / 2, (y1 + y2) / 2) == pytest.approx((60.0, 80.0), abs=0.01)


def test_mouth_quad_follows_head_tilt_and_rejects_degenerate_landmarks() -> None:
    a = np.array([50.0, 100.0])
    b = np.array([50.0 + 30 * np.cos(np.pi / 6), 100.0 + 30 * np.sin(np.pi / 6)])  # 30 degrees clockwise
    lm = np.zeros((5, 2), np.float32)
    lm[3], lm[4] = a, b
    q = mouth_quad(lm)
    assert q is not None
    p = np.array(q.pts)
    assert np.linalg.norm(p[1] - p[0]) == pytest.approx(1.4 * 30, abs=0.01)
    assert np.linalg.norm(p[3] - p[0]) == pytest.approx(1.2 * 30, abs=0.01)
    lm[4] = lm[3]
    assert mouth_quad(lm) is None


def test_allowlisted_detection_mouth_matches_landmarks() -> None:
    rig = Rig()
    for _ in range(12):
        (d,) = rig.run([("alice", LEFT)])
    assert d.allow
    x1, y1, x2, y2 = d.aux_quads["mouth"].bbox()
    cx = (LEFT[0] + LEFT[2]) / 2
    cy = (LEFT[1] + LEFT[3]) / 2 + 0.25 * (LEFT[3] - LEFT[1])  # mouth-corner line in the fake's landmarks
    assert ((x1 + x2) / 2, (y1 + y2) / 2) == pytest.approx((cx, cy), abs=1.5)
    rig.close()


# --------------------------------------------------------------------------- low light, tracks, privacy
def test_dark_frame_goes_through_clahe_without_breaking() -> None:
    rig = Rig()
    rig.image = np.full((H, W, 3), 20, np.uint8)
    for _ in range(12):
        (d,) = rig.run([("alice", LEFT)])
    assert d.allow
    rig.close()


def test_track_state_is_garbage_collected() -> None:
    rig = Rig()
    for _ in range(8):
        rig.run([("bob", LEFT)])
    for _ in range(60):  # long absence: the tracker forgets the track and so must we
        rig.run([])
    assert len(rig.det._state) == 0 and len(rig.det._votes) == 0
    rig.close()


def test_detections_carry_codes_only_and_logs_have_no_embeddings(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    rig = Rig(async_embed=True)
    rig.backend.fail_embed = True
    for _ in range(5):
        dets = rig.run([("alice", LEFT)])
        time.sleep(0.01)
    rig.close()
    for d in dets:
        assert set(d.extra) == {"reason"}
        assert not isinstance(d.extra["reason"], (list, tuple, np.ndarray))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "[" not in text and "array" not in text.lower()
