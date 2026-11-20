"""Deadline engine on the CPU with synthetic frames.

The first block is ported from IRL Shield tests/test_deadline_engine.py (MIT) to the multi-lane
``LaneResult`` API. Time: frame 0 arrives at 10.0, delay 1.0 s unless stated, so the deadline of
frame i is 11.0 + i/30.
"""

import dataclasses

import numpy as np
import pytest

from contracts import Config, HoldCommand, HoldSource, Lane, MaskKind
from engine.control import HOLD, LIVE, PROTECT
from engine.deadline import DeadlineEngine
from engine.motion import IdentityMotion
from engine.testing import FPS, T0, feed, lane_result, make_engine, make_frame, region_request

FAST, ACC, SLOW = Lane.FAST, Lane.ACCURATE, Lane.SLOW
ALL_LANES = (FAST, ACC)


def dl(seq: int, delay: float = 1.0) -> float:
    return T0 + seq / FPS + delay


class CutMotion:
    """Every frame is its own scene: projection works only from a frame to itself."""

    def add(self, index, pixels):
        pass

    def project_many(self, quads, source, target, deadline=None):
        return quads if source == target else None


class TranslateMotion:
    """The scene slides +10 px in x per frame."""

    def add(self, index, pixels):
        pass

    def project_many(self, quads, source, target, deadline=None):
        dx = 10.0 * (target - source)
        return [[[x + dx, y] for x, y in quad] for quad in quads]


# ---------------------------------------------------------------- ported reference behaviour


@pytest.mark.parametrize(
    ("acc_seq", "acc_done", "state", "reason"),
    [
        (12, 10.9, LIVE, "covered"),  # at the maximum age
        (13, 10.9, PROTECT, "stale-accurate"),  # one frame too old
        (0, 11.001, PROTECT, "missing-accurate"),  # finished after the deadline
    ],
)
def test_late_results_and_age_limits_cannot_claim_live_coverage(acc_seq, acc_done, state, reason):
    engine = make_engine()
    engine.submit(make_frame(0), now=10)
    engine.submit_result(lane_result(FAST, 0, 10.1))
    engine.submit_result(lane_result(ACC, acc_seq, acc_done))
    engine.end(0)
    (decision,) = engine.poll(now=11)
    assert (decision.state, decision.reason) == (state, reason)
    assert decision.live == (state == LIVE)


def test_overdue_output_is_bounded_and_hold_can_intervene_between_batches():
    engine = make_engine(max_poll_frames=2)
    engine.submit(make_frame(0), now=10)
    engine.end(20)
    assert len(engine.poll(now=20)) == 2
    engine.hold(HoldSource.HOTKEY, True, now=20)
    second = engine.poll(now=20)
    assert [d.seq for d in second] == [2, 3]
    assert all(d.state == HOLD and d.presentation == "slate" and d.audio_silent for d in second)


def test_ingest_after_its_deadline_is_a_gap_even_before_poll():
    engine = make_engine()
    for lane in ALL_LANES:
        engine.submit_result(lane_result(lane, 0, 10.1))
    assert engine.submit(make_frame(0), now=10)
    assert not engine.submit(make_frame(1), now=12)  # deadline was 11.033
    engine.end(1)
    result = engine.poll(now=12)
    assert result[0].state == LIVE
    assert (result[1].state, result[1].reason, result[1].gap) == (PROTECT, "ingest-gap", True)
    assert engine.stats["late_ingest"] == 1


def test_poll_just_before_deadline_does_not_emit_or_raise():
    engine = make_engine()
    engine.submit(make_frame(0), now=10)
    engine.end(0)
    assert engine.poll(now=11 - 5e-10) == []
    assert len(engine.poll(now=11)) == 1


def test_overrun_bounds_frame_memory_and_protects_evicted_input():
    engine = make_engine(delay=0.1, max_buffer_frames=3, max_poll_frames=8)
    for seq in range(8):
        engine.submit(make_frame(seq, value=seq * 20), now=T0 + seq / FPS)
    assert engine.stats["buffered_frames"] <= 3
    engine.end(7)
    result = engine.poll(now=11)
    assert [d.seq for d in result] == list(range(8))
    assert all(d.state == PROTECT and d.reason == "ingest-gap" for d in result[:5])
    assert engine.stats["buffer_evictions"] == 5


