"""In-house ByteTrack on synthetic trajectories (CPU, numpy only)."""

from __future__ import annotations

import random

import numpy as np
import pytest

from vision.tracking import ByteTracker, ByteTrackParams, TrackState, iou_matrix


def box(cx: float, cy: float, w: float = 100.0, h: float = 60.0) -> tuple[float, float, float, float]:
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def tracker(rate: float = 10.0, **kw) -> ByteTracker:
    return ByteTracker(ByteTrackParams(frame_rate=rate, **kw))


def step(tr: ByteTracker, k: int, dets, scores=None, dt: float = 0.1, **kw):
    scores = [0.9] * len(dets) if scores is None else scores
    return tr.update(np.array(dets).reshape(-1, 4), scores, k * dt, **kw)


def test_iou_matrix() -> None:
    a = np.array([[0, 0, 10, 10], [0, 0, 10, 10.0]])
    b = np.array([[0, 0, 10, 10], [5, 0, 15, 10], [20, 20, 30, 30.0]])
    m = iou_matrix(a, b)
    assert m.shape == (2, 3)
    assert m[0, 0] == pytest.approx(1.0)
    assert m[0, 1] == pytest.approx(50 / 150)
    assert m[0, 2] == 0.0
    assert iou_matrix(np.zeros((0, 4)), b).shape == (0, 3)


def test_constant_velocity_keeps_one_id_and_estimates_velocity() -> None:
    tr = tracker()
    ids = set()
    for k in range(40):
        (t,) = step(tr, k, [box(100 + 20 * k, 300)])  # 200 px/s at 10 Hz
        ids.add(t.track_id)
        assert t.state is (TrackState.NEW if k == 0 else TrackState.TRACKED)
        if k == 10:
            assert t.velocity_px_s[0] == pytest.approx(200.0, rel=0.08)
        if k == 30:
            assert t.velocity_px_s[0] == pytest.approx(200.0, rel=0.02)
            assert abs(t.velocity_px_s[1]) < 3.0
    assert ids == {1}
    cx, cy = t.center
    assert cx == pytest.approx(100 + 20 * 39, abs=3.0) and cy == pytest.approx(300, abs=2.0)


def test_velocity_in_px_per_s_does_not_depend_on_the_lane_rate_or_jitter() -> None:
    slow = tracker(rate=4.0)
    for k in range(16):
        (t,) = step(slow, k, [box(100 + 50 * k, 300)], dt=0.25)  # 200 px/s at 4 Hz
    assert t.velocity_px_s[0] == pytest.approx(200.0, rel=0.05)
    rng = random.Random(3)
    jit = tracker()
    now = 0.0
    for _ in range(40):
        now += rng.uniform(0.08, 0.12)
        (t,) = jit.update(np.array([box(100 + 200 * now, 300)]), [0.9], now)
    assert t.velocity_px_s[0] == pytest.approx(200.0, rel=0.06)


def test_crossing_tracks_keep_their_identity() -> None:
    tr = tracker()
    for k in range(41):
        big = box(100 + 25 * k, 300, 160, 100)
        small = box(1100 - 25 * k, 300, 70, 50)  # passes straight through the big one at k = 20
        out = step(tr, k, [big, small], payloads=["big", "small"], labels=None)
        assert len(out) == 2
        by_payload = {t.payload: t.track_id for t in out}
        if k == 0:
            first = dict(by_payload)
        assert by_payload == first, f"identity swapped at step {k}"
    assert len(first) == 2 and first["big"] != first["small"]


def test_occlusion_of_ten_runs_keeps_the_id_and_predicts_through() -> None:
    tr = tracker()
    for k in range(10):
        (t,) = step(tr, k, [box(100 + 20 * k, 300)])
    tid = t.track_id
    for k in range(10, 20):  # hidden for 10 runs (1 s, inside the 1.5 s lost buffer)
        assert step(tr, k, []) == []
        (lost,) = tr.tracks
        assert lost.state is TrackState.LOST and lost.track_id == tid and not lost.observed
        assert lost.center[0] == pytest.approx(100 + 20 * k, abs=12.0)  # keeps moving with its velocity
    (back,) = step(tr, 20, [box(100 + 20 * 20, 300)])
    assert back.track_id == tid and back.state is TrackState.TRACKED and back.observed
    assert back.hits == 11


def test_occlusion_longer_than_the_buffer_removes_the_track() -> None:
    tr = tracker()
    for k in range(10):
        step(tr, k, [box(100, 300)])
    for k in range(10, 26):  # 1.6 s
        step(tr, k, [])
    assert tr.tracks == []
    (t,) = step(tr, 26, [box(100, 300)])
    assert t.track_id == 2 and t.state is TrackState.NEW


