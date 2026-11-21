#!/usr/bin/env bash
# Gate 4: chat shield (docs/PLAN.md section 5 G4, docs/specs/eval.md 5.9, T4.1).
#
# Run inside WSL from anywhere (scripts/ss.sh sets the venv):
#   bash scripts/ss.sh bash scripts/gate_phase4.sh [options]
# Writes eval/reports/<date>-phase4.md (+ .json) and exits non-zero when the gate fails.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/gate_lib.sh
source scripts/gate_lib.sh

usage() {
  cat <<'EOF2'
Usage: scripts/gate_phase4.sh [--data DIR] [--out DIR] [--report-dir DIR] [--reference [--defect NAME]]
                              [--no-llm-leg] [--heldout] [--plan] [--help]

Gate G4 (PLAN section 5): about 2,000 labelled SPECIMEN chat messages (Hinglish in Latin and Devanagari, Kannada,
Tamil, Telugu and Bengali abuse; threats; scam links; fake UPI payment lures; doxxing; hard negatives such as
Scunthorpe-style substrings, legitimate links and harmless numbers; broadcaster and moderator messages), written
independently of chat/classify, through chat.main.build; then the real YouTube and Twitch connectors against the
mock platforms in eval/mock_platforms. All CPU, no network, no keys (no GPU step, so no gpu lock).

Steps:
  generate      the set when DATA/manifest.json is missing (seeded; pure Python).
  data check    sizes per label, unique texts and ids, SPECIMEN lint.
  classify      NO LLM (offline: borderline messages are held for a moderator, never allowed). Pass: abuse recall
                >= 85 % and precision >= 90 %, scam link recall >= 95 %, fake UPI recall >= 98 %, doxxing recall
                >= 85 %, clean false positives <= 3 %, classifier p95 <= 300 ms, broadcaster and moderator messages
                never hidden, flagged text never on the streamer overlay, no log record with flagged text or the
                streamer's private data.
  classify llm  informational: the same with a stand-in Groq (truth labels, 5 % errors). Never gating.
  youtube       real poller, governor, pipeline and actions against the YouTube mock: quota arithmetic (3 h plan =>
                6.75 s +- 0.1 between lists, governor units = server units), pagination, delete and ban round trips,
                503 retry, token refresh, quotaExceeded pause until the Pacific-midnight reset, missing permission.
  twitch        device-code login, EventSub connect, abrupt drop and reconnect with no duplicates and no HOLD,
                session_reconnect handover, Helix delete and ban round trips, !hold only for the broadcaster and
                moderators (redelivery ignored), scope lost after validation => hold for a moderator, never allow.

Options:
  --data DIR         set directory (default eval/data/generated/chat)
  --out DIR          run directory for the leg JSONs (default eval/runs/phase4-<timestamp>)
  --report-dir DIR   where the report goes (default eval/reports)
  --reference        classify legs use a stand-in classifier that is right by construction (validates the harness:
                     must pass); report <date>-phase4-reference.md
  --defect NAME      with --reference, seed a defect (no_scam | slow_classifier | leak_logs): must exit 1
  --no-llm-leg       skip the informational stand-in-Groq leg
  --heldout          also score the blind held-out chat set v2 (eval/data/private/chat_heldout_v2, gitignored; never
                     read by chat/ code or tests; the report shows aggregates only). Informational rows next to the
                     gated ones: never fails the gate, and a missing set is reported as not run (exit 2 accepted).
  --plan             print the steps that would run and exit 0 without running anything
  -h, --help         this text

Exit: 0 pass, 1 a criterion failed, 2 environment or setup error (set missing and unbuildable, bad arguments,
chat modules not importable; nothing trustworthy was measured), 3 a required hardware leg was skipped (none here).
EOF2
}

die_usage() { echo "gate_phase4.sh: $1" >&2; echo "try: scripts/gate_phase4.sh --help" >&2; exit 2; }

