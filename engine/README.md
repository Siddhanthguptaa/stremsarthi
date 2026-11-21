# engine/

The always-on process that owns time. Spec: `docs/specs/engine.md`. T0.4 built the deadline core, T1.1 the rest (CPU only).

| File | Role |
|---|---|
| `deadline.py`, `control.py`, `motion.py`, `backpressure.py` | `DeadlineEngine` (ring, N-lane coverage, hold, fence), `Control`, motion projection, request tracker |
| `compositor.py`, `blur.py` | `Compositor`: BLUR / PIXELATE / SOLID on ROI crops, 3 px feather, solid collapse above `engine.max_mask_area_ratio`, PROTECT and HOLD output |
| `slate.py` | English + Hindi slates (Pillow + libraqm, fonts in `assets/`), `SlatePresenter` with the capture high-water-mark fence |
| `session.py` | `Session`: audio ring, 16 kHz tap, tick loop, emit to the `FrameSink`; `audio_mute.py`: sample-accurate mutes |
| `bus.py` | `LocalBus` (typed, bounded, thread-safe) and `ZmqBroker` (XSUB/XPUB on `ipc:///tmp/streamsarthi-bus[-in]`) |
| `publishers.py`, `timing.py` | `SafetyEvent` (masked, held, failsafe), `EngineStatus` at 1 Hz, `StageTiming` JSONL |
| `main.py` | assembler: loads `Config.runtime.modules` (`module:build`), SIGINT/SIGTERM shutdown |
| `testing.py`, `session_fakes.py` | synthetic builders; `SyntheticSource`, `FakeLaneHost`, `RecordingSink`, `build_*` fakes |

```python
engine = DeadlineEngine.from_config(config, lanes=host.active_lanes)
session = Session(config, source, sink, lane_host=host, audio_host=audio, bus=LocalBus())
session.start()  # ingest-video, ingest-audio and tick threads
# tests drive it in virtual time instead: session.on_frame(f, now=t); session.step(t)
```

Run: `scripts/run_engine.sh [dev|demo]` (or `python -m engine.main --profile dev`). Every module in
`runtime.modules` is required; `--skip audio` drops one on purpose. Fakes for all three:
`--module relay=engine.session_fakes:build_relay --module vision=engine.session_fakes:build_vision --module audio=engine.session_fakes:build_audio`.
`STREAMSARTHI_CV_THREADS=2` caps OpenCV's thread pool (it spins and costs CPU by default).
Timing: set `engine.timing_log_path` (or env `ENGINE__TIMING_LOG_PATH`) for the JSONL stage log.

Tests (CPU only, inside WSL through the wrapper):

```
MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /home/siddhanth/stremsarthi/scripts/ss.sh python -m pytest engine -q
MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /home/siddhanth/stremsarthi/scripts/ss.sh ruff check engine
```

Licences: `THIRD_PARTY_LICENSES/irl-shield/LICENSE` (MIT), `THIRD_PARTY_LICENSES/privastream/LICENSE`
(Apache-2.0), fonts (OFL-1.1) with their texts and SHA-256 in `assets/NOTICE.md`.

Captions (T5.4): `captions.py` burns `CaptionEvent` text (Pillow + raqm, Noto fonts) after the masks; toggle with
`captions.enabled`. Shaping goldens: `python -m engine.tests.gen_engine_caption_goldens`, then check the PNGs by eye.
