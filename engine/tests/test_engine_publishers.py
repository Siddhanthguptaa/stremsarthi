"""Safety events (masked, failsafe, held) and the 1 Hz engine status."""

from __future__ import annotations

from contracts import CONTRACT_VERSION, Category, HoldSource, Lane, Modality, Quad, Topic
from engine.deadline import ProjectedMask
from engine.publishers import SafetyPublisher, StatusPublisher
from engine.testing import FPS, T0, feed, make_engine, make_frame, region_request


class Capture:
    def __init__(self) -> None:
        self.items: list[tuple[Topic, object]] = []

    def publish(self, topic: Topic, event: object) -> None:
        self.items.append((topic, event))

    def of(self, action: str):
        return [e for t, e in self.items if t is Topic.SAFETY_EVENT and e.action == action]


def pm(box=(100, 100, 300, 200), *, track_id=None, category=Category.AADHAAR, rid="r") -> ProjectedMask:
    request = region_request(rid, box, category=category)
    if track_id is not None:
        import dataclasses

        request = dataclasses.replace(request, track_id=track_id)
    return ProjectedMask(request, request.quads)


def publisher(cap: Capture, **kw) -> SafetyPublisher:
    return SafetyPublisher(cap, "sess1", fps=FPS, wall=lambda: "2026-11-20T00:00:00+00:00", **kw)


# ------------------------------------------------------------------ masked


def test_one_event_per_track_start_and_duration_at_the_end():
    cap = Capture()
    pub = publisher(cap)
    for i in range(100):  # 3.3 s of the same card, a fresh request id every frame
        pub.frame(i / FPS, [pm(rid=f"r{i}")])
    assert len(cap.items) == 1  # never one per frame
    start = cap.of("masked")[0]
    assert start.duration_ms is None and start.pts == 0.0
    assert start.modality is Modality.VIDEO and start.category is Category.AADHAAR
    assert start.bbox == Quad.from_xyxy(100, 100, 300, 200) and start.session_id == "sess1"
    for i in range(100, 130):  # the mask is gone for 1 s
        pub.frame(i / FPS, [])
    events = cap.of("masked")
    assert len(events) == 2 and events[1].event_id == start.event_id  # same id: consumers upsert
    assert events[1].duration_ms == round((99 / FPS + 1 / FPS) * 1000)


def test_tracked_masks_use_the_track_id_and_a_reappearance_is_a_new_event():
    cap = Capture()
    pub = publisher(cap)
    for i in range(10):
        pub.frame(i / FPS, [pm(track_id=7), pm((500, 300, 600, 400), track_id=8)])
    assert len(cap.of("masked")) == 2
    for i in range(10, 30):
        pub.frame(i / FPS, [pm(track_id=8, box=(500, 300, 600, 400))])  # track 7 leaves
    ended = [e for e in cap.of("masked") if e.duration_ms is not None]
    assert len(ended) == 1 and ended[0].bbox == Quad.from_xyxy(100, 100, 300, 200)
    for i in range(30, 45):
        pub.frame(i / FPS, [pm(track_id=7)])  # 7 comes back after more than the gap
    assert len({e.event_id for e in cap.of("masked")}) == 3


def test_untracked_masks_are_associated_by_overlap():
    cap = Capture()
    pub = publisher(cap)
    for i in range(30):
        x = 100 + 2 * i  # drifting 2 px per frame: still the same object
        pub.frame(i / FPS, [pm((x, 100, x + 200, 200))])
    pub.frame(30 / FPS, [pm((100, 100, 300, 200)), pm((800, 400, 900, 500))])  # a second, distant object
    assert len({e.event_id for e in cap.of("masked")}) == 2


def test_several_results_for_one_object_in_a_frame_make_one_track():
    cap = Capture()
    pub = publisher(cap)
    for i in range(20):
        pub.frame(i / FPS, [pm(rid=f"a{i}"), pm(rid=f"b{i}"), pm(rid=f"c{i}")])
    assert len({e.event_id for e in cap.of("masked")}) == 1


def test_different_categories_are_different_tracks():
    cap = Capture()
    pub = publisher(cap)
    pub.frame(0.0, [pm(category=Category.AADHAAR), pm(category=Category.FACE)])
    assert {e.category for e in cap.of("masked")} == {Category.AADHAAR, Category.FACE}


def test_close_flushes_open_tracks_with_a_duration():
    cap = Capture()
    pub = publisher(cap)
    for i in range(6):
        pub.frame(i / FPS, [pm()])
    pub.close(now=1.0)
    events = cap.of("masked")
    assert len(events) == 2 and events[1].duration_ms == round(6 / FPS * 1000)


# ------------------------------------------------------------------ failsafe


