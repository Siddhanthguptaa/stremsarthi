# audio/

Audio path of Stream Sarthi. T0.6 delivers the core: VAD, segmenter and STT adapters. The orchestrator,
PII rules, mute spans and captions come in T3.x and T5.3 (see `docs/specs/audio.md`).

| File | What |
|---|---|
| `vad.py` | Silero VAD on ONNX Runtime CPU (no torch), 512-sample windows, on/off hysteresis from `Config.audio.vad` |
| `segmenter.py` | VAD-cut `Segment`s: 1.5 s cap, cut at the quietest 30 ms, 0.4 s overlap, `voiced` at >= 200 ms speech |
| `stt/base.py` | `SpeechToText` re-export, errors, token bucket, WAV and word-interpolation helpers |
| `stt/sarvam.py` | Sarvam `saaras:v4` codemix, REST per segment, 55 req/min bucket, 1.8 s timeout |
| `stt/groq.py` | Groq `whisper-large-v3-turbo`, word timestamps, 18 req/min, `GroqChunker` (>= 3 s chunks) |
| `stt/local.py` | faster-whisper int8 CPU fallback (adapted from PrivaStream, Apache-2.0) |
| `models.py` | pinned/verified model downloads into `models/weights/audio/` (gitignored) |
| `spikes/stt_spike.py` | latency and capability spike, writes `spikes/REPORT.md` |

Licences: `stt/LICENSE-privastream-Apache-2.0` (PrivaStream), `licenses/silero-vad-MIT.txt` (Silero VAD).

## Run
```
# tests (CPU, no network, no keys); live tests are skipped without keys
python -m pytest audio -q
# models (verified downloads): Silero VAD, whisper tiny/base
python -c "from audio.models import ensure_silero_vad; print(ensure_silero_vad())"
python -m audio.spikes.stt_spike --fetch-only
# spike (SARVAM_API_KEY / GROQ_API_KEY from the process environment enable the API rows)
python -m audio.spikes.stt_spike
```
Test audio is `tests/data/tts_two_sentences.wav` (ffmpeg flite TTS, benign text).
Never log transcript text; adapters raise errors that carry no body text.
