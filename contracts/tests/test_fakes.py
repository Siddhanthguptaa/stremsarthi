import asyncio

import pytest

from contracts import (
    AllowlistView,
    Category,
    ChatAction,
    ChatMessage,
    DetectContext,
    Detection,
    Detector,
    EventBus,
    FaceEnroller,
    HoldCommand,
    HoldSource,
    Lane,
    OcrProvider,
    Quad,
    Resources,
    SpeechToText,
    Topic,
)
from contracts.fakes import (
    FakeAllowlist,
    FakeChatClassifier,
    FakeDetector,
    FakeEnroller,
    FakeFrameSource,
    FakeResources,
    FakeSTT,
    InProcessBus,
    make_fake_detector,
)

Q = Quad.from_xyxy(0, 0, 10, 10)


def run(coro):
    return asyncio.run(coro)


def hold(i, active=True):
    return HoldCommand(source=HoldSource.API, active=active, actor=f"a{i}")


def test_fakes_satisfy_protocols():
    assert isinstance(FakeDetector(), Detector)
    assert isinstance(FakeSTT(), SpeechToText)
    assert isinstance(InProcessBus(), EventBus)
    assert isinstance(FakeAllowlist(), AllowlistView)
    assert isinstance(FakeResources(), Resources)
    assert isinstance(FakeResources().ocr, OcrProvider)
    assert isinstance(FakeEnroller(), FaceEnroller)


def test_a_class_missing_a_method_does_not_satisfy_the_protocol():
    class NoDetect:
        id = "x"

    assert not isinstance(NoDetect(), Detector)


def test_bus_delivers_to_subscribers_registered_before_publish():
    async def go():
        bus = InProcessBus()
        sub = bus.subscribe([Topic.HOLD])
        other = bus.subscribe([Topic.SAFETY_EVENT])
        cmd = hold(0)
        bus.publish(Topic.HOLD, cmd)
        topic, ev = await asyncio.wait_for(sub.__anext__(), 1)
        assert (topic, ev) == (Topic.HOLD, cmd)
        assert other.empty()
        assert bus.of(Topic.HOLD) == [cmd]
        sub.close()
        bus.publish(Topic.HOLD, cmd)
        assert sub.empty()

    run(go())


def test_bus_wakes_a_waiting_subscriber():
    async def go():
        bus = InProcessBus()
        sub = bus.subscribe([Topic.HOLD])
        waiter = asyncio.ensure_future(sub.__anext__())
        await asyncio.sleep(0)
        bus.publish(Topic.HOLD, hold(1))
        topic, _ = await asyncio.wait_for(waiter, 1)
        assert topic is Topic.HOLD

    run(go())


def test_bus_rejects_wrong_event_type_for_topic():
    bus = InProcessBus()
    with pytest.raises(TypeError):
        bus.publish(Topic.SAFETY_EVENT, hold(0))


def test_bus_drops_oldest_when_subscriber_is_slow_but_never_a_hold():
    async def go():
        bus = InProcessBus(maxsize=2)
        sub = bus.subscribe([Topic.HOLD, Topic.TIMING])
        from contracts import StageTiming

        t = [StageTiming(stage="s", seq=i, pts=None, t_start=0.0, t_end=0.1) for i in range(4)]
        bus.publish(Topic.TIMING, t[0])
        bus.publish(Topic.HOLD, hold(0))
        bus.publish(Topic.TIMING, t[1])  # full: drops the oldest droppable (t[0]), keeps the hold
        bus.publish(Topic.TIMING, t[2])
        bus.publish(Topic.HOLD, hold(1))
        got = [sub.get_nowait() for _ in range(len(sub))]
        holds = [e for tp, e in got if tp is Topic.HOLD]
        assert len(holds) == 2  # no hold was ever dropped
        assert sub.dropped >= 2

    run(go())


def test_fake_detector_stamps_frames_and_injects_failures():
    from contracts import Frame

    det0 = Detection(
        detector="fake",
        category=Category.AADHAAR,
        quad=Q,
        confidence=0.9,
        frame_seq=-1,
        pts=-1.0,
        completed_at=0.0,
    )
    d = FakeDetector(script={2: [det0]}, raise_on_seq=5)
    ctx = DetectContext(lane=Lane.FAST, categories={}, stride=3)
    out = d.detect(Frame(seq=2, pts=0.066, arrival=0.0, image=None), ctx)
    assert out[0].frame_seq == 2 and out[0].pts == 0.066 and out[0].completed_at > 0
    assert d.detect(Frame(seq=3, pts=0.1, arrival=0.0, image=None), ctx) == []
    with pytest.raises(RuntimeError):
        d.detect(Frame(seq=5, pts=0.2, arrival=0.0, image=None), ctx)
    Sub = make_fake_detector("other", Lane.ACCURATE, frozenset({Category.PAN}))
    assert Sub.id == "other" and Sub.lane is Lane.ACCURATE and FakeDetector.id == "fake"


def test_fake_stt_word_times_approx_and_failure():
    async def go():
        t = await FakeSTT(texts=["ek do teen"], word_times=True).transcribe(None, 10.0, "hi")
        assert [w.t0 for w in t.words] == pytest.approx([10.0, 10.5, 11.0]) and t.has_word_times
        seg = await FakeSTT(texts=["ek do"], word_times=False).transcribe(None, 0.0, None)
        assert all(w.approx for w in seg.words) and not seg.has_word_times
        with pytest.raises(TimeoutError):
            await FakeSTT(fail_with=TimeoutError()).transcribe(None, 0.0, None)

    run(go())


def test_fake_chat_classifier():
    def msg(text):
        return ChatMessage(
            platform="twitch",
            message_id="m",
            channel_id="c",
            author_id="a",
            author_display="d",
            text=text,
            received_at=0.0,
        )

    out = run(FakeChatClassifier().classify([msg("hi"), msg("x [scam-test] y")]))
    assert out[0].action is ChatAction.ALLOW
    assert out[1].action is ChatAction.DELETE and out[1].categories == (Category.CHAT_SCAM_LINK,)


def test_fake_resources_and_enroller():
    res = FakeResources()
    with pytest.raises(RuntimeError):
        res.ort_session("yunet")
    res.register_session("yunet", object())
    assert res.ort_session("yunet") is not None
    assert res.model_path("yunet").name == "yunet"
    fid, emb = FakeEnroller().enrol([None] * 5, "label")
    assert fid.startswith("f_") and sum(x * x for x in emb) == pytest.approx(1.0)
    assert FakeEnroller().enrol([None] * 5, "label") == (fid, emb)  # deterministic
    with pytest.raises(ValueError):
        FakeEnroller().enrol([None] * 2, "label")


def test_fake_frame_source_needs_numpy_and_is_deterministic():
    np = pytest.importorskip("numpy", exc_type=ImportError)
    try:
        np.zeros(1)
    except Exception:  # broken numpy install
        pytest.skip("numpy unusable")
    frames = list(FakeFrameSource(n_frames=3))
    assert [f.seq for f in frames] == [0, 1, 2]
    frames[0].validate_canvas()
    x1, y1, x2, y2 = (int(v) for v in FakeFrameSource().card_quad.bbox())
    assert frames[0].image[(y1 + y2) // 2, x1 + 5].tolist() == [235, 235, 235]
    assert frames[1].pts == pytest.approx(1 / 30)
