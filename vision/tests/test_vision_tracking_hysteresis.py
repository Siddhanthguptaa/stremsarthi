"""Hysteresis on tracks, per-track votes and the ByteTrack + hysteresis tracker (CPU, no models)."""

from __future__ import annotations

import pytest

from contracts import Category, Config, Detection, Quad
from vision.tracking import (
    ByteTrackHold,
    Observation,
    TrackHysteresis,
    TrackVotes,
)


def cfg(**cats: dict):
    c = Config(profile="dev", _env_file=None)
    for name, update in cats.items():
        cat = Category(name)
        c.categories[cat] = c.categories[cat].model_copy(update=update)
    return c


def det(cat: Category = Category.AADHAAR, conf: float = 0.9, box=(300, 200, 600, 400), pts: float = 0.0):
    return Detection("d", cat, Quad.from_xyxy(*box), conf, 0, pts, 0.0, extra={"reason": "t"})


def obs(tid: int = 1, **kw) -> Observation:
    return Observation(tid, det(**kw))


# ---------------------------------------------------------------- TrackHysteresis
def test_k_confirm_two_hits_unless_confident_or_unsure() -> None:
    c = cfg(pin_code={"mask_when_unsure": False})
    h = TrackHysteresis(c.categories)
    weak = Category.PIN_CODE
    assert h.update([obs(1, cat=weak, conf=0.6)], 0.0) == []  # first hit: not confirmed
    (td,) = h.update([obs(1, cat=weak, conf=0.6)], 0.25)  # second hit confirms
    assert td.track_id == 1 and not td.held
    h2 = TrackHysteresis(c.categories)
    assert len(h2.update([obs(1, cat=weak, conf=0.85)], 0.0)) == 1  # confident: at once
    h3 = TrackHysteresis(c.categories)
    assert len(h3.update([obs(1, cat=Category.AADHAAR, conf=0.4)], 0.0)) == 1  # mask_when_unsure: at once
    h4 = TrackHysteresis(c.categories, k_confirm=3)
    assert h4.update([obs(1, cat=weak, conf=0.6)], 0.0) == []
    assert h4.update([obs(1, cat=weak, conf=0.6)], 0.1) == []
    assert len(h4.update([obs(1, cat=weak, conf=0.6)], 0.2)) == 1


@pytest.mark.parametrize(
    ("cat", "hold"),
    [
        (Category.FACE, 0.8),
        (Category.AADHAAR, 2.0),
        (Category.PAN, 2.0),
        (Category.UPI_QR, 2.0),
        (Category.EMAIL, 1.0),
    ],
)
def test_a_vanished_track_is_held_for_its_category_hold_time(cat: Category, hold: float) -> None:
    h = TrackHysteresis(cfg().categories)
    h.update([obs(7, cat=cat)], 10.0)
    (td,) = h.update([], 10.0 + hold - 0.01)
    assert td.held and td.track_id == 7 and td.last_seen_pts == 10.0
    assert h.update([], 10.0 + hold + 0.05) == []


def test_held_track_keeps_its_last_detection_and_velocity() -> None:
    h = TrackHysteresis(cfg().categories)
    d = det()
    h.update([Observation(3, d, (120.0, -30.0))], 5.0)
    (td,) = h.update([], 5.5)
    assert td.detection is d and td.velocity == (120.0, -30.0) and td.held


def test_state_is_garbage_collected_after_three_holds() -> None:
    h = TrackHysteresis(cfg().categories)
    h.update([obs(1, cat=Category.FACE)], 0.0)
    h.update([], 2.3)  # 2.3 s < 3 x 0.8 s = 2.4 s: remembered (but not masked: past the hold)
    assert len(h) == 1
    h.update([], 2.5)
    assert len(h) == 0
    unconfirmed = TrackHysteresis(cfg(pin_code={"mask_when_unsure": False}).categories)
    unconfirmed.update([obs(1, cat=Category.PIN_CODE, conf=0.5)], 0.0)
    assert len(unconfirmed) == 1
    unconfirmed.update([], 3.5)
    assert len(unconfirmed) == 0  # an entry that never confirmed goes too


def test_re_sighting_after_the_hold_reuses_the_entry_and_unholds() -> None:
    h = TrackHysteresis(cfg().categories)
    h.update([obs(1)], 0.0)
    assert h.update([], 1.0)[0].held
    (td,) = h.update([obs(1)], 1.5)
    assert not td.held and td.last_seen_pts == 1.5


def test_hold_frames_and_overrides() -> None:
    h = TrackHysteresis(cfg().categories)
    assert h.hold_frames(Category.AADHAAR, 10.0) == 20
    assert h.hold_frames(Category.FACE, 10.0) == 8
    assert h.hold_frames(Category.EMAIL, 4.0) == 4
    assert h.max_hold_s == 2.0
    custom = TrackHysteresis(cfg().categories, hold_s={Category.AADHAAR: 0.5}, default_hold_s=0.2)
    assert custom.hold_for(Category.AADHAAR) == 0.5 and custom.hold_for(Category.PAN) == 0.2
    with pytest.raises(ValueError):
        TrackHysteresis(cfg().categories, k_confirm=0)
    with pytest.raises(ValueError):
        TrackHysteresis(cfg().categories, gc_factor=0.5)


def test_source_clock_restart_clears_state() -> None:
    h = TrackHysteresis(cfg().categories)
    h.update([obs(1)], 100.0)
    assert h.update([], 0.5) == [] and len(h) == 0


