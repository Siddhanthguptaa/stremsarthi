"""Supporting contracts added from the module specs (LaneResult, mod queue, allowlist, status, ...)."""

import dataclasses
import math

import pytest

from contracts import (
    AllowlistUpdate,
    AudioHost,
    Category,
    ChatAction,
    ChatMessage,
    ChatVerdict,
    ConfigChange,
    Detection,
    EngineStatus,
    EnrolledFaceMeta,
    FrameSink,
    FrameSource,
    Lane,
    LaneHost,
    LaneResult,
    MaskKind,
    MaskRequest,
    MaskStyle,
    ModAction,
    ModQueueItem,
    OcrResult,
    PiiSpan,
    Quad,
    RiskMeter,
    SessionCommand,
    Transcript,
    Word,
)

Q = Quad.from_xyxy(0, 0, 10, 10)


def mask():
    return MaskRequest(
        request_id="r",
        source="s",
        category=Category.FACE,
        kind=MaskKind.REGION,
        quads=(Q,),
        frame_pts=0.0,
        pts_start=0.0,
        pts_end=1.0,
        style=MaskStyle.BLUR,
        confidence=0.5,
        reason="x",
        completed_at=0.0,
    )


def msg(mid="m1"):
    return ChatMessage(
        platform="twitch",
        message_id=mid,
        channel_id="c",
        author_id="a",
        author_display="Name",
        text="secret",
        received_at=0.0,
    )


def verdict(mid="m1"):
    return ChatVerdict(
        platform="twitch",
        message_id=mid,
        action=ChatAction.HIDE_FROM_STREAMER,
        categories=(Category.CHAT_ABUSE,),
        confidence=0.9,
        reason="r",
        stage="rules",
        latency_ms=1.0,
    )


def test_lane_result_heartbeat_and_failure_rules():
    ok = LaneResult(lane=Lane.FAST, frame_seq=3, pts=0.1, completed_at=1.0, ok=True)
    assert ok.requests == () and ok.aux_detections == ()
    LaneResult(lane=Lane.ACCURATE, frame_seq=3, pts=0.1, completed_at=1.0, ok=True, requests=(mask(),))
    with pytest.raises(ValueError):
        LaneResult(lane=Lane.FAST, frame_seq=3, pts=0.1, completed_at=1.0, ok=False, requests=(mask(),))
    with pytest.raises(ValueError):
        LaneResult(lane=Lane.FAST, frame_seq=3, pts=math.nan, completed_at=1.0, ok=True)


def test_detection_allow_only_for_faces_and_finite_times():
    base = dict(detector="d", quad=Q, confidence=0.5, frame_seq=0, pts=0.0, completed_at=0.0)
    Detection(category=Category.FACE, allow=True, **base)
    with pytest.raises(ValueError):
        Detection(category=Category.AADHAAR, allow=True, **base)
    with pytest.raises(ValueError):
        Detection(category=Category.FACE, **{**base, "pts": math.inf})
    with pytest.raises(ValueError):
        Quad(((0, 0), (math.nan, 0), (1, 1), (0, 1)))


def test_mod_queue_item_and_action():
    ModQueueItem(message=msg(), verdict=verdict(), enqueued_at=0.0)
    with pytest.raises(ValueError):
        ModQueueItem(message=msg("m1"), verdict=verdict("m2"), enqueued_at=0.0)
    with pytest.raises(ValueError):
        ModQueueItem(message=msg(), verdict=verdict(), enqueued_at=0.0, state="vanished")
    ModAction(message_id="m1", platform="twitch", action="approve", timeout_seconds=None, actor="h1")
    with pytest.raises(ValueError):
        ModAction(message_id="m1", platform="twitch", action="timeout", timeout_seconds=None, actor="h1")
    with pytest.raises(ValueError):
        ModAction(
            message_id="m1", platform="twitch", action="approve", timeout_seconds=None, actor="Some Person"
        )
    assert "secret" not in repr(ModQueueItem(message=msg(), verdict=verdict(), enqueued_at=0.0))


def test_allowlist_update_rules_and_redaction():
    AllowlistUpdate(op="add", face_id="f1", label="Name", embedding=(0.1, 0.2), version=1)
    AllowlistUpdate(op="remove", face_id="f1", label=None, embedding=None, version=2)
    AllowlistUpdate(op="snapshot", face_id=None, label=None, embedding=None, version=3)
    for bad in (
        dict(op="add", face_id="f1", label=None, embedding=None, version=1),
        dict(op="add", face_id=None, label=None, embedding=(0.1,), version=1),
        dict(op="remove", face_id=None, label=None, embedding=None, version=1),
        dict(op="snapshot", face_id="f1", label=None, embedding=None, version=1),
        dict(op="add", face_id="f1", label=None, embedding=(math.nan,), version=1),
    ):
        with pytest.raises(ValueError):
            AllowlistUpdate(**bad)
    r = repr(AllowlistUpdate(op="add", face_id="f1", label="Teammate", embedding=(0.111, 0.222), version=1))
    assert "0.111" not in r and "Teammate" not in r
    assert "Teammate" not in repr(
        EnrolledFaceMeta(
            face_id="f1", label="Teammate", created_at="2026-11-20T10:00:00+00:00", expires_at=None
        )
    )