def test_empty_result_from_a_disconnected_scene_is_not_coverage():
    engine = make_engine(motion=CutMotion())
    for lane in ALL_LANES:
        engine.submit_result(lane_result(lane, 0, 10.1))
    engine.submit(make_frame(0, value=120), now=10)
    engine.submit(make_frame(1, value=220), now=T0 + 1 / FPS)
    engine.end(1)
    result = engine.poll(now=dl(1))
    assert [d.state for d in result] == [LIVE, PROTECT]
    assert result[1].reason == "unresolved-motion-fast"


@pytest.mark.parametrize("fps", [float("inf"), float("nan"), 0])
def test_nonfinite_rate_is_rejected_at_construction(fps):
    with pytest.raises(ValueError):
        DeadlineEngine(ALL_LANES, fps=fps, motion=IdentityMotion())


@pytest.mark.parametrize("delay", [float("inf"), float("nan"), -1.0])
def test_nonfinite_delay_is_rejected(delay):
    with pytest.raises(ValueError):
        DeadlineEngine(ALL_LANES, delay_seconds=delay, motion=IdentityMotion())


def test_nonfinite_frame_time_is_rejected_before_the_clock_starts():
    engine = make_engine()
    with pytest.raises(ValueError):
        engine.submit(make_frame(0, pts=float("nan")), now=10)
    assert engine.poll(now=100) == []
    assert engine.origin is None


def test_protection_duration_uses_missed_deadline_not_late_poll_time():
    engine = make_engine()
    engine.submit(make_frame(0, value=120), now=10)
    engine.end(0)
    (decision,) = engine.poll(now=12.1)
    assert decision.state == PROTECT and decision.presentation == "slate"
    assert decision.protect_age == pytest.approx(1.1)


def test_first_second_of_protect_is_a_mean_colour_fill_then_slate():
    engine = make_engine(protect_fill_s=1.0)
    for seq in range(60):
        engine.submit(make_frame(seq), now=T0 + seq / FPS)
    engine.end(59)
    first = engine.poll(now=dl(0))[0]
    assert first.presentation == "mean"
    later = engine.poll(now=dl(0) + 1.5)
    assert later and later[-1].presentation == "slate" and later[-1].state == PROTECT


def test_foreign_scene_results_do_not_replace_fresh_current_scene_coverage():
    engine = make_engine(motion=CutMotion())
    a = region_request("fast-a", (5, 5, 10, 10), seq=0)
    b = region_request("fast-b", (20, 20, 25, 25), seq=1)
    engine.submit_result(lane_result(FAST, 0, 10.1, requests=[a]))
    engine.submit_result(lane_result(FAST, 1, 10.2, requests=[b]))
    engine.submit_result(lane_result(ACC, 0, 10.1))
    engine.submit_result(lane_result(ACC, 1, 10.2))
    engine.submit(make_frame(0, value=120), now=10)
    engine.submit(make_frame(1, value=180), now=T0 + 1 / FPS)
    engine.end(1)
    decision = engine.poll(now=11)[0]
    assert decision.state == LIVE
    assert [m.request.request_id for m in decision.masks] == ["fast-a"]  # b belongs to another scene


def test_fresh_results_from_both_lanes_allow_live_and_union_all_masks():
    engine = make_engine()
    fast_req = region_request("fast-a", (5, 5, 10, 10))
    acc_req = region_request("acc-b", (20, 20, 25, 25))
    engine.submit_result(lane_result(FAST, 0, 10.1, requests=[fast_req]))
    engine.submit_result(lane_result(ACC, 0, 10.2, requests=[acc_req]))
    source = np.full((32, 32, 3), 120, np.uint8)
    engine.submit(dataclasses.replace(make_frame(0), image=source), now=10)
    source[:] = 250  # the transport reuses its buffer: the ring must hold its own copy
    engine.end(0)
    (decision,) = engine.poll(now=11)
    assert decision.state == LIVE and decision.presentation == "frame"
    assert decision.lane_ages == {"fast": 0, "accurate": 0}
    assert {m.request.request_id for m in decision.masks} == {"fast-a", "acc-b"}
    assert decision.image[0, 31].tolist() == [120, 120, 120]
    assert not decision.image.flags.writeable


