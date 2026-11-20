"""Minimal built-in ``PiiSpanDetector`` used until ``audio.pii`` (T3.3) exists: long spoken number runs only."""

from __future__ import annotations

import importlib
import logging
from typing import Any

from audio.orchestrator.mute import is_number_token
from contracts.enums import Category
from contracts.protocols import PiiSpanDetector
from contracts.speech import PiiSpan, TranscriptWindow, Word

log = logging.getLogger("audio.orchestrator")


class DigitRunPiiDetector:
    """Flags runs of number tokens worth >= ``min_digits`` digits (10 digits: phone, otherwise other PII)."""

    def __init__(self, min_digits: int = 6, max_gap_s: float = 1.0) -> None:
        self.min_digits = min_digits
        self.max_gap_s = max_gap_s

    async def detect(self, window: TranscriptWindow) -> list[PiiSpan]:
        out: list[PiiSpan] = []
        run: list[int] = []  # indices into window.words

        def close() -> None:
            if not run:
                return
            ws: list[Word] = [window.words[i] for i in run]
            digits = sum(len(w.text) if w.text.isdigit() else 1 for w in ws)
            if digits >= self.min_digits and run[-1] >= window.new_from_index:
                cat = Category.SPOKEN_PHONE if digits == 10 else Category.SPOKEN_OTHER_PII
                gran = "segment" if any(w.approx for w in ws) else "word"
                out.append(PiiSpan(cat, ws[0].t0, ws[-1].t1, 0.6, "fallback_digit_run", gran))
            run.clear()

        for i, w in enumerate(window.words):
            if is_number_token(w.text) and (not run or w.t0 - window.words[run[-1]].t1 < self.max_gap_s):
                run.append(i)
            else:
                close()
                if is_number_token(w.text):
                    run.append(i)
        close()
        return out


def load_pii_detector(config: Any) -> PiiSpanDetector:
    """``audio.pii`` if it exports ``build_detector(config)`` (or ``build``), else the built-in fallback."""
    try:
        mod = importlib.import_module("audio.pii")
    except ImportError:
        log.warning("audio.pii is not available: using the built-in digit-run detector")
        return DigitRunPiiDetector()
    except Exception as exc:  # a half-written module must not stop the audio path
        log.warning("audio.pii failed to import (%s): using the built-in digit-run detector", type(exc).__name__)
        return DigitRunPiiDetector()
    for name in ("build_detector", "build"):
        factory = getattr(mod, name, None)
        if callable(factory):
            try:
                det = factory(config)
            except Exception as exc:
                log.warning("audio.pii.%s failed (%s): using the fallback", name, type(exc).__name__)
                break
            if isinstance(det, PiiSpanDetector):
                return det
    log.warning("audio.pii has no usable detector factory: using the built-in digit-run detector")
    return DigitRunPiiDetector()
