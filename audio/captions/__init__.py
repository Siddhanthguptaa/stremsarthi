"""Live captions (T5.3): redact muted spans, translate with Sarvam / IndicTrans2 / nothing, time for the overlay.

``audio.captions:build(config, bus)`` returns a ``CaptionService``. See ``docs/specs/audio.md`` 5.9.
"""

from audio.captions.service import CaptionService, build
from audio.captions.settings import MASK, CaptionSettings
from audio.captions.translate import (
    IndicTrans2Translator,
    PassthroughTranslator,
    ResilientTranslator,
    SarvamTranslator,
    build_translator,
)

__all__ = [
    "MASK",
    "CaptionService",
    "CaptionSettings",
    "IndicTrans2Translator",
    "PassthroughTranslator",
    "ResilientTranslator",
    "SarvamTranslator",
    "build",
    "build_translator",
]
