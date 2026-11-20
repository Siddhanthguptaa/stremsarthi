"""One valid sample of every wire contract. Used by tests, and by the web app's type-drift
tests via ``python -m contracts.samples <outdir>`` (writes ``<Type>.json`` per sample)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from . import CONTRACT_VERSION
from .chat import ChatMessage, ChatVerdict, ModAction, ModQueueItem
from .detection import Detection, MaskRequest, MuteSpan
from .enums import Category, ChatAction, HoldSource, MaskKind, MaskStyle, Modality
from .events import (
    AllowlistUpdate,
    CaptionEvent,
    ConfigChange,
    EngineStatus,
    EnrolledFaceMeta,
    HoldCommand,
    RiskMeter,
    RiskSignal,
    SafetyEvent,
    SessionCommand,
    StageTiming,
)
from .geometry import Quad
from .wire import dumps

Q = Quad.from_xyxy(10, 20, 110, 80)


def samples() -> list[Any]:
    msg = ChatMessage(
        platform="twitch",
        message_id="m1",
        channel_id="c1",
        author_id="u1",
        author_display="viewer one",
        text="hello there",
        received_at=1.7e9,
    )
    verdict = ChatVerdict(
        platform="twitch",
        message_id="m1",
        action=ChatAction.TIMEOUT,
        categories=(Category.CHAT_ABUSE,),
        confidence=0.91,
        reason="abuse_list_hit",
        stage="rules",
        latency_ms=3.2,
        timeout_seconds=300,
    )
    return [
        Q,
        Detection(
            detector="cards",
            category=Category.AADHAAR,
            quad=Q,
            confidence=0.93,
            frame_seq=7,
            pts=0.233,
            completed_at=12.5,
            track_id=3,
            aux_quads={"mouth": Q},
            extra={"verhoeff": True, "score": 0.5, "script": "latin"},
        ),
        MaskRequest(
            request_id="r-1",
            source="cards",
            category=Category.AADHAAR,
            kind=MaskKind.REGION,
            quads=(Q, Q),
            frame_pts=0.2,
            pts_start=0.2,
            pts_end=0.5,
            style=MaskStyle.SOLID,
            confidence=0.9,
            reason="verhoeff_ok",
            completed_at=1.0,
        ),
        MuteSpan(
            span_id="s-1",
            pts_start=3.0,
            pts_end=4.4,
            category=Category.SPOKEN_PHONE,
            granularity="segment",
            confidence=0.88,
            reason="digit_run_10",
            completed_at=5.0,
        ),
        msg,
        verdict,
        ModQueueItem(message=msg, verdict=verdict, enqueued_at=1.7e9, state="pending"),
        ModAction(message_id="m1", platform="twitch", action="timeout", timeout_seconds=300, actor="h_ab12"),
        SafetyEvent(
            event_id="e1",
            session_id="sess-1",
            wall_ts="2026-11-20T10:00:00+00:00",
            pts=12.0,
            modality=Modality.VIDEO,
            category=Category.UPI_QR,
            action="masked",
            confidence=0.97,
            bbox=Q,
            duration_ms=1200,
            source="upi_qr",
            detail_code="upi_scheme",
        ),
        RiskSignal(
            wall_ts="2026-11-20T10:00:01+00:00",
            pts=None,
            source="fingerprint",
            category=Category.COPYRIGHT_MATCH,
            score=0.9,
            ttl_s=10.0,
            label="fp:demo_03",
        ),
        RiskMeter(wall_ts="2026-11-20T10:00:01+00:00", level="red", score=0.95, top_sources=("fingerprint",)),
        HoldCommand(source=HoldSource.CHAT, active=True, actor="h_ab12"),
        SessionCommand(action="start", actor="h_ab12"),
        CaptionEvent(pts_start=1.0, pts_end=2.0, lang="hi", text="नमस्ते", is_final=True),
        ConfigChange(
            diff={"categories.face.enabled": False, "audio.languages": ["hi", "en"]}, actor="h_ab12"
        ),
        AllowlistUpdate(op="add", face_id="f1", label="Teammate", embedding=(0.1, -0.2, 0.3), version=2),
        EnrolledFaceMeta(
            face_id="f1", label="Teammate", created_at="2026-11-20T10:00:00+00:00", expires_at=None
        ),
        EngineStatus(
            state="live",
            hold_active=False,
            hold_sources=(),
            delay_s_p50=4.6,
            fps=29.9,
            cpu_cores=4.2,
            vram_mb=None,
            allowlist_version=2,
            lanes_stale=(),
            protect_seconds=0.0,
            contract_version=CONTRACT_VERSION,
        ),
        StageTiming(stage="lane_fast", seq=9, pts=0.3, t_start=1.0, t_end=1.02),
    ]


def write_samples(outdir: Path) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for obj in samples():
        path = outdir / f"{type(obj).__name__}.json"
        path.write_bytes(dumps(obj))
        written.append(path)
    return written


if __name__ == "__main__":  # pragma: no cover
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "contracts/tests/wire_samples")
    for p in write_samples(target):
        print(p)