# ---------------------------------------------------------------- TrackVotes
def test_votes_need_five_of_seven_and_three_samples() -> None:
    v = TrackVotes(7, 5)
    assert not v.allowed(1)  # unknown track: masked
    for _ in range(4):
        v.add(1, True)
    assert not v.allowed(1)  # 4 of 4 is not 5
    v.add(1, True)
    assert v.allowed(1) and v.matches(1) == 5 and v.samples(1) == 5
    v.add(1, False)
    v.add(1, False)
    assert v.allowed(1)  # 5 of 7
    v.add(1, False)
    assert not v.allowed(1)  # window is now T T T F F F plus one more T dropped: 4 of 7
    short = TrackVotes(window=3, min_votes=2, min_samples=3)
    short.add(9, True)
    short.add(9, True)
    assert not short.allowed(9)  # two matches but only two samples
    short.add(9, True)
    assert short.allowed(9)


def test_three_misses_in_a_row_revoke_the_allowance() -> None:
    v = TrackVotes(window=9, min_votes=5)
    for _ in range(9):
        v.add(1, True)
    assert v.allowed(1)
    v.add(1, False)
    v.add(1, False)
    assert v.allowed(1)  # 7 of 9
    v.add(1, False)
    assert not v.allowed(1)  # still 6 of 9 matches, but the last three missed: masked again


def test_votes_are_per_track_a_neighbour_never_lends_votes() -> None:
    v = TrackVotes(7, 5)
    for _ in range(7):
        v.add(1, True)  # the enrolled person
        v.add(2, False)  # a stranger standing next to them
    assert v.allowed(1) and not v.allowed(2)
    v.add(3, True)
    assert not v.allowed(3)  # a new track starts masked


def test_votes_prune_forget_reset_and_config() -> None:
    c = Config(profile="dev", _env_file=None)
    v = TrackVotes.from_config(c)
    assert (v.window, v.min_votes) == (c.vision.face_vote_window, c.vision.face_vote_min) == (7, 5)
    for tid in (1, 2, 3):
        v.add(tid, True)
    v.prune([1, 3])
    assert len(v) == 2 and v.samples(2) == 0
    v.forget(1)
    assert len(v) == 1
    v.reset()
    assert len(v) == 0
    assert TrackVotes(window=3, min_votes=5).min_votes == 3  # a minimum above the window is clamped
    with pytest.raises(ValueError):
        TrackVotes(window=0)


# ---------------------------------------------------------------- ByteTrackHold
def test_tracker_keeps_one_id_and_holds_through_missed_detections() -> None:
    t = ByteTrackHold(cfg().categories, frame_rate=10.0)
    ids = set()
    for k in range(30):
        pts = k * 0.1
        dets = [] if k in (8, 9, 17, 18, 19) else [det(box=(300, 200, 600, 400), pts=pts)]
        out = t.update(dets, pts)
        assert len(out) == 1  # AADHAAR holds 2.0 s: never a gap
        ids.add(out[0].track_id)
        assert out[0].held is (k in (8, 9, 17, 18, 19))
    assert ids == {1}


def test_tracker_holds_for_the_category_hold_and_forgets_after() -> None:
    t = ByteTrackHold(cfg().categories, frame_rate=10.0)
    for k in range(5):
        t.update([det(Category.FACE, box=(100, 100, 200, 200))], k * 0.1)
    assert len(t.update([], 0.4 + 0.7)) == 1
    assert t.update([], 0.4 + 0.9) == []  # faces hold 0.8 s


def test_lost_buffer_is_never_shorter_than_the_longest_hold() -> None:
    for rate in (10.0, 4.0):
        t = ByteTrackHold(cfg().categories, frame_rate=rate)
        assert t.tracker.max_time_lost_s >= t.hysteresis.max_hold_s
        assert t.tracker.max_time_lost_s >= 1.5  # spec: lost_track_buffer 15 runs at the lane rate... or more
    t = ByteTrackHold(cfg().categories, frame_rate=10.0)
    for k in range(3):
        t.update([det(box=(300, 200, 600, 400))], k * 0.1)
    t.update([], 1.9)  # held 1.7 s later
    (td,) = t.update([det(box=(302, 201, 602, 401))], 2.0)  # found again: same track, same id
    assert td.track_id == 1 and not td.held


def test_tracker_reports_velocity_of_a_moving_object() -> None:
    t = ByteTrackHold(cfg().categories, frame_rate=10.0)
    for k in range(15):
        x = 100 + 20 * k
        (td,) = t.update([det(box=(x, 200, x + 300, 400))], k * 0.1)
    assert td.velocity[0] == pytest.approx(200.0, rel=0.05) and abs(td.velocity[1]) < 5
    assert t.live_tracks == 1
    t.reset()
    assert t.live_tracks == 0


def test_two_categories_never_share_a_track() -> None:
    t = ByteTrackHold(cfg().categories, frame_rate=10.0)
    box = (300, 200, 600, 400)
    out = t.update([det(Category.AADHAAR, box=box), det(Category.PAN, box=box)], 0.0)
    assert len(out) == 2 and out[0].track_id != out[1].track_id
    out2 = t.update([det(Category.PAN, box=box), det(Category.AADHAAR, box=box)], 0.1)
    assert {x.detection.category: x.track_id for x in out2} == {x.detection.category: x.track_id for x in out}
