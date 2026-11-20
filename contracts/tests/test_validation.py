import dataclasses

import pytest

from contracts import (
    CaptionEvent,
    Category,
    ChatAction,
    ChatMessage,
    ChatVerdict,
    Detection,
    HoldCommand,
    HoldSource,
    MaskKind,
    MaskRequest,
    MaskStyle,
    Modality,
    MuteSpan,
    Quad,
    RiskSignal,
    SafetyEvent,
    StageTiming,
)

Q = Quad.from_xyxy(0, 0, 10, 10)


def det(**kw):
    base = dict(
        detector="d", category=Category.FACE, quad=Q, confidence=0.5, frame_seq=0, pts=0.0, completed_at=0.0
    )
    base.update(kw)
    return Detection(**base)


def test_confidence_bounds():
    for bad in (-0.01, 1.01):
        with pytest.raises(ValueError):
            det(confidence=bad)
    det(confidence=0.0)
    det(confidence=1.0)


def test_instances_are_frozen():
    d = det()
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.confidence = 0.9  # type: ignore[misc]


def test_detection_extra_strings_must_be_codes():
    det(extra={"script": "devanagari", "n": 3})
    with pytest.raises(ValueError):
        det(extra={"text": "mera number nau aath saat"})  # spaces: looks like free text


def test_detector_id_must_be_code():
    with pytest.raises(ValueError):
        det(detector="has spaces")


def test_mask_request_rules():
    kw = dict(
        request_id="r",
        source="s",
        category=Category.FACE,
        frame_pts=0.0,
        pts_start=0.0,
        pts_end=1.0,
        style=MaskStyle.BLUR,
        confidence=0.5,
        reason="x",
        completed_at=0.0,
    )
    MaskRequest(kind=MaskKind.REGION, quads=(Q,), **kw)
    with pytest.raises(ValueError):
        MaskRequest(kind=MaskKind.REGION, quads=(), **kw)
    with pytest.raises(ValueError):
        MaskRequest(kind=MaskKind.FULL_FRAME, quads=(Q,), **kw)
    with pytest.raises(ValueError):
        MaskRequest(kind=MaskKind.FULL_FRAME, quads=(), **{**kw, "pts_end": -1.0})
    MaskRequest(kind=MaskKind.FULL_FRAME, quads=(), **kw)


def test_mute_span_rules():
    kw = dict(
        span_id="s",
        category=Category.SPOKEN_PHONE,
        granularity="word",
        confidence=0.5,
        reason="r",
        completed_at=0.0,
    )
    MuteSpan(pts_start=1.0, pts_end=1.0, **kw)
    with pytest.raises(ValueError):
        MuteSpan(pts_start=2.0, pts_end=1.0, **kw)
    with pytest.raises(ValueError):
        MuteSpan(pts_start=0.0, pts_end=1.0, **{**kw, "granularity": "sentence"})
    with pytest.raises(ValueError):
        MuteSpan(pts_start=0.0, pts_end=1.0, **{**kw, "reason": "the user said a phone number"})


def test_chat_verdict_rules():
    base = dict(platform="twitch", message_id="m", confidence=0.9, reason="r", stage="rules", latency_ms=1.0)
    ChatVerdict(action=ChatAction.ALLOW, categories=(), **base)
    with pytest.raises(ValueError):
        ChatVerdict(action=ChatAction.DELETE, categories=(), **base)
    with pytest.raises(ValueError):
        ChatVerdict(action=ChatAction.TIMEOUT, categories=(Category.CHAT_ABUSE,), **base)
    ChatVerdict(action=ChatAction.TIMEOUT, categories=(Category.CHAT_ABUSE,), timeout_seconds=60, **base)
    with pytest.raises(ValueError):
        ChatVerdict(action=ChatAction.HIDE_FROM_STREAMER, categories=(Category.FACE,), **base)


def test_chat_message_repr_never_prints_text():
    m = ChatMessage(
        platform="youtube",
        message_id="m",
        channel_id="c",
        author_id="a",
        author_display="Someone Real",
        text="secret words",
        received_at=0.0,
    )
    assert "secret" not in repr(m) and "Someone" not in repr(m)


def test_caption_repr_never_prints_text():
    c = CaptionEvent(pts_start=0.0, pts_end=1.0, lang="hi", text="गोपनीय", is_final=True)
    assert "गोपनीय" not in repr(c)
    with pytest.raises(ValueError):
        CaptionEvent(pts_start=2.0, pts_end=1.0, lang="hi", text="x", is_final=True)


def test_safety_event_rejects_free_text_detail():
    kw = dict(
        event_id="e",
        session_id="s",
        wall_ts="2026-11-20T10:00:00+00:00",
        pts=None,
        modality=Modality.AUDIO,
        category=Category.SPOKEN_PHONE,
        action="muted",
        confidence=0.9,
        bbox=None,
        duration_ms=100,
        source="audio",
    )
    SafetyEvent(detail_code="digit_run", **kw)
    with pytest.raises(ValueError):
        SafetyEvent(detail_code="nine eight seven six", **kw)
    with pytest.raises(ValueError):
        SafetyEvent(detail_code="x", **{**kw, "action": "exploded"})


def test_risk_signal_and_timing_and_hold():
    RiskSignal(
        wall_ts="t", pts=None, source="music", category=Category.MUSIC, score=1.0, ttl_s=1.0, label="m"
    )
    with pytest.raises(ValueError):
        RiskSignal(
            wall_ts="t", pts=None, source="music", category=Category.MUSIC, score=1.2, ttl_s=1.0, label="m"
        )
    with pytest.raises(ValueError):
        RiskSignal(
            wall_ts="t", pts=None, source="music", category=Category.MUSIC, score=0.5, ttl_s=0, label="m"
        )
    with pytest.raises(ValueError):
        StageTiming(stage="s", seq=0, pts=None, t_start=2.0, t_end=1.0)
    with pytest.raises(ValueError):
        HoldCommand(source=HoldSource.API, active=True, actor="Real Name")
