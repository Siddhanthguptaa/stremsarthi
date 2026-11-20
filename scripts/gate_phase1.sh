#!/usr/bin/env bash
# Gate 1: end-to-end pipe with one detector (docs/PLAN.md section 5 G1, docs/specs/eval.md 5.9).
#
# Run inside WSL from anywhere (scripts/ss.sh sets the venv):
#   bash scripts/ss.sh bash scripts/gate_phase1.sh [options]
# Writes eval/reports/<date>-phase1.md (+ .json) and exits non-zero when the gate fails.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/gate_lib.sh
source scripts/gate_lib.sh

usage() {
  cat <<'EOF'
Usage: scripts/gate_phase1.sh [--live-youtube] [--cpu-only] [--require-gpu] [--minutes N]
                              [--engine real|ref|ref-broken] [--viewer-latency-s S] [--quick] [--out DIR] [--plan] [--help]

Gate G1 (PLAN section 5): synthetic stamped sender -> engine (--profile dev) -> MediaMTX `shielded` tap.

Legs, each reading the tap over RTMP (never RTSP) and decoding the optical stamp:
  main (cpu)   120 s: card_absent 10 s, card_static 40 s, card_moving 40 s, card_absent 30 s.
               Pass: delay p95 <= 5.0 s and max <= 5.5 s (capture to tap; egress-socket value too when the
               relay logs it), output fps >= 29, SPECIMEN card unreadable in >= 98 % of card-visible frames
               after a 1.0 s warm-up with 0 Verhoeff-valid OCR reads, A/V offset <= 40 ms, stamp unreadable <= 1 %.
  main (gpu)   the same with gpu.enabled=true, under `flock /tmp/streamsarthi-gpu.lock`. Skipped with a note when
               there is no GPU, unless --require-gpu (then exit 3). Not run with --cpu-only.
  failsafe     SS_FAULT=lane:accurate:hang@t=20s with a card held in view: every frame captured more than 1.0 s
               after the first missed deadline is a slate and 0 frames show the card.
  hold         hold over the real ZeroMQ bus while the card is visible: slate within 1 frame, the last 3.0 s
               are slates (retro window), audio <= -50 dBFS during the hold, release needs newer frames.
  soak         only with --minutes N (N > 0): memory growth <= 10 %, fps, delay percentiles, 0 card frames.
  live         only with --live-youtube: 3 minute run to the unlisted broadcast named by YOUTUBE_STREAM_KEY
               (taken from the environment, never printed). Without the key this leg is skipped, the rest run.

Options:
  --live-youtube          add the 3 minute YouTube leg (viewer latency is informational, never gating)
  --viewer-latency-s S    viewer-side latency you measured by hand; recorded in the report only
  --cpu-only              do not run the GPU leg
  --require-gpu           a missing GPU is exit 3 instead of a skipped leg
  --minutes N             soak length in minutes (default 0 = no soak)
  --engine E              real (default: python -m engine.main), ref or ref-broken (eval/refengine.py, a stand-in
                          used to validate the harness; ref-broken has the fail-safe off and must make the gate FAIL)
  --out DIR               run directory (default eval/runs/phase1-<timestamp>)
  --report-dir DIR        where the report goes (default eval/reports)
  --quick                 45 s main leg instead of 120 s (what gate_final uses); report <date>-phase1-quick.md,
                          no baseline. The full 120 s run is the one that closes the gate.
  --plan                  print the steps that would run and exit 0 without running anything
  -h, --help              this text

Exit: 0 pass, 1 a criterion failed, 2 environment or setup error (nothing trustworthy measured, bad arguments,
missing fault hooks in the engine/vision/relay), 3 a required hardware leg was skipped.
The baseline eval/baselines/phase1.json is written by the first passing --engine real run.
EOF
}

die_usage() { echo "gate_phase1.sh: $1" >&2; echo "try: scripts/gate_phase1.sh --help" >&2; exit 2; }

LIVE=0; CPU_ONLY=0; REQUIRE_GPU=0; MINUTES=0; ENGINE=real; VIEWER=""; OUT=""; PLAN=0; QUICK=0; REPORT_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    -h | --help) usage; exit 0 ;;
    --live-youtube) LIVE=1 ;;
    --cpu-only) CPU_ONLY=1 ;;
    --require-gpu) REQUIRE_GPU=1 ;;
    --plan) PLAN=1 ;;
    --quick) QUICK=1 ;;
    --minutes)
      [ $# -ge 2 ] || die_usage "--minutes needs a number"
      MINUTES="$2"; shift ;;
    --minutes=*) MINUTES="${1#*=}" ;;
    --engine)
      [ $# -ge 2 ] || die_usage "--engine needs real, ref or ref-broken"
      ENGINE="$2"; shift ;;
    --engine=*) ENGINE="${1#*=}" ;;
    --viewer-latency-s)
      [ $# -ge 2 ] || die_usage "--viewer-latency-s needs a number"
      VIEWER="$2"; shift ;;
    --viewer-latency-s=*) VIEWER="${1#*=}" ;;
    --out)
      [ $# -ge 2 ] || die_usage "--out needs a directory"
      OUT="$2"; shift ;;
    --out=*) OUT="${1#*=}" ;;
    --report-dir)
      [ $# -ge 2 ] || die_usage "--report-dir needs a directory"
      REPORT_DIR="$2"; shift ;;
    *) die_usage "unknown argument: $1" ;;
  esac
  shift