def test_deadline_without_coverage_emits_protection_never_live():
    engine = make_engine()
    engine.submit(make_frame(0), now=10)
    engine.end(0)
    assert engine.poll(now=10.999) == []
    (decision,) = engine.poll(now=11)
    assert (decision.seq, decision.deadline) == (0, 11)
    assert (decision.state, decision.reason) == (PROTECT, "missing-fast")
    assert decision.masks == () and not decision.live
    assert engine.drained


# ---------------------------------------------------------------- start-up, gaps, validation


def test_engine_starts_in_protect_and_polls_nothing_before_first_frame():
    engine = make_engine()
    snap = engine.control.snapshot()
    assert (snap.state, snap.reason) == (PROTECT, "startup")
    assert engine.poll(now=100) == []


def test_missing_frame_is_filled_with_last_pixels_but_forced_protect():
    engine = make_engine()
    for seq in (0, 1, 3):  # 2 never arrives
        engine.submit(make_frame(seq, value=40 + seq), now=T0 + seq / FPS)
        for lane in ALL_LANES:
            engine.submit_result(lane_result(lane, seq, T0 + seq / FPS + 0.05))
    engine.end(3)
    result = {d.seq: d for d in engine.poll(now=20)}
    assert len(result) == 4 and engine.poll(now=20) == []
    gap = result[2]
    assert (gap.state, gap.reason, gap.gap) == (PROTECT, "ingest-gap", True)
    assert gap.pts == pytest.approx(2 / FPS)
    assert int(gap.image[0, 0, 0]) == 41  # the last emitted frame's pixels, not frame 3's
    assert result[3].state == LIVE


def test_gap_before_any_pixels_is_still_protect():
    engine = make_engine(delay=0.1)
    engine.submit(make_frame(0), now=T0 + 5)  # first frame is already late: discarded
    engine.end(0)
    (decision,) = engine.poll(now=T0 + 6)
    assert decision.state == PROTECT and decision.gap and decision.image is None


def test_submit_validation():
    engine = make_engine()
    with pytest.raises(ValueError):
        engine.submit(dataclasses.replace(make_frame(0), image=np.zeros((4, 4), np.uint8)))
    with pytest.raises(ValueError):
        engine.submit(dataclasses.replace(make_frame(0), image=np.zeros((4, 4, 3), np.float32)))
    engine.submit(make_frame(5), now=T0 + 5 / FPS)
    with pytest.raises(ValueError):
        engine.submit(make_frame(5), now=T0 + 5 / FPS)  # not increasing
    assert not engine.submit(make_frame(2), now=T0 + 5 / FPS)  # older than the next frame to emit
    engine.end(5)
    with pytest.raises(RuntimeError):
        engine.submit(make_frame(6), now=T0 + 6 / FPS)


def test_pts_reset_restarts_the_session_in_protect_and_keeps_holds():
    engine = make_engine(delay=0.5, max_buffer_frames=200)
    feed(engine, range(100))  # pts 0 .. 3.3
    engine.hold(HoldSource.AUTO_EGRESS, True, now=T0 + 3.3)
    assert engine.stats["buffered_frames"] == 100
    new_session = make_frame(0, t0=T0 + 4.0)  # pts back to 0
    assert engine.submit(new_session, now=T0 + 4.0)
    assert engine.stats["resets"] == 1
    assert engine.stats["buffered_frames"] == 1  # the old ring never goes out
    assert engine.control.snapshot().hold_sources == (HoldSource.AUTO_EGRESS,)
    engine.hold(HoldSource.AUTO_EGRESS, False, now=T0 + 4.0)
    decisions = engine.poll(now=T0 + 4.6)
    assert decisions[0].seq == 0
    assert all(d.state != LIVE for d in decisions)  # no results for the new session, and the fence applies


