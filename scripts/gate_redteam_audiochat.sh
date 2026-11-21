#!/usr/bin/env bash
# Red-team gate for audio and chat (docs/PLAN.md T7.2, docs/specs/eval.md 5.8).
#
# Run inside WSL (scripts/ss.sh sets the venv):
#   bash scripts/ss.sh bash scripts/gate_redteam_audiochat.sh [options]
# Writes eval/reports/<date>-redteam-chat.md, <date>-redteam-audio.md and <date>-redteam-audiochat.md and exits non-zero
# when the gate fails.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/gate_lib.sh
source scripts/gate_lib.sh

usage() {
  cat <<'EOF'
Usage: scripts/gate_redteam_audiochat.sh [--quick] [--record-only] [--per-label N] [--per-technique N]
                                         [--report-dir DIR] [--reference [--defect NAME]] [--plan] [--help]

Red team of the audio and chat modules. Two steps, both CPU only (no GPU, so no flock step):
  chat   evasion attacks (homoglyphs, zero-width, leetspeak, split words, mixed scripts, encodings, visual tricks,
         URL and UPI obfuscation, staff impersonation, padding, prompt injection), plus !hold spoofing, replayed
         message ids, a 4,000 message flood, malformed and huge messages, and a log/event scan for message text.
  audio  spoken PII said awkwardly (fillers, code-mixed numbers, spelling, long pauses, whisper and noise, fast
         speech, overlapping speakers, music, split numbers, phonetic UPI ids, STT 429/hang/empty/down, ASR drops),
         leak read from the OUTPUT audio after the engine's mute book.
Pass: every hard invariant holds (no crash, no text in logs, no !hold from non-staff, no replay leak, ...) AND every
miss rate is under its category ceiling (eval/redteam/*/run.py, eval/redteam/chat/attacks.py CEILINGS).

Options:
  --quick             chat --per-label 4, audio --per-technique 1 (a small subset)
  --record-only       record miss rates but fail only on hard invariants (what T7.2 itself runs: the fixes are T7.5)
  --per-label N       chat: base messages per label (default 12)
  --per-technique N   audio: clips per technique (default 2)
  --report-dir DIR    where the suite reports go (default eval/reports)
  --reference         run the stand-ins that are right by construction (validates the harness: must pass)
  --defect NAME       with --reference, seed a defect (chat: bypass_all | leak_logs | hold_nonstaff;
                      audio: late_spans | no_failsafe): must exit 1; only the suite that knows the defect runs
  --plan              print the steps and exit 0 without running anything
  -h, --help          this text

Exit: 0 pass, 1 a criterion failed, 2 environment or setup error (nothing measured), 3 a required hardware leg was
skipped (none here).
EOF
}

die_usage() { echo "gate_redteam_audiochat.sh: $1" >&2; echo "try: scripts/gate_redteam_audiochat.sh --help" >&2; exit 2; }

QUICK=0; RECORD=0; PER_LABEL=12; PER_TECH=2; REPORT_DIR=""; REF=0; DEFECT=""; PLAN=0
while [ $# -gt 0 ]; do
  case "$1" in
    -h | --help) usage; exit 0 ;;
    --plan) PLAN=1 ;;
    --quick) QUICK=1 ;;
    --record-only) RECORD=1 ;;
    --reference) REF=1 ;;
    --per-label) [ $# -ge 2 ] || die_usage "--per-label needs a number"; PER_LABEL="$2"; shift ;;
    --per-technique) [ $# -ge 2 ] || die_usage "--per-technique needs a number"; PER_TECH="$2"; shift ;;
    --report-dir) [ $# -ge 2 ] || die_usage "--report-dir needs a directory"; REPORT_DIR="$2"; shift ;;
    --defect) [ $# -ge 2 ] || die_usage "--defect needs a name"; DEFECT="$2"; shift ;;
    --defect=*) DEFECT="${1#*=}" ;;
    *) die_usage "unknown argument: $1" ;;
  esac
  shift
done
case "$PER_LABEL" in '' | *[!0-9]* | 0) die_usage "--per-label must be a positive integer, got '$PER_LABEL'" ;; esac
case "$PER_TECH" in '' | *[!0-9]* | 0) die_usage "--per-technique must be a positive integer, got '$PER_TECH'" ;; esac
case "$DEFECT" in
  '' | bypass_all | leak_logs | hold_nonstaff | late_spans | no_failsafe) ;;
  *) die_usage "--defect must be bypass_all, leak_logs, hold_nonstaff, late_spans or no_failsafe, got '$DEFECT'" ;;
esac
[ -z "$DEFECT" ] || [ "$REF" = 1 ] || die_usage "--defect needs --reference"
if [ "$QUICK" = 1 ]; then PER_LABEL=4; PER_TECH=1; fi

PHASE_NAME=redteam-audiochat
[ "$REF" = 0 ] || PHASE_NAME="${PHASE_NAME}-reference"
[ -z "$DEFECT" ] || PHASE_NAME="${PHASE_NAME}-defect-${DEFECT}"
COMMON=()
[ "$REPORT_DIR" = "" ] || COMMON+=(--report-dir "$REPORT_DIR")
[ "$RECORD" = 0 ] || COMMON+=(--record-only)
[ "$REF" = 0 ] || COMMON+=(--reference)
[ -z "$DEFECT" ] || COMMON+=(--defect "$DEFECT")
SUFFIX=""
[ "$REF" = 0 ] || SUFFIX="-reference"
[ -z "$DEFECT" ] || SUFFIX="${SUFFIX}-defect-${DEFECT}"
CHAT=(python -m eval.run redteam-chat --per-label "$PER_LABEL" --phase-name "redteam-chat${SUFFIX}" "${COMMON[@]}")
AUDIO=(python -m eval.run redteam-audio --per-technique "$PER_TECH" --phase-name "redteam-audio${SUFFIX}" "${COMMON[@]}")
RUN_CHAT=1; RUN_AUDIO=1
case "$DEFECT" in
  bypass_all | leak_logs | hold_nonstaff) RUN_AUDIO=0 ;;
  late_spans | no_failsafe) RUN_CHAT=0 ;;
esac

step() {
  if [ "$PLAN" = 1 ]; then local name="$1"; shift; echo "PLAN: [$name] $*"; else gate_step "$@"; fi
}

if [ "$PLAN" = 0 ]; then
  command -v python >/dev/null 2>&1 || { echo "python not on PATH: run through scripts/ss.sh" >&2; exit 2; }
  gate_init "$PHASE_NAME"
fi
[ "$RUN_CHAT" = 0 ] || step "red team: chat" "${CHAT[@]}"
[ "$RUN_AUDIO" = 0 ] || step "red team: audio" "${AUDIO[@]}"
if [ "$PLAN" = 1 ]; then echo "PLAN: report -> eval/reports/<date>-${PHASE_NAME}.md"; exit 0; fi
gate_finish