def test_config_change_rules():
    ConfigChange(diff={"categories.face.enabled": False, "audio.languages": ["hi", "en"]}, actor="h1")
    with pytest.raises(ValueError):
        ConfigChange(diff={}, actor="h1")
    with pytest.raises(ValueError):
        ConfigChange(diff={"bad key with spaces": 1}, actor="h1")
    with pytest.raises(ValueError):
        ConfigChange(diff={"a.b": object()}, actor="h1")


def test_risk_meter_engine_status_session():
    RiskMeter(wall_ts="t", level="yellow", score=0.3, top_sources=("music",))
    with pytest.raises(ValueError):
        RiskMeter(wall_ts="t", level="purple", score=0.3)
    with pytest.raises(ValueError):
        RiskMeter(wall_ts="t", level="red", score=0.3, top_sources=("has space",))
    kw = dict(
        hold_active=False,
        hold_sources=(),
        delay_s_p50=None,
        fps=None,
        cpu_cores=None,
        vram_mb=None,
        allowlist_version=0,
        lanes_stale=(),
        protect_seconds=0.0,
        contract_version="1.0.0",
    )
    EngineStatus(state="ready", **kw)
    with pytest.raises(ValueError):
        EngineStatus(state="confused", **kw)
    with pytest.raises(ValueError):
        EngineStatus(state="ready", **{**kw, "lanes_stale": ("fast lane",)})
    SessionCommand(action="stop", actor="h1")
    with pytest.raises(ValueError):
        SessionCommand(action="explode", actor="h1")


def test_speech_types():
    w = Word("ek", 1.0, 1.5)
    assert "ek" not in repr(w)
    with pytest.raises(ValueError):
        Word("x", 2.0, 1.0)
    t = Transcript(0.0, 1.5, "ek do", (Word("ek", 0.0, 0.7), Word("do", 0.7, 1.5)), "hi", "sarvam", 0.4)
    assert t.has_word_times and "ek" not in repr(t)
    approx = Transcript(0.0, 1.5, "ek do", (Word("ek", 0.0, 0.7, approx=True),), "hi", "local", 0.4)
    assert not approx.has_word_times and not Transcript(0.0, 1.0, "", (), "hi", "local", 0.1).has_word_times
    with pytest.raises(ValueError):
        Transcript(0.0, 1.0, "x", (), "hi", "whisperx", 0.1)
    PiiSpan(
        category=Category.SPOKEN_PHONE,
        pts_start=0.0,
        pts_end=1.0,
        confidence=0.8,
        reason="digit_run",
        granularity="word",
    )
    with pytest.raises(ValueError):
        PiiSpan(
            category=Category.SPOKEN_PHONE,
            pts_start=1.0,
            pts_end=0.0,
            confidence=0.8,
            reason="r",
            granularity="word",
        )


def test_ocr_result_validation_and_redaction():
    r = OcrResult(boxes=(Q,), texts=("secret text",), confs=(0.9,), scripts=("latin",))
    assert "secret" not in repr(r)
    with pytest.raises(ValueError):
        OcrResult(boxes=(Q,), texts=(), confs=(0.9,), scripts=("latin",))
    with pytest.raises(ValueError):
        OcrResult(boxes=(Q,), texts=("t",), confs=(1.5,), scripts=("latin",))
    with pytest.raises(ValueError):
        OcrResult(boxes=(Q,), texts=("t",), confs=(0.5,), scripts=("not a code",))


def test_runtime_protocols_are_structural():
    class Src:
        def frames(self):
            return iter(())

        def audio_blocks(self):
            return iter(())

        def close(self): ...

    class Sink:
        healthy = True

        def write(self, pts, image, audio_pcm48k): ...
        def close(self): ...

    class Lanes:
        active_lanes = frozenset({Lane.FAST})

        def start(self): ...
        def submit(self, frame): ...
        def poll(self):
            return []

        def close(self): ...

    class Audio:
        def start(self): ...
        def feed(self, chunk): ...
        def close(self): ...

    assert isinstance(Src(), FrameSource) and isinstance(Sink(), FrameSink)
    assert isinstance(Lanes(), LaneHost) and isinstance(Audio(), AudioHost)
    assert not isinstance(object(), LaneHost)


def test_all_data_contracts_are_frozen():
    for obj in (
        mask(),
        msg(),
        verdict(),
        Word("a", 0.0, 1.0),
        LaneResult(lane=Lane.FAST, frame_seq=0, pts=0.0, completed_at=0.0, ok=True),
    ):
        first = dataclasses.fields(obj)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, first, getattr(obj, first))