def test_results_for_inactive_or_long_past_frames_are_discarded():
    engine = make_engine(lanes=(FAST,), max_poll_frames=1000)
    engine.submit_result(lane_result(ACC, 0, 10.1))  # lane not active
    feed(engine, range(40))
    engine.poll(now=dl(39) + 1)
    engine.submit_result(lane_result(FAST, 2, 12.0))  # 2 < next_index - before
    stats = engine.stats["lanes"]
    assert stats["accurate"]["discarded"] == 1
    assert stats["fast"]["discarded"] == 1


# ---------------------------------------------------------------- fail-safe: never LIVE


def test_lane_that_never_reports_makes_every_frame_protect_and_no_live_frame_leaves():
    engine = make_engine(max_poll_frames=1000, max_buffer_frames=200)
    feed(engine, range(90), lanes=(FAST,))  # the accurate lane stays silent forever
    engine.end(89)
    result = engine.poll(now=30)
    assert len(result) == 90
    assert {d.state for d in result} == {PROTECT}
    assert {d.reason for d in result} == {"missing-accurate"}
    assert all(d.masks == () for d in result)
    assert engine.stats["live"] == 0 and engine.stats["protect"] == 90


def test_lane_that_crashes_mid_stream_goes_protect_exactly_when_its_coverage_ages_out():
    engine = make_engine(max_poll_frames=1000)
    for seq in range(60):
        arrival = T0 + seq / FPS
        engine.submit(make_frame(seq), now=arrival)
        engine.submit_result(lane_result(ACC, seq, arrival + 0.05))
        if seq <= 20:  # fast lane dies after its result for frame 20
            engine.submit_result(lane_result(FAST, seq, arrival + 0.05))
    engine.end(59)
    result = {d.seq: d for d in engine.poll(now=30)}
    assert all(result[s].state == LIVE for s in range(0, 27))  # result at 20 covers up to age 6
    assert all(result[s].state == PROTECT for s in range(27, 60))
    assert {result[s].reason for s in range(27, 60)} == {"missing-fast"}


@pytest.mark.parametrize(
    ("make_results", "reason"),
    [
        (lambda s: [lane_result(FAST, s, T0 + s / FPS + 0.05, ok=False)], "lane-error-fast"),
        (lambda s: [lane_result(FAST, s, T0 + s / FPS + 5.0)], "missing-fast"),  # always late
        (lambda s: [], "missing-fast"),
    ],
)
def test_failing_late_or_absent_lane_never_yields_live(make_results, reason):
    engine = make_engine(lanes=(FAST,), max_poll_frames=1000)
    for seq in range(40):
        engine.submit(make_frame(seq), now=T0 + seq / FPS)
        for result in make_results(seq):
            engine.submit_result(result)
    engine.end(39)
    decisions = engine.poll(now=30)
    assert len(decisions) == 40 and engine.stats["live"] == 0
    assert {d.reason for d in decisions} == {reason}
    if reason == "lane-error-fast":
        assert engine.stats["lanes"]["fast"]["failures"] == 40


def test_motion_that_never_completes_never_yields_live():
    class NoPath:
        def add(self, index, pixels):
            pass

        def project_many(self, quads, source, target, deadline=None):
            return None

    engine = make_engine(motion=NoPath(), max_poll_frames=1000)
    feed(engine, range(30))
    engine.end(29)
    decisions = engine.poll(now=30)
    assert engine.stats["live"] == 0
    assert {d.reason for d in decisions} == {"unresolved-motion-fast"}


def test_zero_enabled_lanes_refuses_live():
    engine = make_engine(lanes=(), max_poll_frames=1000)
    feed(engine, range(10))
    engine.end(9)
    decisions = engine.poll(now=30)
    assert {d.state for d in decisions} == {PROTECT} and {d.reason for d in decisions} == {"no-lanes"}


def test_late_result_cannot_rescue_its_frame_but_helps_the_next_one():
    engine = make_engine(lanes=(FAST,), max_poll_frames=1000)
    engine.submit(make_frame(0), now=10)
    engine.submit(make_frame(1), now=T0 + 1 / FPS)
    # completes just after frame 0's deadline, well before frame 1's
    engine.submit_result(lane_result(FAST, 1, dl(0) + 0.001))
    engine.end(1)
    first, second = engine.poll(now=20)
    assert (first.state, first.reason) == (PROTECT, "missing-fast")
    assert second.state == LIVE


