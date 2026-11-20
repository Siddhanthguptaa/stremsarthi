"""Live provider checks. Skipped unless the key is in the process environment (never read from .env here).

Run with: SARVAM_API_KEY=... GROQ_API_KEY=... pytest audio/tests/test_audio_stt_live.py -m live
The audio is a benign flite TTS clip (no personal data).
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from audio.stt.groq import GroqSTT
from audio.stt.sarvam import SarvamSTT

CLIP = Path(__file__).parent / "data" / "tts_two_sentences.wav"
SR = 16_000


def _cut(start_s: float, dur_s: float) -> np.ndarray:
    x, _ = sf.read(CLIP, dtype="int16")
    return x[int(start_s * SR) : int((start_s + dur_s) * SR)]


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("SARVAM_API_KEY"), reason="SARVAM_API_KEY not set")
async def test_live_sarvam_rest_segment():
    stt = SarvamSTT(os.environ["SARVAM_API_KEY"], timeout_s=5.0)
    try:
        tr = await stt.transcribe(_cut(0.5, 1.5), 10.0, None)
    finally:
        await stt.aclose()
    assert tr.provider == "sarvam" and tr.text and tr.latency_s < 5.0


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("GROQ_API_KEY"), reason="GROQ_API_KEY not set")
async def test_live_groq_chunk_has_word_times():
    stt = GroqSTT(os.environ["GROQ_API_KEY"], timeout_s=5.0)
    try:
        tr = await stt.transcribe(_cut(0.5, 3.1), 10.0, "en")
    finally:
        await stt.aclose()
    assert tr.provider == "groq" and tr.text and tr.has_word_times
    assert all(10.0 <= w.t0 <= w.t1 <= 13.1 for w in tr.words)
