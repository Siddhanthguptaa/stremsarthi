"""Real faster-whisper tiny on the TTS clip (CPU). Skipped when the package or verified model files are absent."""

from __future__ import annotations

from pathlib import Path

import pytest
import soundfile as sf

from audio.stt.local import LocalWhisperSTT, model_dir_for

CLIP = Path(__file__).parent / "data" / "tts_two_sentences.wav"
SR = 16_000

pytest.importorskip("faster_whisper")
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not (model_dir_for("tiny") / "model.bin").exists(), reason="tiny model not fetched"),
]


async def test_local_tiny_transcribes_clip_with_absolute_word_times():
    x, _ = sf.read(CLIP, dtype="int16")
    seg = x[int(0.5 * SR) : int(2.5 * SR)]  # first sentence, spans source time 0.5 to 2.5 s
    stt = LocalWhisperSTT("tiny", timeout_s=20.0)
    try:
        await stt.warmup()
        tr = await stt.transcribe(seg, 50.5, "en")
    finally:
        await stt.aclose()
    assert "test" in tr.text.lower()
    assert tr.has_word_times and tr.words
    assert all(50.5 <= w.t0 <= w.t1 <= 52.5 for w in tr.words)
    assert tr.words[0].t0 < 51.2 and tr.words[-1].t1 > 51.5  # word times follow the speech, not the segment
