"""Caption settings: the contract's ``Config.captions`` plus the tuning knobs that have no contract field yet.

``Config.captions`` only has ``enabled``, ``target_lang``, ``provider``, ``max_chars_per_line`` and
``on_late``. The rest (timeouts, rate budget, settle time) live here with the defaults of docs/specs/audio.md 5.9,
so no contract change is needed. Add them to ``CaptionsConfig`` if they should become configurable per profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

MASK = "▮▮▮"


@dataclass(frozen=True, slots=True)
class CaptionSettings:
    # from Config.captions / Config
    enabled: bool = False
    target_lang: str = "hi"
    provider: Literal["sarvam", "indictrans2"] = "sarvam"
    max_chars_per_line: int = 42
    on_late: Literal["skip", "show_source"] = "skip"
    delay_s: float = 4.0
    profile: str = "dev"
    # timing (spec 5.9)
    max_lines: int = 2
    min_show_s: float = 1.2
    max_show_s: float = 4.0
    tail_s: float = 0.3  # pts_end = last word t1 + tail
    soft_margin_s: float = 0.3  # translation must be ready by pts_start + D - soft_margin
    translate_timeout_s: float = 0.8
    # redaction
    settle_s: float = 0.25  # wait for the mute verdict of a transcript before it is captioned
    min_overlap_s: float = 0.06  # a word is masked when a mute covers >= this much of it, or half of it
    local_rules: bool = True  # also mask what the spoken-PII rules flag, even if the mute has not arrived
    mask_token: str = MASK
    # cost and rate budget for the paid API (Sarvam: 2 INR per 1,000 characters)
    rpm: float = 45.0
    burst: int = 3
    chars_per_min: int = 2400
    session_char_cap: int = 150_000  # about 300 INR per session
    breaker_failures: int = 3
    breaker_open_s: float = 20.0
    # bounded memory: recent words kept for overlap removal and for late-mute corrections
    keep_s: float = 12.0

    @classmethod
    def from_config(cls, config: Any, **overrides: Any) -> CaptionSettings:
        c = config.captions
        base: dict[str, Any] = {
            "enabled": c.enabled,
            "target_lang": c.target_lang,
            "provider": c.provider,
            "max_chars_per_line": c.max_chars_per_line,
            "on_late": c.on_late,
            "delay_s": float(config.delay_seconds),
            "profile": str(getattr(config, "profile", "dev")),
        }
        base.update(overrides)
        return cls(**base)
