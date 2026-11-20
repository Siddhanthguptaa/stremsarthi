"""Poll cost with a full 160-frame ring (acceptance: under 1 ms per emitted frame, CPU only)."""

import statistics
import time

from contracts import Lane
from engine.testing import FPS, T0, lane_result, make_engine, make_frame, region_request

RING = 160
LANES = (Lane.FAST, Lane.ACCURATE, Lane.SLOW)
PERIOD = {Lane.FAST: 3, Lane.ACCURATE: 8, Lane.SLOW: 30}  # frames between lane runs (8, 4 and 1 Hz)
BUDGET_MS = 1.0


def build(n_requests: int = 4, *, terminal: bool = True):
    engine = make_engine(LANES, delay=4.0, max_buffer_frames=RING, max_poll_frames=RING)
    for seq in range(RING):
        arrival = T0 + seq / FPS
        engine.submit(make_frame(seq, size=16), now=arrival)
        for lane in LANES:
            if seq % PERIOD[lane] == 0:
                requests = [
                    region_request(
                        f"{lane.value}-{k}", (10 * k, 10, 10 * k + 50, 60), seq=seq, pts_end=seq / FPS + 1.0
                    )
                    for k in range(n_requests)
                ]
                engine.submit_result(lane_result(lane, seq, arrival + 0.05, requests=requests))
    if terminal:
        engine.end(RING - 1)
    return engine


def per_frame_ms_batch() -> float:
    engine = build()
    began = time.perf_counter()
    decisions = engine.poll(now=T0 + 60)
    elapsed = time.perf_counter() - began
    assert len(decisions) == RING
    assert sum(d.live for d in decisions) > RING // 2  # real work happened: masks were selected
    return 1000 * elapsed / RING


def per_frame_ms_tick() -> float:
    """The production pattern: one poll per 33 ms tick, emitting one frame, ring still full."""
    engine = build(terminal=False)
    samples = []
    for seq in range(60):
        now = T0 + seq / FPS + 4.0
        began = time.perf_counter()
        out = engine.poll(now=now)
        samples.append(1000 * (time.perf_counter() - began) / max(1, len(out)))
        assert len(out) == 1
    return statistics.median(samples)


def test_poll_cost_per_frame_with_a_full_ring_is_under_a_millisecond():
    batch = min(per_frame_ms_batch() for _ in range(5))
    tick = min(per_frame_ms_tick() for _ in range(5))
    print(f"poll cost per frame: batch={batch:.3f} ms, tick={tick:.3f} ms (ring={RING}, lanes=3)")
    assert batch < BUDGET_MS
    assert tick < BUDGET_MS
