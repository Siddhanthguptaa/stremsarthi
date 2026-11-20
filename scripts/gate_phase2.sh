#!/usr/bin/env bash
# Gate 2: the Indian video layer (docs/PLAN.md section 5 G2, docs/specs/eval.md 5.9, task T2.10).
#
# Run inside WSL from anywhere (scripts/ss.sh sets the venv):
#   bash scripts/ss.sh bash scripts/gate_phase2.sh [options]
# Writes eval/reports/<date>-phase2.md (+ .json) and exits non-zero when the gate fails.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/gate_lib.sh
source scripts/gate_lib.sh

usage() {
  cat <<'EOF'
Usage: scripts/gate_phase2.sh [--n N] [--clips N] [--data DIR] [--seed S] [--regen] [--offline-only] [--cpu-only]
                              [--require-gpu] [--engine real|ref|ref-broken] [--perf-seconds S] [--quick]
                              [--gate-levels L] [--gate-sources S] [--out DIR] [--report-dir DIR] [--plan] [--help]

Gate G2 (PLAN section 5): every video category, with tracking.

Steps:
  dataset      generate the T2.1 SPECIMEN suite under --data (default /tmp/ss_phase2_video) when it is missing or was
               made with other parameters (seeded: the same seed regenerates the same suite).
  preflight    dataset readable, vision registry importable, which detectors exist. Missing detectors are reported
               and become NOT RUN rows; the gate stops here only when nothing at all could be measured (exit 2).
  offline      accuracy, CPU, no stream, faster than real time: every detector the registry supplies over the images
               and the 30 fps clips. Per category, frame-level recall with a 95 % Wilson interval, by difficulty
               level (clean, light, heavy), stills and clips separately. Initial targets: faces 95 % masked and
               allowlisted faces visible >= 95 %; Aadhaar 95 %; PAN 95 %; UPI QR 98 %; signboard text 85 %;
               phone, PIN, UPI ID, e-mail 90 % each; OTP screens 85 %; game IDs 90 %; plates 85 %; mask flicker on
               static targets <= 2 %. A detector that is missing, broken or cannot load is NOT RUN (never a pass).
  perf (cpu)   real chain with ALL detectors on (many_targets scene, 120 s): fps >= 29, delay p95 <= 5.0 s and
               max <= 5.5 s, CPU <= 5 cores, a card unreadable in >= 98 % of visible frames, delay not worse than
               eval/baselines/phase1.json by more than 0.2 s (NOT RUN, and so a failed gate, while no baseline exists).
  perf (gpu)   the same under `flock /tmp/streamsarthi-gpu.lock`, plus VRAM growth <= 3.5 GB. Skipped with a note
               when there is no GPU unless --require-gpu (then exit 3).

Options:
  --n N                images per bucket (default 40; 26 buckets)        --clips N   clips per clip bucket (default 4)
  --data DIR           dataset directory (default /tmp/ss_phase2_video)   --seed S    generator seed (default 1234)
  --regen              regenerate the dataset even if it matches
  --offline-only       skip the real-time legs (accuracy only; the gate cannot close without them)
  --cpu-only           do not run the GPU perf leg
  --require-gpu        a missing GPU is exit 3 instead of a skipped leg
  --engine E           real (default: python -m engine.main), ref or ref-broken (eval/refengine.py stand-ins used to
                       validate the harness; they have no vision host, so "all detectors on" is NOT RUN)
  --perf-seconds S     length of the perf legs (default 120)
  --quick              8 images per bucket, 45 s perf legs, lower sample minimums; report <date>-phase2-quick.md, no
                       baseline. The full run is the one that closes the gate.
  --gate-levels L      comma list of clean,light,heavy the recall checks use (default all three; every level is
                       always reported)
  --gate-sources S     img,clip (default both)
  --out DIR            run directory (default eval/runs/phase2-<timestamp>)
  --report-dir DIR     where the report goes (default eval/reports)
  --plan               print the steps that would run and exit 0 without running anything
  -h, --help           this text

Exit: 0 pass, 1 a criterion failed or is NOT RUN, 2 environment or setup error (nothing trustworthy measured, bad
arguments, dataset cannot be built), 3 a required hardware leg was skipped.
The baseline eval/baselines/phase2.json is written by the first passing full run.
EOF
}

die_usage() { echo "gate_phase2.sh: $1" >&2; echo "try: scripts/gate_phase2.sh --help" >&2; exit 2; }