def test_result_finishing_exactly_on_the_deadline_counts():
    engine = make_engine(lanes=(FAST,))
    engine.submit(make_frame(0), now=10)
    engine.submit_result(lane_result(FAST, 0, dl(0)))
    engine.end(0)
    assert engine.poll(now=dl(0))[0].state == LIVE


# ---------------------------------------------------------------- N lanes


def test_three_lanes_each_with_their_own_rate_and_a_missing_one_forces_protect():
    def run(skip_slow: bool):
        engine = make_engine(lanes=(FAST, ACC, SLOW), max_poll_frames=1000, max_buffer_frames=200)
        for seq in range(120):
            engine.submit(make_frame(seq), now=T0 + seq / FPS)
            done = T0 + seq / FPS + 0.05
            if seq % 3 == 0:
                engine.submit_result(lane_result(FAST, seq, done))
            if seq % 10 == 0:
                engine.submit_result(lane_result(ACC, seq, done))
            if seq % 30 == 0 and not skip_slow:
                engine.submit_result(lane_result(SLOW, seq, done))
        engine.end(119)
        return engine.poll(now=60)

    healthy = run(skip_slow=False)
    assert all(d.state == LIVE for d in healthy[:90])
    assert all(d.state == PROTECT for d in healthy[110:])  # no results ahead of the tail
    mid = healthy[40]
    assert set(mid.lane_ages) == {"fast", "accurate", "slow"}
    assert all(age is not None for age in mid.lane_ages.values())
    broken = run(skip_slow=True)
    assert all(d.state == PROTECT for d in broken)
    assert {d.reason for d in broken[:110]} == {"missing-slow"}


def test_per_lane_max_age_from_config_is_respected():
    config = Config(_env_file=None)
    assert (config.engine.lane_max_age.fast, config.engine.lane_max_age.accurate) == (6, 12)

    def state_with_fast_age(age: int):
        engine = DeadlineEngine.from_config(config, ALL_LANES, motion=IdentityMotion(), delay_seconds=1.0)
        engine.submit(make_frame(0), now=10)
        engine.submit_result(lane_result(FAST, age, 10.1))
        engine.submit_result(lane_result(ACC, 0, 10.1))
        engine.end(0)
        return engine.poll(now=11)[0]

    assert state_with_fast_age(6).state == LIVE
    assert state_with_fast_age(7).reason == "stale-fast"
    tight = Config(_env_file=None, engine={"lane_max_age": {"fast": 2, "accurate": 12, "slow": 60}})
    engine = DeadlineEngine.from_config(tight, ALL_LANES, motion=IdentityMotion(), delay_seconds=1.0)
    engine.submit(make_frame(0), now=10)
    engine.submit_result(lane_result(FAST, 3, 10.1))
    engine.submit_result(lane_result(ACC, 0, 10.1))
    engine.end(0)
    assert engine.poll(now=11)[0].reason == "stale-fast"


def test_from_config_sizes_the_ring_and_clamps_retro():
    config = Config(_env_file=None)
    engine = DeadlineEngine.from_config(config, ALL_LANES, motion=IdentityMotion())
    assert engine.delay_seconds == 4.0
    for seq in range(200):
        engine.submit(make_frame(seq, t0=T0), now=T0 + seq / FPS)  # none poll-emitted
    assert engine.stats["buffered_frames"] == 158  # ceil(30 * (4 + 1)) + 8
    assert engine.control.retro_seconds == config.effective_retro_seconds == 3.0


def test_active_lane_set_can_change_at_runtime():
    engine = make_engine(lanes=ALL_LANES, max_poll_frames=1000)
    engine.submit(make_frame(0), now=10)
    engine.submit_result(lane_result(FAST, 0, 10.1))
    engine.end(0)
    engine.set_lanes((FAST,))  # accurate-lane detectors were toggled off
    assert engine.poll(now=11)[0].state == LIVE
    assert engine.lanes == (FAST,)


# ---------------------------------------------------------------- mask selection and projection


