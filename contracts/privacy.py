"""Privacy classification of every string field in the data contracts.

Rule (CLAUDE.md, Data and privacy): events carry codes, ids, times and boxes, never raw
text, images or PII. Every ``str`` field of a data contract must be classified here:

* FREE_TEXT_FIELDS: transient, in-memory content that may hold user text or names. Never
  logged or stored. Only chat text/author names, caption text, transcript text, enrolled-face
  labels and OCR text are allowed. Their classes redact them in ``repr``.
* CODE_FIELDS: machine codes, e.g. ``verhoeff_ok``, ``fp:demo_03``. Validated against
  CODE_RE (no spaces, at most 64 chars). A best-effort guard, not proof of no PII.
* ID_FIELDS: opaque identifiers, validated against ID_RE.

``contracts/tests/test_no_free_text.py`` fails when a new ``str`` field is added without being
classified, which forces a decision (and a DECISIONS.md entry).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import is_dataclass
from typing import Any

CODE_RE = re.compile(r"^[A-Za-z0-9_:.\-/]{0,64}$")
ID_RE = re.compile(r"^[A-Za-z0-9_:.\-+]{1,128}$")  # '+' allows ISO-8601 offsets in wall_ts

FREE_TEXT_FIELDS: dict[str, frozenset[str]] = {
    "ChatMessage": frozenset({"text", "author_display"}),
    "CaptionEvent": frozenset({"text"}),
    "Word": frozenset({"text"}),
    "Transcript": frozenset({"text"}),
    "AllowlistUpdate": frozenset({"label"}),
    "EnrolledFaceMeta": frozenset({"label"}),
    "OcrResult": frozenset({"texts"}),  # tuple[str, ...]: free text, one frame only
}

CODE_FIELDS: dict[str, frozenset[str]] = {
    "Detection": frozenset({"detector"}),
    "MaskRequest": frozenset({"source", "reason"}),
    "MuteSpan": frozenset({"reason"}),
    "ChatVerdict": frozenset({"reason"}),
    "SafetyEvent": frozenset({"source", "detail_code"}),
    "RiskSignal": frozenset({"label"}),
    "PiiSpan": frozenset({"reason"}),
    "StageTiming": frozenset({"stage"}),
    "CaptionEvent": frozenset({"lang"}),
    "Transcript": frozenset({"lang"}),
    "EngineStatus": frozenset({"contract_version"}),
    "OcrResult": frozenset({"scripts"}),  # validated per entry via check_codes
}

ID_FIELDS: dict[str, frozenset[str]] = {
    "MaskRequest": frozenset({"request_id"}),
    "MuteSpan": frozenset({"span_id"}),
    "ChatMessage": frozenset({"message_id", "channel_id", "author_id"}),
    "ChatVerdict": frozenset({"message_id", "platform"}),
    "ModAction": frozenset({"message_id", "platform", "actor"}),
    "SafetyEvent": frozenset({"event_id", "session_id", "wall_ts"}),
    "RiskSignal": frozenset({"wall_ts"}),
    "RiskMeter": frozenset({"wall_ts"}),
    "HoldCommand": frozenset({"actor"}),
    "ConfigChange": frozenset({"actor"}),
    "SessionCommand": frozenset({"actor"}),
    "AllowlistUpdate": frozenset({"face_id"}),
    "EnrolledFaceMeta": frozenset({"face_id", "created_at", "expires_at"}),
}


def check_codes(owner: str, field_name: str, values: Iterable[str]) -> None:
    """Validate a collection of machine codes (for tuple-typed fields)."""
    for v in values:
        if not isinstance(v, str) or not CODE_RE.match(v):
            raise ValueError(f"{owner}.{field_name} entries must be machine codes")


def validate_instance(obj: Any) -> None:
    """Validate CODE and ID fields of a dataclass instance. Raises ValueError."""
    if not is_dataclass(obj):
        return
    name = type(obj).__name__
    for fname in CODE_FIELDS.get(name, ()):
        value = getattr(obj, fname, None)
        if isinstance(value, str) and not CODE_RE.match(value):
            raise ValueError(f"{name}.{fname} must be a machine code (no spaces, <=64 chars)")
    for fname in ID_FIELDS.get(name, ()):
        value = getattr(obj, fname, None)
        if isinstance(value, str) and not ID_RE.match(value):
            raise ValueError(f"{name}.{fname} must be an opaque id")
    # string values inside `extra` mappings are codes too
    extra = getattr(obj, "extra", None)
    if isinstance(extra, dict):
        for k, v in extra.items():
            if isinstance(v, str) and not CODE_RE.match(v):
                raise ValueError(f"{name}.extra[{k!r}] must be a machine code")


def classified_fields(cls_name: str) -> frozenset[str]:
    return (
        FREE_TEXT_FIELDS.get(cls_name, frozenset())
        | CODE_FIELDS.get(cls_name, frozenset())
        | ID_FIELDS.get(cls_name, frozenset())
    )


__all__ = [
    "CODE_FIELDS",
    "CODE_RE",
    "FREE_TEXT_FIELDS",
    "ID_FIELDS",
    "ID_RE",
    "check_codes",
    "classified_fields",
    "validate_instance",
]