done
case "$MINUTES" in '' | *[!0-9.]* | *.*.* | .) die_usage "--minutes must be a non-negative number, got '$MINUTES'" ;; esac
case "$ENGINE" in real | ref | ref-broken) ;; *) die_usage "--engine must be real, ref or ref-broken, got '$ENGINE'" ;; esac
if [ -n "$VIEWER" ]; then
  case "$VIEWER" in *[!0-9.]* | *.*.* | .) die_usage "--viewer-latency-s must be a number, got '$VIEWER'" ;; esac
fi
if [ "$CPU_ONLY" = 1 ] && [ "$REQUIRE_GPU" = 1 ]; then die_usage "--cpu-only and --require-gpu contradict each other"; fi

MINUTES_POS=$(awk -v m="$MINUTES" 'BEGIN{print (m+0 > 0) ? 1 : 0}')
[ -n "$OUT" ] || OUT="eval/runs/phase1-$(date +%Y%m%d-%H%M%S)"
case "$ENGINE" in real) PHASE_NAME=phase1; BASELINE_FLAG="" ;; *) PHASE_NAME="phase1-$ENGINE"; BASELINE_FLAG="--no-baseline" ;; esac
MAIN_EXTRA=()
if [ "$QUICK" = 1 ]; then
  # same checks on a 45 s main leg (10 s absent + 35 s card): for gate_final and for a flaky WSL; never a baseline
  MAIN_EXTRA=(--seconds 45)
  PHASE_NAME="${PHASE_NAME}-quick"; BASELINE_FLAG="--no-baseline"
fi
LOCK="${SS_GPU_LOCK_FILE:-/tmp/streamsarthi-gpu.lock}"  # the override exists for the gate-script tests only
PY=(python -m eval.run phase1 --engine "$ENGINE" --out "$OUT")

# run (or, with --plan, only print) one step
step() {
  if [ "$PLAN" = 1 ]; then
    local okrc=""
    if [ "$1" = "--ok-rc" ]; then okrc=" (exit $2 accepted)"; shift 2; fi
    local name="$1"; shift
    echo "PLAN: [$name]$okrc $*"
  else
    gate_step "$@"
  fi
}
record() {
  if [ "$PLAN" = 1 ]; then echo "PLAN: [$1] (not run) ${3:-}"; else gate_record "$@"; fi
}

if [ "$PLAN" = 0 ]; then
  command -v python >/dev/null 2>&1 || { echo "python not on PATH: run through scripts/ss.sh" >&2; exit 2; }
  mkdir -p "$OUT"
  gate_init "$PHASE_NAME"
fi

# 0. can the chain be built at all? (exit 2 with the reason, nothing is measured)
step "preflight" python -m eval.run phase1 --leg preflight --engine "$ENGINE" --out "$OUT"
if [ "$PLAN" = 0 ] && [ "${GATE_MAPPED[0]}" != 0 ]; then
  echo "preflight failed: the chain cannot be built, so no leg is run (nothing was measured)"
  gate_finish
fi

# 1. main legs
step "main leg (cpu)" "${PY[@]}" --leg main "${MAIN_EXTRA[@]}"
if [ "$CPU_ONLY" = 1 ]; then
  record "main leg (gpu)" 0 "not run: --cpu-only"
else
  gpu_step=(flock -E 2 -w 1800 "$LOCK" env SS_GPU_LOCK_HELD=1 "${PY[@]}" --leg main --gpu "${MAIN_EXTRA[@]}")
  if [ "$REQUIRE_GPU" = 1 ]; then
    step "main leg (gpu)" "${gpu_step[@]}"
  else
    step --ok-rc 3 "main leg (gpu)" "${gpu_step[@]}"
  fi
fi

# 2. fail-safe and hold
step "fail-safe (lane hang)" "${PY[@]}" --leg failsafe
step "hold (bus, retro, fence)" "${PY[@]}" --leg hold

# 3. soak
if [ "$MINUTES_POS" = 1 ]; then
  step "soak ${MINUTES} min" "${PY[@]}" --leg soak --minutes "$MINUTES"
else
  record "soak" 0 "not run: pass --minutes N"
fi

# 4. live YouTube (the key comes from the environment and is never printed)
if [ "$LIVE" = 1 ]; then
  if [ -z "${YOUTUBE_STREAM_KEY:-}" ]; then
    echo "SKIP live-youtube: YOUTUBE_STREAM_KEY is not set in the environment (the other legs are unaffected)"
    record "live YouTube 3 min" 0 "SKIPPED: YOUTUBE_STREAM_KEY not set"
  else
    live_args=("${PY[@]}" --leg live)
    if [ -n "$VIEWER" ]; then live_args+=(--viewer-latency-s "$VIEWER"); fi
    step "live YouTube 3 min" "${live_args[@]}"
  fi
fi

if [ "$PLAN" = 1 ]; then
  echo "PLAN: report -> eval/reports/<date>-${PHASE_NAME}.md (engine=$ENGINE, out=$OUT)"
  exit 0
fi

GATE_REPORT_HOOK=phase1_report
phase1_report() {
  local extra=()
  if [ -n "$REPORT_DIR" ]; then extra+=(--report-dir "$REPORT_DIR"); fi
  python -m eval.run phase1-report --dir "$OUT" --results "$1" --phase-name "$PHASE_NAME" $BASELINE_FLAG "${extra[@]}"
}
gate_finish