def test_appear_and_disappear() -> None:
    tr = tracker()
    for k in range(3):
        assert step(tr, k, []) == []
    (t,) = step(tr, 3, [box(500, 200)])
    assert t.state is TrackState.NEW and t.hits == 1
    (t,) = step(tr, 4, [box(500, 200)])
    assert t.state is TrackState.TRACKED and t.hits == 2
    seen = []
    for k in range(5, 30):
        seen.append(len(step(tr, k, [])))
    assert sum(seen) == 0  # nothing is reported once it is gone
    assert tr.tracks == []  # and it is forgotten after the lost buffer
    assert [] == [t for t in tr.tracks if t.state is TrackState.REMOVED]


def test_seen_once_missed_once_seen_again_keeps_the_id() -> None:
    tr = tracker()
    (a,) = step(tr, 0, [box(300, 300)])
    assert step(tr, 1, []) == []
    assert tr.tracks[0].state is TrackState.LOST  # a NEW track that misses is held, not dropped
    (b,) = step(tr, 2, [box(302, 300)])
    assert b.track_id == a.track_id and b.state is TrackState.TRACKED


def test_low_score_detection_keeps_a_track_alive_but_never_starts_one() -> None:
    tr = tracker()
    for k in range(4):
        step(tr, k, [box(300, 300)], [0.9])
    (t,) = step(tr, 4, [box(302, 300)], [0.15])  # between low_threshold 0.1 and activation 0.25
    assert t.track_id == 1 and t.observed and t.hits == 5
    fresh = tracker()
    assert step(fresh, 0, [box(300, 300)], [0.15]) == []  # cannot start a track
    assert step(fresh, 1, [box(300, 300)], [0.05]) == []  # below low_threshold: ignored
    assert fresh.tracks == []
    eager = tracker(new_track_threshold=0.0, low_threshold=0.0)
    assert len(step(eager, 0, [box(300, 300)], [0.05])) == 1  # the planner configures it this way


def test_labels_gate_the_association() -> None:
    tr = tracker()
    out = step(tr, 0, [box(300, 300), box(300, 300)], labels=["aadhaar", "pan"])
    assert len(out) == 2
    ids = {t.label: t.track_id for t in out}
    out = step(tr, 1, [box(300, 300), box(300, 300)], labels=["pan", "aadhaar"])  # order swapped
    assert {t.label: t.track_id for t in out} == ids
    out = step(tr, 2, [box(300, 300)], labels=["pan"])
    assert [t.track_id for t in out] == [ids["pan"]]


def test_payload_is_the_matched_detection_object() -> None:
    tr = tracker()
    first, second = object(), object()
    (t,) = step(tr, 0, [box(300, 300)], payloads=[first])
    assert t.payload is first
    (t,) = step(tr, 1, [box(305, 300)], payloads=[second])
    assert t.payload is second


def test_is_deterministic() -> None:
    def run() -> list[tuple[int, tuple[float, float, float, float]]]:
        rng = random.Random(11)
        tr = tracker()
        out = []
        for k in range(60):
            dets = [box(100 + 15 * k + rng.gauss(0, 2), 200 + rng.gauss(0, 2)), box(900 - 10 * k, 500)]
            if rng.random() < 0.15:
                dets.pop(rng.randrange(len(dets)))
            out.extend((t.track_id, t.box_xyxy) for t in step(tr, k, dets))
        return out

    assert run() == run()


def test_pts_going_back_resets_the_tracker() -> None:
    tr = tracker()
    for k in range(5):
        step(tr, k, [box(300, 300)])
    (t,) = tr.update(np.array([box(300, 300)]), [0.9], 0.0 - 50.0)
    assert t.state is TrackState.NEW and len(tr.tracks) == 1


def test_validation_and_empty_input() -> None:
    tr = tracker()
    with pytest.raises(ValueError):
        tr.update(np.zeros((2, 4)), [0.9], 0.0)
    with pytest.raises(ValueError):
        tr.update(np.zeros((1, 4)), [0.9], 0.0, labels=["a", "b"])
    with pytest.raises(ValueError):
        ByteTrackParams(frame_rate=0)
    assert tr.update(np.zeros((0, 4)), [], 0.0) == []
    tr.update(np.array([box(10, 10)]), [0.9], 1.0)
    tr.reset()
    assert tr.tracks == []


def test_degenerate_boxes_do_not_break_the_filter() -> None:
    tr = tracker()
    for k in range(5):
        out = step(tr, k, [(100.0, 100.0, 100.0, 100.0)])  # zero area
        assert len(out) == 1 and np.isfinite(out[0].kf).all()


def test_reset_clears_the_clock_and_forget_label_keeps_it() -> None:
    tr = tracker()
    for k in range(5):
        step(tr, k, [box(100, 100), box(300, 300)], labels=["a", "b"])
    assert len(tr.tracks) == 2
    last = tr._last_pts
    tr.forget_label("a")
    assert [t.label for t in tr.tracks] == ["b"], "only that label's tracks go"
    assert tr._last_pts == last, "forget_label leaves the clock alone"
    tr.reset()
    assert tr.tracks == [] and tr._last_pts == -float("inf"), "reset clears the clock too"
    # a restarted (earlier) source clock after reset must start fresh tracks, not be read as a back-jump
    step(tr, 0, [box(100, 100)], labels=["a"])
    assert len(tr.tracks) == 1
