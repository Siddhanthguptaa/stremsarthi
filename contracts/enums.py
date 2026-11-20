"""Stable enums shared by every module.

Change control: only the lead edits this file. Every change is logged in
docs/DECISIONS.md and the snapshot in contracts/tests/test_enum_snapshot.py is updated
in the same commit. Values are wire-stable strings: never rename, only add.
"""

from __future__ import annotations

from enum import StrEnum


class Category(StrEnum):
    # --- video PII ---
    FACE = "face"
    AADHAAR = "aadhaar"
    PAN = "pan"
    UPI_QR = "upi_qr"
    PLATE = "plate"
    SIGNBOARD = "signboard"
    PIN_CODE = "pin_code"
    PHONE_NUMBER = "phone_number"
    UPI_ID = "upi_id"
    EMAIL = "email"
    OTP_SCREEN = "otp_screen"
    GAME_ID = "game_id"
    ROOM_CREDENTIAL = "room_credential"
    RISKY_TEXT = "risky_text"
    # --- audio PII ---
    SPOKEN_PHONE = "spoken_phone"
    SPOKEN_UPI = "spoken_upi"
    SPOKEN_ADDRESS = "spoken_address"
    SPOKEN_OTHER_PII = "spoken_other_pii"
    SPOKEN_UNVERIFIED = "spoken_unverified"  # fail-safe mute of voiced speech with no verdict
    # --- chat ---
    CHAT_ABUSE = "chat_abuse"
    CHAT_DOXXING = "chat_doxxing"
    CHAT_SCAM_LINK = "chat_scam_link"
    CHAT_FAKE_UPI = "chat_fake_upi"
    # --- strike guard ---
    MUSIC = "music"
    COPYRIGHT_MATCH = "copyright_match"
    PROFANITY = "profanity"
    # --- system ---
    HOLD = "hold"
    FAILSAFE = "failsafe"


class Modality(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"
    CHAT = "chat"
    GUARD = "guard"
    SYSTEM = "system"


class MaskStyle(StrEnum):
    BLUR = "blur"
    PIXELATE = "pixelate"
    SOLID = "solid"


class MaskKind(StrEnum):
    REGION = "region"
    FULL_FRAME = "full_frame"
    # Engine resolves this to the mouth quad of allowlisted (visible) faces, taken from
    # Detection.aux_quads["mouth"], for frames inside [pts_start, pts_end].
    MOUTH_OF_SPEAKER = "mouth_of_speaker"


class Lane(StrEnum):
    FAST = "fast"  # >= 8 Hz: faces, QR, game-id regions
    ACCURATE = "accurate"  # 3-5 Hz: OCR, cards, plates, signs, screens
    SLOW = "slow"  # <= 1 Hz


class Device(StrEnum):
    CPU = "cpu"
    GPU_OPTIONAL = "gpu_optional"
    GPU_ONLY = "gpu_only"


class ChatAction(StrEnum):
    ALLOW = "allow"
    HIDE_FROM_STREAMER = "hide"
    HOLD_FOR_MOD = "hold_for_mod"
    DELETE = "delete"
    TIMEOUT = "timeout"


class HoldSource(StrEnum):
    HOTKEY = "hotkey"
    API = "api"
    CHAT = "chat"
    APP = "app"
    AUTO_TRANSPORT = "auto_transport"
    AUTO_EGRESS = "auto_egress"


class Topic(StrEnum):
    """Event-bus topics (stable strings; ZeroMQ PUB/SUB prefix or InProcessBus key)."""

    # hot path, in-process only
    DETECTION = "video.detection"
    MASK_REQUEST = "video.mask"
    MUTE_SPAN = "audio.mute"
    TRANSCRIPT = "audio.transcript"  # raw transcript text: never leaves the engine process
    # cross-process telemetry and control
    SAFETY_EVENT = "safety.event"
    RISK = "guard.risk"
    RISK_METER = "guard.meter"
    CAPTION = "caption.event"
    CHAT_ALLOWED = "chat.allowed"
    CHAT_VERDICT = "chat.verdict"
    CHAT_MODQUEUE = "chat.modqueue"
    CHAT_MODACTION = "chat.modaction"
    HOLD = "control.hold"
    CONFIG = "control.config"
    ALLOWLIST = "control.allowlist"
    SESSION = "control.session"
    ENGINE_STATUS = "engine.status"
    TIMING = "telemetry.timing"


# Never bridged to ZeroMQ: hot-path topics stay inside the engine process.
LOCAL_ONLY_TOPICS: frozenset[Topic] = frozenset(
    {Topic.DETECTION, Topic.MASK_REQUEST, Topic.MUTE_SPAN, Topic.TRANSCRIPT}
)
# A slow subscriber may lose telemetry, but never a hold command.
NEVER_DROP_TOPICS: frozenset[Topic] = frozenset({Topic.HOLD})