def test_failsafe_events_skip_warm_up_and_cover_one_episode():
    cap = Capture()
    pub = publisher(cap)
    for i in range(5):  # engine starts in PROTECT: not reported
        pub.state("PROTECT", "missing-fast", i / FPS)
    assert cap.of("failsafe") == []
    pub.state("LIVE", "covered", 5 / FPS)
    for i in range(6, 66):
        pub.state("PROTECT", "stale-fast" if i > 6 else "missing-fast", i / FPS)  # 2 s of protection
    assert len(cap.of("failsafe")) == 1  # one event for the episode, not per frame
    first = cap.of("failsafe")[0]
    assert first.category is Category.FAILSAFE and first.modality is Modality.SYSTEM
    assert first.detail_code == "missing-fast" and first.duration_ms is None and first.bbox is None
    pub.state("LIVE", "covered", 66 / FPS)
    events = cap.of("failsafe")
    assert len(events) == 2 and events[1].event_id == first.event_id
    assert events[1].duration_ms == round((65 / FPS - 6 / FPS + 1 / FPS) * 1000)


def test_a_hold_ends_a_failsafe_episode():
    cap = Capture()
    pub = publisher(cap)
    pub.state("LIVE", "covered", 0.0)
    pub.state("PROTECT", "missing-fast", 0.1)
    pub.state("HOLD", "hotkey", 0.2)
    assert [e.duration_ms is not None for e in cap.of("failsafe")] == [False, True]


# ------------------------------------------------------------------ held


def test_held_event_on_release_with_the_time_held():
    cap = Capture()
    pub = publisher(cap)
    pub.hold(HoldSource.HOTKEY, True, now=100.0, pts=4.0)
    pub.hold(HoldSource.HOTKEY, True, now=101.0, pts=5.0)  # a repeated assert does not restart the clock
    assert cap.of("held") == []
    pub.hold(HoldSource.HOTKEY, False, now=102.5, pts=6.0)
    (event,) = cap.of("held")
    assert event.category is Category.HOLD and event.duration_ms == 2500 and event.pts == 4.0
    assert event.source == "hotkey" and event.confidence == 1.0 and event.bbox is None
    pub.hold(HoldSource.HOTKEY, False, now=103.0, pts=7.0)  # a release without an assert is ignored
    assert len(cap.of("held")) == 1


def test_auto_transport_hold_is_left_to_the_relay_and_close_flushes_open_holds():
    cap = Capture()
    pub = publisher(cap)
    pub.hold(HoldSource.AUTO_TRANSPORT, True, now=1.0, pts=None)
    pub.hold(HoldSource.AUTO_TRANSPORT, False, now=2.0, pts=None)
    assert cap.of("held") == []
    pub.hold(HoldSource.API, True, now=5.0, pts=None)
    pub.close(now=8.0)
    (event,) = cap.of("held")
    assert event.source == "api" and event.duration_ms == 3000


# ------------------------------------------------------------------ status


def make_status(cap: Capture):
    engine = make_engine((Lane.FAST,), delay=1.0)
    feed(engine, range(10))
    clock = [0.0]
    return engine, StatusPublisher(cap, engine, process_clock=lambda: clock[0]), clock


def test_status_is_published_once_a_second():
    cap = Capture()
    engine, status, _ = make_status(cap)
    assert status.tick(100.0, "live") is not None
    assert status.tick(100.4, "live") is None and status.tick(100.99, "live") is None
    assert status.tick(101.0, "live") is not None
    assert [t for t, _ in cap.items] == [Topic.ENGINE_STATUS] * 2


def test_status_fields():
    cap = Capture()
    engine, status, clock = make_status(cap)
    status.snapshot(100.0, "live")  # the first call only anchors the CPU reading
    for i in range(30):  # 30 fps for 1 s of emits, each 4.0 s after its arrival
        now = 100.0 + i / FPS
        status.observe(now, now - 4.0, protect_age=0.0)
    engine.hold(HoldSource.API, True, now=1.0)
    status.allowlist_version = 3
    clock[0] = 0.8
    out = status.snapshot(101.0, "live")
    assert out.state == "live" and out.hold_active and out.hold_sources == (HoldSource.API,)
    assert out.fps == 30.0
    assert out.delay_s_p50 == 4.0 and out.cpu_cores == 0.8 and out.vram_mb is None
    assert out.allowlist_version == 3 and out.contract_version == CONTRACT_VERSION
    assert out.protect_seconds == 0.0


def test_status_lanes_stale_and_protect_seconds():
    cap = Capture()
    engine = make_engine((Lane.FAST, Lane.ACCURATE), delay=1.0)
    status = StatusPublisher(cap, engine)
    engine.submit(make_frame(0), now=T0)
    engine.poll(now=T0 + 2.0)  # no results: both lanes are stale
    status.observe(T0 + 2.0, None, protect_age=2.5)
    out = status.snapshot(T0 + 2.0, "waiting_ingest")
    assert out.lanes_stale == ("fast", "accurate") and out.protect_seconds == 2.5 and out.fps == 1.0
    assert out.delay_s_p50 is None
