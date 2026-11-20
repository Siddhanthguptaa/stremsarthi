"""Privacy lint: every `str` field of a data contract must be classified in contracts/privacy.py
as free text (transient), a machine code, or an opaque id. A new str field fails this test until
the lead classifies it (and logs the decision)."""

import dataclasses
import typing

import pytest

from contracts import privacy
from contracts.chat import ChatMessage, ChatVerdict, ModAction, ModQueueItem
from contracts.detection import Detection, LaneResult, MaskRequest, MuteSpan
from contracts.events import (
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
from contracts.protocols import OcrResult
from contracts.speech import PiiSpan, Transcript, TranscriptWindow, Word

DATA_CONTRACTS = [
    Detection,
    MaskRequest,
    MuteSpan,
    LaneResult,
    ChatMessage,
    ChatVerdict,
    ModQueueItem,
    ModAction,
    SafetyEvent,
    RiskSignal,
    RiskMeter,
    HoldCommand,
    SessionCommand,
    CaptionEvent,
    ConfigChange,
    AllowlistUpdate,
    EnrolledFaceMeta,
    EngineStatus,
    StageTiming,
    PiiSpan,
    Transcript,
    TranscriptWindow,
    Word,
    OcrResult,
]
ALLOWED_FREE_TEXT = {
    "ChatMessage",
    "CaptionEvent",
    "Word",
    "Transcript",
    "AllowlistUpdate",
    "EnrolledFaceMeta",
    "OcrResult",
}


def _is_str_like(tp) -> bool:
    """str, str | None, tuple[str, ...]."""
    if tp is str:
        return True
    if typing.get_origin(tp) is typing.Literal:
        return False
    args = [a for a in typing.get_args(tp) if a is not type(None) and a is not Ellipsis]
    return bool(args) and all(_is_str_like(a) for a in args)


@pytest.mark.parametrize("cls", DATA_CONTRACTS, ids=lambda c: c.__name__)
def test_every_str_field_is_classified(cls):
    hints = typing.get_type_hints(cls)
    str_fields = {f.name for f in dataclasses.fields(cls) if _is_str_like(hints[f.name])}
    unclassified = str_fields - privacy.classified_fields(cls.__name__)
    # tuple[str, ...] of codes (top_sources, lanes_stale) are validated in __post_init__ via check_codes
    unclassified -= {"top_sources", "lanes_stale"}
    assert not unclassified, f"{cls.__name__}: classify {sorted(unclassified)} in contracts/privacy.py"


def test_free_text_only_on_allowed_types():
    assert set(privacy.FREE_TEXT_FIELDS) <= ALLOWED_FREE_TEXT


def test_classified_fields_exist():
    by_name = {c.__name__: c for c in DATA_CONTRACTS}
    for registry in (privacy.FREE_TEXT_FIELDS, privacy.CODE_FIELDS, privacy.ID_FIELDS):
        for cls_name, names in registry.items():
            field_names = {f.name for f in dataclasses.fields(by_name[cls_name])}
            assert names <= field_names, f"{cls_name}: stale entries {sorted(names - field_names)}"


def test_lint_catches_a_stray_str_field():
    @dataclasses.dataclass(frozen=True)
    class Stray:
        note: str

    hints = typing.get_type_hints(Stray)
    str_fields = {f.name for f in dataclasses.fields(Stray) if _is_str_like(hints[f.name])}
    assert str_fields - privacy.classified_fields("Stray") == {"note"}
