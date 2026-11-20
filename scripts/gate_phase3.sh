#!/usr/bin/env bash
# Gate 3: Hinglish and regional-language spoken PII (docs/PLAN.md section 5 G3, docs/specs/eval.md 5.9, T3.1).
#
# Run inside WSL from anywhere (scripts/ss.sh sets the venv):
#   bash scripts/ss.sh bash scripts/gate_phase3.sh [options]
# Writes eval/reports/<date>-phase3.md (+ .json) and exits non-zero when the gate fails.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/gate_lib.sh
source scripts/gate_lib.sh

usage() {
  cat <<'EOF'
Usage: scripts/gate_phase3.sh [--data DIR] [--out DIR] [--report-dir DIR] [--api-stride N] [--quick]
                              [--defect NAME] [--allow-tone] [--plan] [--help]

Gate G3 (PLAN section 5): spoken PII (phone, OTP, UPI id, address, Aadhaar-like) in Hinglish, Hindi, English,
Kannada, Tamil, Telugu and Bengali. 150 TTS-composed clips (about 500 SPECIMEN PII spans) with ground-truth word
times by construction, noise mixes, number-bearing non-PII distractors and no-PII control clips.

Steps (all CPU: the audio path and the mute book need no GPU, so there is no `flock /tmp/streamsarthi-gpu.lock`
step in this gate; captions are not part of it):
  generate     the dataset when DATA/manifest.json is missing (seeded, needs ffmpeg with flite; exit 2 otherwise).
  data check   sizes, sample hashes, SPECIMEN lint, real TTS engine (not the tone fallback).
  main         APIs up. Every clip through audio.AudioPipeline (Silero VAD, STT race, PII rules) in virtual time at
               real-time pacing, spans applied by engine.audio_mute.MuteBook to the 48 kHz output. Pass: PII mute
               coverage >= 90 %, spoken digits >= 95 %, over-mute <= 15 %, audio pipeline p95 <= 3.3 s, mouth
               blur on >= 95 % of muted frames with an allowlisted face visible.
  api down     every provider fails: all PII muted by the fail-safe and >= 99 % of all voiced speech (plan: 100 %).
  local only   the API provider fails and a lossy local recogniser answers: >= 70 % of PII muted.

THE RECOGNISER IS AN ORACLE built from the ground truth with seeded errors (the offline English-only TTS cannot be
recognised by a real STT), so the gate checks the audio pipeline, the PII rules and the mute application, not
recognition accuracy. The report says so.

Options:
  --data DIR         dataset directory (default eval/data/generated/audio)
  --out DIR          run directory for the leg JSONs (default eval/runs/phase3-<timestamp>)
  --report-dir DIR   where the report goes (default eval/reports)
  --api-stride N     run the api-down and local-only legs on every N-th clip (default 1 = all clips)
  --quick            main leg on every 3rd clip and the failure legs on every 6th; report <date>-phase3-quick.md.
                     The full run is the one that closes the gate.
  --defect NAME      seed a defect (failsafe_off | bad_stt | slow_stt): the gate must then exit 1; report
                     <date>-phase3-defect-NAME.md
  --allow-tone       accept the tone-fallback dataset (unit tests only; never closes the gate)
  --plan             print the steps that would run and exit 0 without running anything
  -h, --help         this text

Exit: 0 pass, 1 a criterion failed, 2 environment or setup error (no dataset and cannot build one, bad arguments,
modules not importable; nothing trustworthy was measured), 3 a required hardware leg was skipped (none here).
EOF
}

die_usage() { echo "gate_phase3.sh: $1" >&2; echo "try: scripts/gate_phase3.sh --help" >&2; exit 2; }

