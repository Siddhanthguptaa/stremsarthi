# eval

Synthetic SPECIMEN data, the stamp sender and reader, the measurement harness and the gate helpers.
Spec: `docs/specs/eval.md`. All commands run inside WSL through `scripts/ss.sh` (see `docs/AGENT_BRIEF.md`).

```
python -m eval.run --selftest            # harness self-test (CPU, offline, ~15 s)
python -m pytest eval/tests -m "not slow" # unit tests (slow = MediaMTX chains and the OCR test)
python -m eval.run loopback --seconds 60 # sender -> MediaMTX -> reader: drops, gaps, delay, A/V offset
bash scripts/gate_phase0.sh              # gate 0 (exit 0 pass, 1 fail, 2 env error, 3 hardware leg skipped)
bash scripts/gate_phase1.sh --help       # gate 1: delay at the egress tap, card unreadable, fail-safe, hold, soak
bash scripts/gate_phase1.sh --plan       # print the steps without running them
bash scripts/gate_phase1.sh --engine ref # validate the gate against eval/refengine.py (no real engine needed)
python -m eval.run phase1 --leg main --engine ref|ref-broken|real [--gpu]   # one leg (main failsafe hold soak live)
```

Layout
- `harness/`: `timing.py`, `metrics.py` (recall, Wilson CI, mute coverage, delay, flicker), `report.py`
  (`eval/reports/<date>-<phase>.md` + JSON), `gpu_lock.py`, `procs.py` (private MediaMTX, CPU sampler), `microbench.py`.
- `sender/`: optical stamp (256x32 px top-left, CRC-8), beep, scenes (`card_static`, `card_moving`, `card_hold`
  = still, slowly moving, tilted), sender (SRT via ffmpeg, RTMP/files via PyAV), `reader.py`.
- Gate 1 (T1.4): `tap.py` (reads the `shielded` path over RTMP, decodes stamps, scores the card), `cardcheck.py`
  (structure test + RapidOCR: a Verhoeff-valid 12-digit read counts as a leak), `delay.py` (delay percentiles, fps,
  StageTiming JSONL, alignment to the engine timing log), `fault.py` (SS_FAULT protocol, stdlib only),
  `holdbus.py` (hold over the real ZeroMQ bus), `stack.py` (sender -> engine -> tap chain), `phase1.py` (legs and
  checks), `phase1_report.py`, `refengine.py` (stand-in engine; `--broken` has the fail-safe off).
- `data_gen/`: `common/` (Verhoeff, SPECIMEN rules, seeds), `video/` (cards, UPI QR; others by T2.1).

Rules: every card carries a SPECIMEN watermark; Aadhaar numbers are Verhoeff-valid and start with 0 or 1;
PAN 4th character is X; UPI ids are `zz-specimen-<hex>@...`. Datasets are seeded and regenerate bit-for-bit.
Nothing real, no frame or text is stored at runtime (timing logs carry numbers only).

Notes
- Delay is read on an RTMP or SRT tap, never RTSP (RTSP pull gives a false A/V offset). The stamp is never masked.
- A GPU leg runs under `flock /tmp/streamsarthi-gpu.lock` (the gate takes it and sets `SS_GPU_LOCK_HELD=1`).
- Third-party: `eval/LICENSES/` (IRL Shield MIT, PrivaStream Apache-2.0); `microbench.py` adapted from PrivaStream.