N=""; CLIPS=""; DATA=""; SEED=1234; REGEN=0; OFFLINE_ONLY=0; CPU_ONLY=0; REQUIRE_GPU=0; ENGINE=real; PERF_S=""
QUICK=0; LEVELS=""; SOURCES="img,clip"; OUT=""; REPORT_DIR=""; PLAN=0
need_arg() { [ "$2" -ge 2 ] || die_usage "$1 needs a value"; }
while [ $# -gt 0 ]; do
  case "$1" in
    -h | --help) usage; exit 0 ;;
    --n) need_arg "$1" $#; N="$2"; shift ;;
    --n=*) N="${1#*=}" ;;
    --clips) need_arg "$1" $#; CLIPS="$2"; shift ;;
    --clips=*) CLIPS="${1#*=}" ;;
    --data) need_arg "$1" $#; DATA="$2"; shift ;;
    --data=*) DATA="${1#*=}" ;;
    --seed) need_arg "$1" $#; SEED="$2"; shift ;;
    --seed=*) SEED="${1#*=}" ;;
    --regen) REGEN=1 ;;
    --offline-only) OFFLINE_ONLY=1 ;;
    --cpu-only) CPU_ONLY=1 ;;
    --require-gpu) REQUIRE_GPU=1 ;;
    --engine) need_arg "$1" $#; ENGINE="$2"; shift ;;
    --engine=*) ENGINE="${1#*=}" ;;
    --perf-seconds) need_arg "$1" $#; PERF_S="$2"; shift ;;
    --perf-seconds=*) PERF_S="${1#*=}" ;;
    --quick) QUICK=1 ;;
    --gate-levels) need_arg "$1" $#; LEVELS="$2"; shift ;;
    --gate-levels=*) LEVELS="${1#*=}" ;;
    --gate-sources) need_arg "$1" $#; SOURCES="$2"; shift ;;
    --gate-sources=*) SOURCES="${1#*=}" ;;
    --out) need_arg "$1" $#; OUT="$2"; shift ;;
    --out=*) OUT="${1#*=}" ;;
    --report-dir) need_arg "$1" $#; REPORT_DIR="$2"; shift ;;
    --report-dir=*) REPORT_DIR="${1#*=}" ;;
    --plan) PLAN=1 ;;
    *) die_usage "unknown argument: $1" ;;
  esac
  shift
done

is_uint() { case "$1" in '' | *[!0-9]*) return 1 ;; *) return 0 ;; esac; }
is_num() { case "$1" in '' | *[!0-9.]* | *.*.* | .) return 1 ;; *) return 0 ;; esac; }

if [ "$QUICK" = 1 ]; then
  : "${N:=8}"; : "${CLIPS:=4}"; : "${PERF_S:=45}"; : "${DATA:=/tmp/ss_phase2_video_quick}"
  MIN_N=5; MIN_WIN=10
else
  : "${N:=40}"; : "${CLIPS:=4}"; : "${PERF_S:=120}"; : "${DATA:=/tmp/ss_phase2_video}"
  MIN_N=20; MIN_WIN=20
fi
is_uint "$N" && [ "$N" -ge 1 ] || die_usage "--n must be a positive integer, got '$N'"
is_uint "$CLIPS" || die_usage "--clips must be a non-negative integer, got '$CLIPS'"
is_uint "$SEED" || die_usage "--seed must be a non-negative integer, got '$SEED'"
is_num "$PERF_S" || die_usage "--perf-seconds must be a number, got '$PERF_S'"
case "$ENGINE" in real | ref | ref-broken) ;; *) die_usage "--engine must be real, ref or ref-broken, got '$ENGINE'" ;; esac
if [ -n "$LEVELS" ]; then
  case "$LEVELS" in *[!a-z,]*) die_usage "--gate-levels must be a comma list of clean,light,heavy, got '$LEVELS'" ;; esac
  IFS=',' read -r -a _lv <<<"$LEVELS"
  for x in "${_lv[@]}"; do case "$x" in clean | light | heavy) ;; *) die_usage "--gate-levels: unknown level '$x'" ;; esac; done
fi
case "$SOURCES" in img | clip | img,clip | clip,img) ;; *) die_usage "--gate-sources must be img, clip or img,clip, got '$SOURCES'" ;; esac
if [ "$CPU_ONLY" = 1 ] && [ "$REQUIRE_GPU" = 1 ]; then die_usage "--cpu-only and --require-gpu contradict each other"; fi
if [ "$CLIPS" = 0 ] && [ "$SOURCES" != img ]; then die_usage "--clips 0 leaves no clips to score: use --gate-sources img"; fi

