"""Planner: an allowlisted face releases the held mask of its own track and nothing else (CPU, no models)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from contracts import Category, Config, Detection, Lane, Quad
from vision.planner import MaskPlanner, TrackedDetection

FACE_BOX = (300.0, 200.0, 400.0, 320.0)
HZ = 10.0
TRACKERS = ("bytetrack", "iou")


def _planner(tracker: str = "bytetrack", **face: object) -> MaskPlanner:
    cfg = Config(profile="dev", _env_file=None)
    if face:
        cfg.categories[Category.FACE] = cfg.categories[Category.FACE].model_copy(update=face)
    return MaskPlanner(Lane.FAST, cfg, tracker=tracker)  # type: ignore[arg-type]


def _face(
    box: tuple[float, float, float, float] = FACE_BOX,
    *,
    tid: int | None = 7,
    allow: bool = False,
    detector: str = "faces",
    conf: float = 0.9,
) -> Detection:
    return Detection(
        detector,
        Category.FACE,
        Quad.from_xyxy(*box),
        conf,
        0,
        0.0,
        0.0,
        track_id=tid,
        allow=allow,
        extra={"reason": "face_allowlisted" if allow else "face_unverified"},
    )


def _card(box: tuple[float, float, float, float] = FACE_BOX, tid: int | None = 7) -> Detection:
    return Detection(
        "faces", Category.AADHAAR, Quad.from_xyxy(*box), 0.9, 0, 0.0, 0.0, track_id=tid, extra={"reason": "x"}
    )


def _cats(reqs: Sequence) -> list[Category]:
    return sorted(r.category for r in reqs)


@pytest.mark.parametrize("tracker", TRACKERS)
def test_held_mask_persists_without_allow(tracker: str) -> None:
    """Baseline (unchanged): with no allow=True detection the face stays masked for its 0.8 s hold only."""
    p = _planner(tracker)
    assert len(p.plan(0.0, [_face()])[0]) == 1
    for k in range(1, 8):  # 0.1 .. 0.7 s
        assert len(p.plan(k / HZ, [])[0]) == 1, k
    assert p.plan(1.0, [])[0] == ()


@pytest.mark.parametrize("tracker", TRACKERS)
def test_allow_releases_held_mask_of_same_track(tracker: str) -> None:
    p = _planner(tracker)
    p.plan(0.0, [_face()])
    reqs, aux = p.plan(0.1, [_face(allow=True)])
    assert reqs == ()
    for k in range(2, 10):  # nothing comes back inside the old hold window
        assert p.plan(k / HZ, [_face(allow=True)])[0] == ()
    assert p.plan(1.0, [])[0] == ()


@pytest.mark.parametrize("tracker", TRACKERS)
def test_allow_on_a_different_track_does_not_release(tracker: str) -> None:
    p = _planner(tracker)
    p.plan(0.0, [_face(tid=7)])
    reqs, _ = p.plan(0.1, [_face(tid=8, allow=True)])  # same place, other detector track
    assert _cats(reqs) == [Category.FACE] and reqs[0].reason.endswith(":held")
    reqs, _ = p.plan(0.2, [_face(tid=None, allow=True)])  # no track id at all
    assert len(reqs) == 1
    far = (900.0, 200.0, 1000.0, 320.0)
    reqs, _ = p.plan(0.3, [_face(far, tid=7, allow=True)])  # right id, but not on the held mask
    assert len(reqs) == 1
    reqs, _ = p.plan(0.4, [_face(tid=7, allow=True, detector="other")])  # another detector's id 7
    assert len(reqs) == 1


def test_allow_does_not_release_a_track_that_is_not_the_owner() -> None:
    """Two masked faces side by side: allowing one releases only that one."""
    p = _planner()
    mine, other = (300.0, 200.0, 400.0, 320.0), (700.0, 200.0, 800.0, 320.0)
    p.plan(0.0, [_face(mine, tid=7), _face(other, tid=9)])
    reqs, _ = p.plan(0.1, [_face(mine, tid=7, allow=True)])
    assert len(reqs) == 1
    assert reqs[0].quads[0].bbox()[0] > 600  # the stranger's mask, held, is still there
    assert reqs[0].reason.endswith(":held")


@pytest.mark.parametrize("tracker", TRACKERS)
def test_allow_never_releases_another_category(tracker: str) -> None:
    """A card on the face, even with the same detector track id, stays masked."""
    p = _planner(tracker)
    p.plan(0.0, [_face(), _card()])
    reqs, _ = p.plan(0.1, [_face(allow=True)])
    assert _cats(reqs) == [Category.AADHAAR]
    assert reqs[0].reason.endswith(":held")
    # (the contract already rejects allow=True on a non-face detection)
    for k in range(2, 20):  # the card keeps its full 2 s hold
        assert _cats(p.plan(k / HZ, [_face(allow=True)])[0]) == [Category.AADHAAR], k


@pytest.mark.parametrize("tracker", TRACKERS)
def test_swap_to_stranger_is_masked_again_immediately(tracker: str) -> None:
    p = _planner(tracker)
    p.plan(0.0, [_face()])
    assert p.plan(0.1, [_face(allow=True)])[0] == ()
    reqs, _ = p.plan(0.2, [_face(allow=False)])  # votes revoked: same track, allow=False again
    assert len(reqs) == 1 and not reqs[0].reason.endswith(":held")
    # and it stays masked afterwards, including the hold
    assert len(p.plan(0.3, [])[0]) == 1
    assert len(p.plan(0.9, [])[0]) == 1


@pytest.mark.parametrize("tracker", TRACKERS)
def test_new_unmatched_stranger_on_released_track_is_masked_at_once(tracker: str) -> None:
    """A stranger appears on the released face with a different box and a new detector track id."""
    p = _planner(tracker)
    p.plan(0.0, [_face()])
    p.plan(0.1, [_face(allow=True)])
    stranger = _face((330.0, 230.0, 430.0, 350.0), tid=99)
    reqs, _ = p.plan(0.2, [_face(allow=True), stranger])
    assert reqs and any(
        r.quads[0].bbox()[0] <= 330.0 and r.quads[0].bbox()[2] >= 430.0 for r in reqs
    )  # something covers the stranger


class _StubTracker:
    """Sees the face once, then only ever holds it: it never associates a later detection, so the
    planner's own guard against a stranger on a released entry is what is tested."""

    def __init__(self) -> None:
        self.det: Detection | None = None

    def update(self, detections: Sequence[Detection], pts: float) -> list[TrackedDetection]:
        if self.det is None and detections:
            self.det = detections[0]
            return [TrackedDetection(self.det, pts, False, 1)]
        if self.det is None:
            return []
        return [TrackedDetection(self.det, 0.0, True, 1)]

    def reset(self) -> None:
        pass


