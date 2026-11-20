"""JSON wire codec for contract dataclasses (cross-process bus, logs, tests).

Envelope: ``{"t": "<ClassName>", "v": <WIRE_VERSION>, "d": {...}}``. Decoding is strict: unknown
types, versions, fields, enum values and bad ``Literal`` values raise ``ContractError``.
Hot-path and transient types (Frame, AudioChunk, LaneResult, Transcript, ...) are never
serialised. Messages over ``MAX_WIRE_BYTES`` are rejected.
"""

from __future__ import annotations

import collections.abc
import json
import types
import typing
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any

from .chat import ChatMessage, ChatVerdict, ModAction, ModQueueItem
from .detection import Detection, MaskRequest, MuteSpan
from .enums import LOCAL_ONLY_TOPICS, Topic
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
from .speech import Transcript

WIRE_VERSION = 1
MAX_WIRE_BYTES = 256 * 1024


class ContractError(ValueError):
    """A wire payload violates the contracts."""


WIRE_TYPES: dict[str, type] = {
    cls.__name__: cls
    for cls in (
        Quad,
        Detection,
        MaskRequest,
        MuteSpan,
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
    )
}

# What each topic carries (validated by the buses).
TOPIC_TYPES: dict[Topic, type] = {
    Topic.DETECTION: Detection,
    Topic.MASK_REQUEST: MaskRequest,
    Topic.MUTE_SPAN: MuteSpan,
    Topic.TRANSCRIPT: Transcript,
    Topic.SAFETY_EVENT: SafetyEvent,
    Topic.RISK: RiskSignal,
    Topic.RISK_METER: RiskMeter,
    Topic.CAPTION: CaptionEvent,
    Topic.CHAT_ALLOWED: ChatMessage,
    Topic.CHAT_VERDICT: ChatVerdict,
    Topic.CHAT_MODQUEUE: ModQueueItem,
    Topic.CHAT_MODACTION: ModAction,
    Topic.HOLD: HoldCommand,
    Topic.CONFIG: ConfigChange,
    Topic.ALLOWLIST: AllowlistUpdate,
    Topic.SESSION: SessionCommand,
    Topic.ENGINE_STATUS: EngineStatus,
    Topic.TIMING: StageTiming,
}
assert set(TOPIC_TYPES) == set(Topic), "every topic needs a payload type"

# Topics that may be bridged to ZeroMQ (everything not engine-process-local).
WIRE_TOPICS: dict[Topic, type] = {t: c for t, c in TOPIC_TYPES.items() if t not in LOCAL_ONLY_TOPICS}


def _enc(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        if type(value).__name__ not in WIRE_TYPES:
            raise TypeError(f"{type(value).__name__} is not a wire contract and cannot be serialised")
        return {f.name: _enc(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (tuple, list)):
        return [_enc(v) for v in value]
    if isinstance(value, collections.abc.Mapping):
        return {str(k.value if isinstance(k, Enum) else k): _enc(v) for k, v in value.items()}
    if hasattr(value, "dtype") and hasattr(value, "shape"):
        raise TypeError("arrays cannot be serialised on the wire")
    return value


def _dec(tp: Any, value: Any) -> Any:
    if tp is Any:
        return value
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        if value is None:
            return None
        if len(args) == 1:
            return _dec(args[0], value)
        return value  # unions of primitives
    if origin is typing.Literal:
        if value not in typing.get_args(tp):
            raise ContractError(f"{value!r} not in {typing.get_args(tp)}")
        return value
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ContractError(f"expected number, got {value!r}")
        return float(value)
    if isinstance(tp, type) and issubclass(tp, Enum):
        try:
            return tp(value)
        except ValueError as exc:
            raise ContractError(f"{value!r} is not a valid {tp.__name__}") from exc
    if isinstance(tp, type) and is_dataclass(tp):
        return _dec_dataclass(tp, value)
    if origin is tuple:
        args = typing.get_args(tp)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_dec(args[0], v) for v in value)
        return tuple(_dec(a, v) for a, v in zip(args, value, strict=True))
    if origin in (collections.abc.Mapping, dict):
        args = typing.get_args(tp)
        val_tp = args[1] if len(args) == 2 else Any
        return {k: _dec(val_tp, v) for k, v in value.items()}
    return value


def _dec_dataclass(cls: type, data: Any) -> Any:
    if not isinstance(data, dict):
        raise ContractError(f"{cls.__name__} expects an object")
    hints = typing.get_type_hints(cls)
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ContractError(f"unknown fields for {cls.__name__}: {sorted(unknown)}")
    try:
        return cls(**{name: _dec(hints[name], data[name]) for name in data})
    except ContractError:
        raise
    except (TypeError, ValueError) as exc:
        raise ContractError(f"invalid {cls.__name__}: {exc}") from exc


def to_wire(obj: Any) -> dict[str, Any]:
    name = type(obj).__name__
    if name not in WIRE_TYPES:
        raise TypeError(f"{name} is not a wire contract (hot-path and transient types never serialise)")
    return {"t": name, "v": WIRE_VERSION, "d": _enc(obj)}


def from_wire(payload: dict[str, Any]) -> Any:
    name = payload.get("t")
    if name not in WIRE_TYPES:
        raise ContractError(f"unknown wire type {name!r}")
    if payload.get("v") != WIRE_VERSION:
        raise ContractError(f"unsupported wire version {payload.get('v')!r}")
    return _dec_dataclass(WIRE_TYPES[name], payload["d"])


def dumps(obj: Any) -> bytes:
    data = json.dumps(to_wire(obj), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_WIRE_BYTES:
        raise ContractError(f"wire message of {len(data)} bytes exceeds {MAX_WIRE_BYTES}")
    return data


def loads(data: bytes | str) -> Any:
    size = len(data.encode("utf-8")) if isinstance(data, str) else len(data)
    if size > MAX_WIRE_BYTES:
        raise ContractError(f"wire message of {size} bytes exceeds {MAX_WIRE_BYTES}")
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid JSON: {exc}") from exc
    return from_wire(payload)