def test_masks_are_projected_through_motion_from_the_anchor_frame():
    engine = make_engine(motion=TranslateMotion())
    request = region_request("card", (100, 100, 200, 200), seq=0)
    engine.submit_result(lane_result(FAST, 0, 10.1, requests=[request]))
    engine.submit_result(lane_result(ACC, 2, 10.1))
    for seq in range(3):
        engine.submit(make_frame(seq), now=T0 + seq / FPS)
    engine.end(2)
    d0, _, d2 = engine.poll(now=dl(2))
    assert d0.masks[0].quads[0].bbox() == (100, 100, 200, 200)
    assert d2.state == LIVE
    assert d2.masks[0].quads[0].bbox() == (120, 100, 220, 200)  # moved +20 px


def test_same_request_id_is_deduplicated_with_nearest_anchor_winning():
    engine = make_engine(motion=TranslateMotion())
    engine.submit_result(
        lane_result(FAST, 0, 10.1, requests=[region_request("t1", (100, 100, 200, 200), seq=0)])
    )
    engine.submit_result(
        lane_result(FAST, 3, 10.2, requests=[region_request("t1", (140, 100, 240, 200), seq=3)])
    )
    engine.submit_result(lane_result(ACC, 2, 10.1))
    for seq in range(3):
        engine.submit(make_frame(seq), now=T0 + seq / FPS)
    engine.end(2)
    d2 = engine.poll(now=dl(2))[2]
    assert len(d2.masks) == 1
    assert d2.masks[0].quads[0].bbox() == (130, 100, 230, 200)  # from the seq-3 anchor, 1 frame away


def test_request_ranges_decide_masks_beyond_the_covering_result():
    engine = make_engine(max_poll_frames=1000)
    engine.submit(make_frame(10), now=T0 + 10 / FPS)
    engine.submit_result(lane_result(FAST, 9, 10.5))  # the covering result: empty
    engine.submit_result(lane_result(ACC, 10, 10.5))
    spans = [
        ("covers", 20, 0.0, 2.0),  # future result, range covers frame 10 -> painted
        ("misses", 21, 21 / FPS, 3.0),  # future result, range starts after frame 10 -> ignored
        ("near", 11, 11 / FPS, 3.0),  # within max age -> painted like the covering result
    ]
    for name, seq, start, end in spans:
        engine.submit_result(
            lane_result(
                FAST,
                seq,
                10.6,
                requests=[region_request(name, (1, 1, 9, 9), seq=seq, pts_start=start, pts_end=end)],
            )
        )
    engine.end(10)
    decision = engine.poll(now=20)[-1]
    assert decision.seq == 10 and decision.state == LIVE
    assert {m.request.request_id for m in decision.masks} == {"covers", "near"}


def test_full_frame_request_sets_flag_and_protect_state_drops_masks():
    engine = make_engine()
    full = region_request("all", (0, 0, 0, 0), kind=MaskKind.FULL_FRAME)
    engine.submit_result(lane_result(FAST, 0, 10.1, requests=[full]))
    engine.submit_result(lane_result(ACC, 0, 10.1))
    engine.submit(make_frame(0), now=10)
    engine.end(0)
    (decision,) = engine.poll(now=11)
    assert decision.live and decision.full_frame and decision.masks == ()
    protected = make_engine()
    protected.submit_result(lane_result(FAST, 0, 10.1, requests=[region_request("x", (1, 1, 5, 5))]))
    protected.submit(make_frame(0), now=10)
    protected.end(0)
    (decision,) = protected.poll(now=11)  # accurate lane missing
    assert decision.state == PROTECT and decision.masks == () and not decision.full_frame


def test_audio_originated_requests_apply_by_pts_range_only():
    engine = make_engine(max_poll_frames=1000)
    mouth = region_request(
        "mouth-1", (0, 0, 0, 0), kind=MaskKind.MOUTH_OF_SPEAKER, pts_start=0.1, pts_end=0.2
    )
    engine.submit_request(mouth)
    with pytest.raises(ValueError):
        engine.submit_request(region_request("r", (1, 1, 5, 5)))
    feed(engine, range(12))
    engine.end(11)
    result = {d.seq: d for d in engine.poll(now=30)}
    assert [s for s, d in result.items() if d.mouth_requests] == [3, 4, 5, 6]


