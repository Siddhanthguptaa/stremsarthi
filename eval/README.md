# eval

Synthetic SPECIMEN data, the stamp sender and reader, the measurement harness and the gate helpers.
Spec: `docs/specs/eval.md`. All commands run inside WSL through `scripts/ss.sh` (see `docs/AGENT_BRIEF.md`).

```
python -m eval.run --selftest            # harness self-test (CPU, offline, ~15 s)
python -m pytest eval/tests -m "not slow" # unit tests (slow = MediaMTX chains and the OCR test)
python -m eval.run loopback --seconds 60 # sender -> MediaMTX -> reader: drops, gaps, delay, A/V offset
python -m eval.run gen-video --out /tmp/video --n 40 --clips 2   # T2.1 video suite (images, 30 fps clips, truth)
bash scripts/gate_phase0.sh              # gate 0 (exit 0 pass, 1 fail, 2 env error, 3 hardware leg skipped)
bash scripts/gate_phase1.sh --help|--plan|--engine ref   # gate 1: delay at the egress tap, fail-safe, hold, soak
python -m eval.run phase1 --leg main --engine ref|ref-broken|real [--gpu]   # one leg (main failsafe hold soak live)
bash scripts/gate_phase2.sh --help|--plan|--quick   # gate 2: per-category recall by level, flicker, perf, NOT RUN
python -m eval.run phase2 --leg preflight|offline|rescore|perf --data /tmp/ss_phase2_video --out DIR
bash scripts/gate_phase2_acceptance.sh   # gate 2 must fail on seeded defects (blind, crashing, absent detector)
bash scripts/gate_phase3.sh --help|--plan|--quick   # gate 3: spoken PII (python -m eval.phase3 legs; --reference --defect X)
bash scripts/gate_phase4.sh --help|--plan           # gate 4: chat shield and mock platforms (python -m eval.phase4 legs)
python -m eval.data_gen.audio --out DIR; python -m eval.data_gen.chat --out DIR   # phase 3 and 4 datasets
```

Layout
- `harness/`: `timing.py`, `metrics.py` (recall, Wilson CI, mute coverage, delay, flicker), `report.py`
  (`eval/reports/<date>-<phase>.md` + JSON), `gpu_lock.py`, `procs.py` (private MediaMTX, CPU sampler), `microbench.py`.
- Red team (T7.2): `redteam/` (audio and chat suites, `python -m eval.run redteam-audio|redteam-chat`, gate `scripts/gate_redteam_audiochat.sh`); see `redteam/README.md`.
- `sender/`: optical stamp (256x32 px top-left, CRC-8), beep, scenes (`card_static`, `card_moving`, `card_hold`
  = still, slowly moving, tilted), sender (SRT via ffmpeg, RTMP/files via PyAV), `reader.py`.
- Gate 1 (T1.4): `tap.py`, `cardcheck.py` (a Verhoeff-valid OCR read is a leak), `delay.py`, `fault.py` (SS_FAULT),
  `holdbus.py`, `stack.py` (sender -> engine -> tap), `phase1.py`, `phase1_report.py`, `refengine.py` (stand-in engine).
- Gate 2 (T2.10): `phase2/` (`rows` targets, `dataset`, `detectors` registry loader: missing/broken = NOT RUN, `runner`,
  `scoring`, `offline`, `perf`, `report`, `seeded`, `acceptance`). Scoring is mask coverage; allowlisted faces have a
  warm-up rule; the ACCURATE lane is judged at the production OCR budget. Details: `eval/docs/gate2.md`.
- `data_gen/`: `common/` (Verhoeff, SPECIMEN rules, seeds, fonts), `video/` (T2.1: `suite.py` buckets, `clips.py`,
  `augment.py`; see its docstrings for the truth format; `python -m eval.data_gen.video --help`).

Rules: every card carries a SPECIMEN watermark; Aadhaar numbers are Verhoeff-valid and start with 0 or 1;
PAN 4th character is X; UPI ids are `zz-specimen-<hex>@...`. Datasets are seeded and regenerate bit-for-bit.
Nothing real, no frame or text is stored at runtime (timing logs and records carry numbers and ids only).

Notes
- Delay is read on an RTMP or SRT tap, never RTSP (RTSP pull gives a false A/V offset). The stamp is never masked.
- A GPU leg runs under `flock /tmp/streamsarthi-gpu.lock` (the gate takes it and sets `SS_GPU_LOCK_HELD=1`).
- Third-party: `eval/LICENSES/` (IRL Shield MIT, PrivaStream Apache-2.0); `microbench.py` from PrivaStream.

## Blind held-out chat set (Gate 4, informational)
`bash scripts/ss.sh bash scripts/gate_phase4.sh --heldout` (or `python -m eval.run phase4 --heldout`) scores the chat
classifier on `eval/data/private/chat_heldout_v2/` (gitignored; about 1,500 messages in a different style and seed from
the Gate 4 set; the generator is kept in the same private directory). It is never read by `chat/` code, tests or
agents, and reports carry aggregates only (recall, hard-flag-only recall, HOLD_FOR_MOD counts, by kind, language and
difficulty). It never gates. Code: `eval/phase4_heldout.py`; tests use a toy set (`eval/tests/test_phase4_heldout_harness.py`).

## Judge demo reel (SPECIMEN only)
`bash scripts/ss.sh bash scripts/demo_assets.sh` builds `/tmp/streamsarthi-demo` (flite audio track + timeline, contact sheet, chat plan) and prints the
run commands. Video: `python -m eval.sender.sender --script demo_reel --audio-file /tmp/streamsarthi-demo/demo_reel.wav --url ...` (90 s, ten
captioned scenes: `eval/sender/demo_scenes.py`; the file track replaces the beep). Audio: `eval/data_gen/demo_audio.py`. Chat: `python -m eval.demo.chat_feeder`
(mock YouTube chat + the real chat process; a drop-in for `chat.main` through `SESSION_CHAT_CMD`). Tests: `eval/tests/test_demo_*.py`.