DATA="${SS_AUDIO_DATA:-eval/data/generated/audio}"; OUT=""; REPORT_DIR=""; API_STRIDE=1; QUICK=0; DEFECT=""; TONE=0; PLAN=0
while [ $# -gt 0 ]; do
  case "$1" in
    -h | --help) usage; exit 0 ;;
    --plan) PLAN=1 ;;
    --quick) QUICK=1 ;;
    --allow-tone) TONE=1 ;;
    --data) [ $# -ge 2 ] || die_usage "--data needs a directory"; DATA="$2"; shift ;;
    --data=*) DATA="${1#*=}" ;;
    --out) [ $# -ge 2 ] || die_usage "--out needs a directory"; OUT="$2"; shift ;;
    --out=*) OUT="${1#*=}" ;;
    --report-dir) [ $# -ge 2 ] || die_usage "--report-dir needs a directory"; REPORT_DIR="$2"; shift ;;
    --api-stride) [ $# -ge 2 ] || die_usage "--api-stride needs a number"; API_STRIDE="$2"; shift ;;
    --api-stride=*) API_STRIDE="${1#*=}" ;;
    --defect) [ $# -ge 2 ] || die_usage "--defect needs a name"; DEFECT="$2"; shift ;;
    --defect=*) DEFECT="${1#*=}" ;;
    *) die_usage "unknown argument: $1" ;;
  esac
  shift
done
case "$API_STRIDE" in '' | *[!0-9]* | 0) die_usage "--api-stride must be a positive integer, got '$API_STRIDE'" ;; esac
case "$DEFECT" in '' | failsafe_off | bad_stt | slow_stt) ;; *) die_usage "--defect must be failsafe_off, bad_stt or slow_stt, got '$DEFECT'" ;; esac

[ -n "$OUT" ] || OUT="eval/runs/phase3-$(date +%Y%m%d-%H%M%S)"
PHASE_NAME=phase3
MAIN_STRIDE=1
if [ "$QUICK" = 1 ]; then PHASE_NAME="${PHASE_NAME}-quick"; MAIN_STRIDE=3; [ "$API_STRIDE" -ge 6 ] || API_STRIDE=6; fi
[ -z "$DEFECT" ] || PHASE_NAME="${PHASE_NAME}-defect-${DEFECT}"
PY=(python -m eval.phase3 --data "$DATA" --out "$OUT")
[ "$TONE" = 0 ] || PY+=(--allow-tone)
DEF=()
[ -z "$DEFECT" ] || DEF=(--defect "$DEFECT")

step() {
  if [ "$PLAN" = 1 ]; then
    local name="$1"; shift
    echo "PLAN: [$name] $*"
  else
    gate_step "$@"
  fi
}

GATE_REPORT_HOOK=phase3_report
phase3_report() {
  local extra=()
  [ -z "$REPORT_DIR" ] || extra+=(--report-dir "$REPORT_DIR")
  python -m eval.phase3 --leg report --out "$OUT" --results "$1" --phase-name "$PHASE_NAME" "${extra[@]}"
}

if [ "$PLAN" = 0 ]; then
  command -v python >/dev/null 2>&1 || { echo "python not on PATH: run through scripts/ss.sh" >&2; exit 2; }
  mkdir -p "$OUT"
  gate_init "$PHASE_NAME"
fi

# 1. dataset (a missing dataset is built; an unbuildable one is exit 2: nothing was measured)
GEN=(python -m eval.data_gen.audio --out "$DATA")
[ "$TONE" = 0 ] || GEN+=(--engine tone)
if [ "$PLAN" = 1 ]; then
  echo "PLAN: [generate audio set] (only when $DATA/manifest.json is missing) ${GEN[*]}"
elif [ ! -f "$DATA/manifest.json" ]; then
  step "generate audio set" "${GEN[@]}"
else
  gate_record "generate audio set" 0 "dataset already present"
fi
step "data check" "${PY[@]}" --leg data
if [ "$PLAN" = 0 ] && [ "${GATE_MAPPED[${#GATE_MAPPED[@]}-1]}" != 0 ]; then
  echo "dataset missing or invalid: no leg is run (nothing was measured)"
  gate_finish
fi

# 2. legs
step "main (APIs up)" "${PY[@]}" --leg main --stride "$MAIN_STRIDE" "${DEF[@]}"
step "API down: fail-safe" "${PY[@]}" --leg api_down --stride "$API_STRIDE" "${DEF[@]}"
step "API down: local recogniser" "${PY[@]}" --leg local_only --stride "$API_STRIDE" "${DEF[@]}"

if [ "$PLAN" = 1 ]; then
  echo "PLAN: report -> eval/reports/<date>-${PHASE_NAME}.md (data=$DATA, out=$OUT)"
  exit 0
fi
gate_finish