# ---------------------------------------------------------------- hold, fence and retro hold


def test_two_holds_release_one_still_hold_release_both_slate_until_the_fence_frame():
    engine = make_engine(max_poll_frames=1000)
    feed(engine, range(30))
    assert engine.poll(now=dl(0))[0].state == LIVE
    engine.hold(HoldSource.HOTKEY, True, now=11.0)
    engine.hold(HoldSource.CHAT, True, now=11.0)
    engine.hold(HoldSource.HOTKEY, False, now=11.1)
    state = engine.poll(now=dl(10))
    assert state and all(d.state == HOLD and d.audio_silent for d in state)  # chat still holds
    assert {d.reason for d in state} == {"chat"}
    engine.hold(HoldSource.CHAT, False, now=11.2)
    feed(engine, range(30, 45))  # captured after the release: newer than the fence
    after = {d.seq: d for d in engine.poll(now=dl(44))}
    stale = [s for s in after if s < 30]
    assert stale and all(after[s].state == HOLD and after[s].fenced for s in stale)
    assert all(after[s].reason == "hold-fence" and after[s].presentation == "slate" for s in stale)
    fresh = [s for s in after if s >= 30]
    assert fresh and all(after[s].state == LIVE for s in fresh)


def test_fence_frames_older_than_the_capture_high_water_stay_slated_after_release():
    engine = make_engine(max_poll_frames=1000)
    feed(engine, range(30))
    engine.poll(now=dl(0))
    engine.hold(HoldSource.API, True, now=11.0)
    feed(engine, range(30, 40))  # captured while held: also older than the mark at release
    engine.poll(now=dl(5))  # emitted under HOLD
    engine.hold(HoldSource.API, False, now=11.5)
    feed(engine, range(40, 50))  # captured after the release
    out = {d.seq: d for d in engine.poll(now=dl(49))}
    assert all(out[s].state == HOLD and out[s].reason == "hold-fence" for s in range(6, 40) if s in out)
    assert max(s for s in out if out[s].fenced) == 39
    assert all(out[s].state == LIVE for s in range(40, 50))


def test_hold_pressed_and_released_between_polls_still_fences_buffered_frames():
    engine = make_engine(max_poll_frames=1000)
    feed(engine, range(20))
    engine.hold(HoldSource.APP, True, now=10.5)
    engine.hold(HoldSource.APP, False, now=10.5)  # nothing was emitted under HOLD
    out = engine.poll(now=dl(19))
    assert len(out) == 20 and all(d.state == HOLD for d in out)


def test_coverage_protect_fences_only_its_own_frame():
    engine = make_engine(lanes=(FAST,), max_poll_frames=1000)
    for seq in range(3):
        engine.submit(make_frame(seq), now=T0 + seq / FPS)
    engine.submit_result(lane_result(FAST, 1, dl(0) + 0.01))  # too late for frame 0 only
    engine.end(2)
    first, second, third = engine.poll(now=20)
    assert first.state == PROTECT
    assert second.state == LIVE and third.state == LIVE  # a PROTECT blip never slates the buffer


def test_hold_command_from_the_bus_and_recheck_before_encoder_handoff():
    engine = make_engine()
    feed(engine, range(2))
    (decision,) = engine.poll(now=dl(0))
    assert decision.state == LIVE and not decision.audio_silent
    engine.apply_command(HoldCommand(source=HoldSource.HOTKEY, active=True, actor="abc123"), now=11.01)
    upgraded = engine.recheck(decision)
    assert upgraded is decision
    assert (decision.state, decision.presentation, decision.audio_silent) == (HOLD, "slate", True)
    assert decision.masks == () and not decision.full_frame
    assert engine.hold_active