[ -n "$OUT" ] || OUT="eval/runs/phase2-$(date +%Y%m%d-%H%M%S)"
PHASE_NAME=phase2; BASELINE_FLAG=""
if [ "$ENGINE" != real ]; then PHASE_NAME="phase2-$ENGINE"; BASELINE_FLAG="--no-baseline"; fi
if [ "$QUICK" = 1 ]; then PHASE_NAME="${PHASE_NAME}-quick"; BASELINE_FLAG="--no-baseline"; fi
if [ "$OFFLINE_ONLY" = 1 ]; then PHASE_NAME="${PHASE_NAME}-offline"; BASELINE_FLAG="--no-baseline"; fi
LOCK="${SS_GPU_LOCK_FILE:-/tmp/streamsarthi-gpu.lock}"  # the override exists for the gate-script tests only

OFFLINE_ARGS=(--min-n "$MIN_N" --min-windows "$MIN_WIN" --gate-sources "$SOURCES")
if [ -n "$LEVELS" ]; then OFFLINE_ARGS+=(--gate-levels "$LEVELS"); fi
GEN=(python -m eval.run gen-video --out "$DATA" --seed "$SEED" --n "$N" --clips "$CLIPS")
CHECK=(python -m eval.run phase2 --leg dataset-check --data "$DATA" --seed "$SEED" --n "$N" --clips "$CLIPS")
P2=(python -m eval.run phase2 --data "$DATA" --out "$OUT")

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

GATE_REPORT_HOOK=phase2_report
phase2_report() {
  local extra=()
  if [ -n "$REPORT_DIR" ]; then extra+=(--report-dir "$REPORT_DIR"); fi
  python -m eval.run phase2-report --dir "$OUT" --results "$1" --phase-name "$PHASE_NAME" $BASELINE_FLAG "${extra[@]}"
}

if [ "$PLAN" = 0 ]; then
  command -v python >/dev/null 2>&1 || { echo "python not on PATH: run through scripts/ss.sh" >&2; exit 2; }
  mkdir -p "$OUT"
  gate_init "$PHASE_NAME"
fi

# 1. the dataset (seeded; generated only when missing or made with other parameters)
if [ "$PLAN" = 1 ]; then
  echo "PLAN: [dataset] ${GEN[*]}   (only when ${CHECK[*]} fails, or with --regen)"
else
  if [ "$REGEN" = 1 ] || ! "${CHECK[@]}" >/dev/null 2>&1; then
    gate_step "generate dataset" "${GEN[@]}"
    if [ "${GATE_MAPPED[0]}" != 0 ]; then
      echo "the dataset could not be generated, so nothing can be measured"
      gate_finish
    fi
  else
    gate_record "dataset" 0 "reused $DATA (n=$N, clips=$CLIPS, seed=$SEED)"
  fi
fi

# 2. preflight: exit 2 with the reasons when nothing can be measured
step "preflight" "${P2[@]}" --leg preflight
if [ "$PLAN" = 0 ] && [ "${GATE_MAPPED[$((${#GATE_MAPPED[@]} - 1))]}" != 0 ]; then
  echo "preflight failed: no leg is run (nothing was measured)"
  gate_finish
fi

# 3. offline accuracy
step "offline accuracy (cpu)" "${P2[@]}" --leg offline "${OFFLINE_ARGS[@]}"

# 4. real-time legs with all detectors on
if [ "$OFFLINE_ONLY" = 1 ]; then
  record "perf (cpu)" 1 "NOT RUN: --offline-only"
  record "perf (gpu)" 1 "NOT RUN: --offline-only"
else
  step "perf (cpu, all detectors)" "${P2[@]}" --leg perf --engine "$ENGINE" --seconds "$PERF_S"
  if [ "$CPU_ONLY" = 1 ]; then
    record "perf (gpu)" 0 "not run: --cpu-only"
  else
    gpu_step=(flock -E 2 -w 1800 "$LOCK" env SS_GPU_LOCK_HELD=1 "${P2[@]}" --leg perf --gpu --engine "$ENGINE" --seconds "$PERF_S")
    if [ "$REQUIRE_GPU" = 1 ]; then
      step "perf (gpu, all detectors)" "${gpu_step[@]}"
    else
      step --ok-rc 3 "perf (gpu, all detectors)" "${gpu_step[@]}"
    fi
  fi
fi

if [ "$PLAN" = 1 ]; then
  echo "PLAN: report -> eval/reports/<date>-${PHASE_NAME}.md (engine=$ENGINE, data=$DATA, out=$OUT)"
  exit 0
fi

gate_finish