DATA="${SS_CHAT_DATA:-eval/data/generated/chat}"; OUT=""; REPORT_DIR=""; REF=0; DEFECT=""; LLM_LEG=1; PLAN=0; HELDOUT=0
while [ $# -gt 0 ]; do
  case "$1" in
    -h | --help) usage; exit 0 ;;
    --plan) PLAN=1 ;;
    --reference) REF=1 ;;
    --no-llm-leg) LLM_LEG=0 ;;
    --heldout) HELDOUT=1 ;;
    --data) [ $# -ge 2 ] || die_usage "--data needs a directory"; DATA="$2"; shift ;;
    --data=*) DATA="${1#*=}" ;;
    --out) [ $# -ge 2 ] || die_usage "--out needs a directory"; OUT="$2"; shift ;;
    --out=*) OUT="${1#*=}" ;;
    --report-dir) [ $# -ge 2 ] || die_usage "--report-dir needs a directory"; REPORT_DIR="$2"; shift ;;
    --defect) [ $# -ge 2 ] || die_usage "--defect needs a name"; DEFECT="$2"; shift ;;
    --defect=*) DEFECT="${1#*=}" ;;
    *) die_usage "unknown argument: $1" ;;
  esac
  shift
done
case "$DEFECT" in '' | no_scam | slow_classifier | leak_logs) ;; *) die_usage "--defect must be no_scam, slow_classifier or leak_logs, got '$DEFECT'" ;; esac
[ -z "$DEFECT" ] || [ "$REF" = 1 ] || die_usage "--defect needs --reference"

[ -n "$OUT" ] || OUT="eval/runs/phase4-$(date +%Y%m%d-%H%M%S)"
PHASE_NAME=phase4
[ "$REF" = 0 ] || PHASE_NAME="${PHASE_NAME}-reference"
[ -z "$DEFECT" ] || PHASE_NAME="${PHASE_NAME}-defect-${DEFECT}"
PY=(python -m eval.phase4 --data "$DATA" --out "$OUT")
CLS=()
[ "$REF" = 0 ] || CLS+=(--reference)
[ -z "$DEFECT" ] || CLS+=(--defect "$DEFECT")

step() {
  if [ "$PLAN" = 1 ]; then
    [ "${1:-}" != "--ok-rc" ] || shift 2
    local name="$1"; shift
    echo "PLAN: [$name] $*"
  else
    gate_step "$@"
  fi
}

GATE_REPORT_HOOK=phase4_report
phase4_report() {
  local extra=()
  [ -z "$REPORT_DIR" ] || extra+=(--report-dir "$REPORT_DIR")
  python -m eval.phase4 --leg report --out "$OUT" --results "$1" --phase-name "$PHASE_NAME" "${extra[@]}"
}

if [ "$PLAN" = 0 ]; then
  command -v python >/dev/null 2>&1 || { echo "python not on PATH: run through scripts/ss.sh" >&2; exit 2; }
  mkdir -p "$OUT"
  gate_init "$PHASE_NAME"
fi

# 1. the set
if [ "$PLAN" = 1 ]; then
  echo "PLAN: [generate chat set] (only when $DATA/manifest.json is missing) python -m eval.data_gen.chat --out $DATA"
elif [ ! -f "$DATA/manifest.json" ]; then
  step "generate chat set" python -m eval.data_gen.chat --out "$DATA"
else
  gate_record "generate chat set" 0 "set already present"
fi
step "data check" "${PY[@]}" --leg data
if [ "$PLAN" = 0 ] && [ "${GATE_MAPPED[${#GATE_MAPPED[@]}-1]}" != 0 ]; then
  echo "chat set missing or invalid: no leg is run (nothing was measured)"
  gate_finish
fi

# 2. legs
step "classify (no LLM)" "${PY[@]}" --leg classify "${CLS[@]}"
if [ "$LLM_LEG" = 1 ]; then
  step "classify (stand-in Groq, informational)" "${PY[@]}" --leg classify_llm "${CLS[@]}"
fi
if [ "$HELDOUT" = 1 ]; then
  # informational: exit 0 once scored; 2 (set missing) is accepted so it can never fail the gate
  step --ok-rc 2 "classify (held-out v2, informational)" "${PY[@]}" --heldout "${CLS[@]}"
fi
step "YouTube mock" "${PY[@]}" --leg youtube
step "Twitch mock" "${PY[@]}" --leg twitch

if [ "$PLAN" = 1 ]; then
  echo "PLAN: report -> eval/reports/<date>-${PHASE_NAME}.md (data=$DATA, out=$OUT)"
  exit 0
fi
gate_finish