@pytest.mark.parametrize("source", [HoldSource.HOTKEY, HoldSource.API, HoldSource.APP, HoldSource.CHAT])
def test_retro_hold_marks_ring_frames_with_arrival_at_or_after_t_minus_retro(source):
    engine = make_engine(delay=4.0, retro_seconds=3.0, max_poll_frames=1000)
    feed(engine, range(135))  # arrivals 10.00 .. 14.47, nothing emitted yet
    engine.end(134)
    engine.poll(now=14.5)  # frames 0..15 leave before the press
    press = 14.51
    engine.hold(source, True, now=press)
    engine.hold(source, False, now=press)
    floor = press - 3.0
    out = {d.seq: d for d in engine.poll(now=30)}
    assert min(out) == 16 and max(out) == 134
    for seq, decision in out.items():
        assert decision.state == HOLD
        assert decision.retro == (T0 + seq / FPS >= floor)
        assert decision.reason == ("retro-hold" if decision.retro else "hold-fence")
    assert min(s for s, d in out.items() if d.retro) == 46  # first frame with arrival >= 11.51
    (span,) = engine.pop_retro_ranges()
    assert (span.source, span.pts_start, span.pts_end) == (source, pytest.approx(1.51), pytest.approx(4.51))
    assert engine.pop_retro_ranges() == []


def test_retro_window_is_clamped_to_delay_minus_half_a_second():
    engine = make_engine(delay=4.0, retro_seconds=10.0)
    engine.submit(make_frame(0), now=10)
    engine.hold(HoldSource.HOTKEY, True, now=12.0)
    (span,) = engine.pop_retro_ranges()
    assert span.pts_end - span.pts_start == pytest.approx(3.5)
    short = make_engine(delay=3.0, retro_seconds=3.0)
    short.submit(make_frame(0), now=10)
    short.hold(HoldSource.HOTKEY, True, now=12.0)
    (span,) = short.pop_retro_ranges()
    assert span.pts_end - span.pts_start == pytest.approx(2.5)


@pytest.mark.parametrize("source", [HoldSource.AUTO_TRANSPORT, HoldSource.AUTO_EGRESS])
def test_automatic_sources_do_not_retro(source):
    engine = make_engine(delay=4.0, retro_seconds=3.0, max_poll_frames=1000)
    feed(engine, range(60))
    engine.end(59)
    engine.hold(source, True, now=11.9)
    engine.hold(source, False, now=11.9)
    out = engine.poll(now=30)
    assert out and not any(d.retro for d in out)
    assert engine.pop_retro_ranges() == []
    assert all(d.fenced for d in out)  # the fence still protects buffered frames


def test_second_press_of_an_already_held_source_does_not_extend_retro():
    engine = make_engine(delay=4.0, retro_seconds=3.0)
    engine.submit(make_frame(0), now=10)
    engine.hold(HoldSource.HOTKEY, True, now=12.0)
    engine.hold(HoldSource.HOTKEY, True, now=13.0)
    assert len(engine.pop_retro_ranges()) == 1


def test_retro_frames_already_emitted_are_not_touched():
    engine = make_engine(delay=1.0, retro_seconds=0.5, max_poll_frames=1000)
    feed(engine, range(20))
    gone = engine.poll(now=dl(9))  # frames 0..9 already left
    engine.hold(HoldSource.HOTKEY, True, now=10.6)
    assert all(d.state == LIVE for d in gone)
    engine.end(19)
    rest = engine.poll(now=dl(19) + 10)
    assert [d.seq for d in rest] == list(range(10, 20)) and all(d.state == HOLD for d in rest)


# ---------------------------------------------------------------- timing, stats, close


def test_stats_record_emit_lag_and_stage_timings():
    engine = make_engine()
    feed(engine, range(3))
    engine.end(2)
    out = engine.poll(now=dl(2) + 0.25)
    assert len(out) == 3
    assert out[2].emit_lag == pytest.approx(0.25)
    stats = engine.stats
    assert stats["outputs"] == 3 and stats["live"] == 3
    assert len(stats["stage_seconds"]["coverage"]) == 3 and len(stats["emit_lag_seconds"]) == 3
    assert stats["lanes"]["fast"]["results"] == 3


def test_lanes_stale_reports_lanes_without_a_covering_result():
    engine = make_engine()
    engine.submit(make_frame(0), now=10)
    engine.submit_result(lane_result(FAST, 0, 10.1))
    engine.end(0)
    engine.poll(now=11)
    assert engine.lanes_stale() == ("accurate",)
