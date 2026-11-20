#!/usr/bin/env bash
# Gate 0: environment, contracts, harness skeleton (docs/PLAN.md section 5, docs/specs/eval.md 5.9).
#
# Run inside WSL from anywhere (the repo venv must be active; scripts/ss.sh does that):
#   bash scripts/ss.sh bash scripts/gate_phase0.sh
#
# Steps: doctor, contracts tests, ruff, eval self-test and unit tests, ffprobe receive check over SRT and
# RTMP, a 60 s loopback smoke (stamp sender -> MediaMTX -> reader), and the STT spike report check.
# Exit: 0 pass, 1 a criterion failed, 2 environment error, 3 required hardware leg skipped.
# No GPU is used, so no flock is needed; GPU gates wrap every GPU step in `flock /tmp/streamsarthi-gpu.lock`.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/gate_lib.sh
source scripts/gate_lib.sh

LOOP_SECONDS="${GATE0_LOOP_SECONDS:-60}"
RUN_DIR=".tmp/gate/phase0"
rm -rf "$RUN_DIR"; mkdir -p "$RUN_DIR"

gate_init phase0
command -v python >/dev/null 2>&1 || { echo "python not on PATH: run through scripts/ss.sh" >&2; exit 2; }

# 1. environment (doctor exit 1 means warnings only, 2 is a blocking failure)
gate_step --ok-rc 1 "doctor" python scripts/doctor.py

# 2. contracts and lint
gate_step "contracts tests" python -m pytest -q contracts/tests
gate_step "ruff check" ruff check .
gate_step "ruff format --check" ruff format --check .

# 3. eval harness
gate_step "eval selftest" python -m eval.run selftest
gate_step "eval unit tests" python -m pytest -q eval/tests

# 4. receive checks against a private MediaMTX (relay/ supervisor takes over in T1.4 when it exists)
if [ -d relay ]; then echo "note: relay/ exists; this gate still drives MediaMTX through eval.harness.procs (T1.4 may switch)"; fi
gate_step "ffprobe receive (SRT ingest)" python -m eval.run ffprobe-check --transport srt --run-dir "$RUN_DIR/ffprobe-srt"
gate_step "ffprobe receive (RTMP ingest)" python -m eval.run ffprobe-check --transport rtmp --run-dir "$RUN_DIR/ffprobe-rtmp"
gate_step "loopback ${LOOP_SECONDS}s (0 drops)" python -m eval.run loopback --seconds "$LOOP_SECONDS" --transport srt --tap rtmp --run-dir "$RUN_DIR/loopback"
GATE_ATTACH_FILES+=("$RUN_DIR/loopback/loopback.json" "$RUN_DIR/ffprobe-srt/ffprobe.json" "$RUN_DIR/ffprobe-rtmp/ffprobe.json")

# 5. STT spike report (T0.6 output)
spike_check() {
  local f=docs/spikes/stt-spike.md
  [ -f "$f" ] || { echo "missing $f (T0.6 output)" >&2; return 1; }
  grep -qi "timestamp" "$f" || { echo "$f does not state whether Sarvam returns timestamps" >&2; return 1; }
  grep -qi "p50" "$f" && grep -qi "p95" "$f" || { echo "$f has no STT p50/p95 latency" >&2; return 1; }
  grep -qi "groq" "$f" && grep -qi "limit" "$f" || { echo "$f has no Groq limits section" >&2; return 1; }
}
gate_step "STT spike report" spike_check

gate_finish