def test_stranger_overlapping_a_released_entry_unreleases_it() -> None:
    p = MaskPlanner(Lane.FAST, Config(profile="dev", _env_file=None), tracker=_StubTracker())
    assert len(p.plan(0.0, [_face()])[0]) == 1
    assert p.plan(0.1, [_face(allow=True)])[0] == ()  # released
    assert p.plan(0.15, [_face(allow=True), _face((900, 200, 1000, 320), tid=42)])[0] == ()  # far away
    # a face that must be masked appears on the entry: the mask is back at once, still held
    reqs, _ = p.plan(0.2, [_face(allow=True), _face(tid=42)])
    assert len(reqs) == 1 and reqs[0].reason.endswith(":held")
    # a stranger below the confidence floor is filtered like anywhere else and does not restore it
    p2 = MaskPlanner(Lane.FAST, Config(profile="dev", _env_file=None), tracker=_StubTracker())
    p2.plan(0.0, [_face()])
    p2.plan(0.1, [_face(allow=True)])
    assert p2.plan(0.2, [_face(allow=True), _face(tid=42, conf=0.05)])[0] == ()


def test_released_state_is_bounded_and_forgotten() -> None:
    p = _planner()
    p.plan(0.0, [_face()])
    p.plan(0.1, [_face(allow=True)])
    assert p._released and p._owner
    p.plan(5.0, [])  # the track is long gone
    assert not p._released and not p._owner


def test_hold_and_failsafe_unchanged_for_other_faces_and_no_owner() -> None:
    """An allow=True face that never had a masked track changes nothing; a masked stranger keeps its hold."""
    p = _planner()
    assert p.plan(0.0, [_face(allow=True)])[0] == ()
    p.plan(0.1, [_face(tid=3)])
    for k in range(2, 8):
        reqs, _ = p.plan(k / HZ, [_face(FACE_BOX, tid=7, allow=True)])
        assert len(reqs) == 1, k  # tid 3 is not tid 7
    assert p.plan(1.0, [])[0] == ()


def test_fallback_tracker_switch_clears_release_state() -> None:
    p = _planner()
    p.plan(0.0, [_face()])
    p.plan(0.1, [_face(allow=True)])
    assert p._released

    def boom(*_a: object, **_k: object) -> list:
        raise RuntimeError("tracker bug")

    p.tracker.update = boom  # type: ignore[method-assign]
    reqs, _ = p.plan(0.2, [_face()])  # IoU fallback takes over and masks the face
    assert p.fell_back and len(reqs) == 1 and not p._released
