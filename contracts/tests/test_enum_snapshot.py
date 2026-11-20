"""Enum values are wire-stable. Adding a value means updating this snapshot in the same commit
(lead only, logged in docs/DECISIONS.md). Removing or renaming a value is forbidden."""

from contracts import enums

SNAPSHOT = {
    "Category": {
        "face",
        "aadhaar",
        "pan",
        "upi_qr",
        "plate",
        "signboard",
        "pin_code",
        "phone_number",
        "upi_id",
        "email",
        "otp_screen",
        "game_id",
        "room_credential",
        "risky_text",
        "spoken_phone",
        "spoken_upi",
        "spoken_address",
        "spoken_other_pii",
        "spoken_unverified",
        "chat_abuse",
        "chat_doxxing",
        "chat_scam_link",
        "chat_fake_upi",
        "music",
        "copyright_match",
        "profanity",
        "hold",
        "failsafe",
        "system_status",
    },
    "Modality": {"video", "audio", "chat", "guard", "system"},
    "MaskStyle": {"blur", "pixelate", "solid"},
    "MaskKind": {"region", "full_frame", "mouth_of_speaker"},
    "Lane": {"fast", "accurate", "slow"},
    "Device": {"cpu", "gpu_optional", "gpu_only"},
    "ChatAction": {"allow", "hide", "hold_for_mod", "delete", "timeout"},
    "HoldSource": {"hotkey", "api", "chat", "app", "auto_transport", "auto_egress"},
    "Topic": {
        "video.detection",
        "video.mask",
        "audio.mute",
        "audio.transcript",
        "safety.event",
        "guard.risk",
        "guard.meter",
        "caption.event",
        "chat.allowed",
        "chat.verdict",
        "chat.modqueue",
        "chat.modaction",
        "control.hold",
        "control.config",
        "control.allowlist",
        "control.session",
        "engine.status",
        "telemetry.timing",
    },
}


def test_enum_values_match_snapshot():
    for name, expected in SNAPSHOT.items():
        assert {m.value for m in getattr(enums, name)} == expected, name


def test_no_unsnapshotted_enums():
    from enum import StrEnum

    found = {
        n
        for n, v in vars(enums).items()
        if isinstance(v, type) and issubclass(v, StrEnum) and v is not StrEnum
    }
    assert found == set(SNAPSHOT)
