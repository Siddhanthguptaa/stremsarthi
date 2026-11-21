#!/usr/bin/env bash
# Final gate (T7.6, docs/PLAN.md section 5 phase 7, docs/specs/eval.md 5.9): runs every gate in order, then builds
# eval/reports/<date>-final.md and .json from the reports they wrote.
#
# Run inside WSL (scripts/ss.sh sets the venv, the memory cap and PYTHONPATH):
#   bash scripts/ss.sh bash scripts/gate_final.sh [--quick] [--skip LIST] [--report-dir DIR] [--plan] [--help]
# Heavy: the gates run one after another, never in parallel (one GPU job at a time; the GPU legs take the lock
# themselves: flock /tmp/streamsarthi-gpu.lock).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/gate_lib.sh
source scripts/gate_lib.sh

usage() {
  cat <<'EOF'
Usage: scripts/gate_final.sh [--quick] [--skip LIST] [--report-dir DIR] [--plan] [--help]

Runs, in this order (a gate whose script file does not exist is recorded as NOT RUN, never skipped silently and
never a pass; gate_phase6.sh is detected by file existence):
  G0              scripts/gate_phase0.sh
  G1              scripts/gate_phase1.sh                 (--quick: 45 s main leg)
  G2              scripts/gate_phase2.sh                 (offline accuracy leg + real-time perf legs; --quick: subset)
  G3              scripts/gate_phase3.sh                 (--quick: every 3rd clip)
  G4              scripts/gate_phase4.sh --heldout       (the held-out chat set is informational, never gating)
  G5              scripts/gate_phase5.sh
  G6              scripts/gate_phase6.sh                 (when the file exists)
  redteam-video   scripts/gate_redteam_video.sh          (--quick: subset)
  redteam-audiochat scripts/gate_redteam_audiochat.sh    (--quick: subset)
  e2e             scripts/e2e_smoke.sh                   (--quick: --duration 30)
then `python -m eval.run final-report`, which writes eval/reports/<date>-final.md and .json: every PLAN section 5
target with the measured value, level scope, verdict and caveats, the NOT RUN list, the (empty unless a live report
exists) "verified live" section and the cost pointer (docs/COST.md). Reports written before this run started are
STALE: a missing or stale report is never a pass.

Options:
  --quick             pass --quick to the gates that take it (G1, G2, G3, both red teams) and shorten the e2e smoke
  --skip LIST         comma list of g0,g1,g2,g3,g4,g5,g6,redteam (both),redteam-video,redteam-audiochat,e2e;
                      a skipped gate is recorded as NOT RUN (exit 3), never as a pass
  --report-dir DIR    where the gates (that take the option) and the final report write (default eval/reports)
  --plan              print what would run and exit 0 without running anything
  -h, --help          this text

Exit: 0 every gate and the final report passed, 1 a criterion failed, 2 environment or setup error, 3 something is
NOT RUN, skipped or STALE (a required leg or gate was not run: never a pass). Precedence 2 > 1 > 3 > 0.
EOF
}

die_usage() { echo "gate_final.sh: $1" >&2; echo "try: scripts/gate_final.sh --help" >&2; exit 2; }

QUICK=0; PLAN=0; SKIP=""; REPORT_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    -h | --help) usage; exit 0 ;;
    --quick) QUICK=1 ;;
    --plan) PLAN=1 ;;
    --skip) [ $# -ge 2 ] || die_usage "--skip needs a comma list"; SKIP="$2"; shift ;;
    --skip=*) SKIP="${1#*=}" ;;
    --report-dir) [ $# -ge 2 ] || die_usage "--report-dir needs a directory"; REPORT_DIR="$2"; shift ;;
    --report-dir=*) REPORT_DIR="${1#*=}" ;;
    *) die_usage "unknown argument: $1" ;;
  esac
  shift
done

SKIP_LC="$(printf '%s' "$SKIP" | tr 'A-Z' 'a-z' | tr -d ' ')"
IFS=',' read -r -a SKIP_ARR <<<"$SKIP_LC"
for s in "${SKIP_ARR[@]:-}"; do
  case "$s" in
    '' | g0 | g1 | g2 | g3 | g4 | g5 | g6 | redteam | redteam-video | redteam-audiochat | e2e) ;;
    *) die_usage "unknown --skip entry '$s' (use g0..g6, redteam, redteam-video, redteam-audiochat, e2e)" ;;
  esac
done

is_skipped() {
  local s
  for s in "${SKIP_ARR[@]:-}"; do
    [ -n "$s" ] || continue
    [ "$s" = "$1" ] && return 0
    [ "$s" = redteam ] && case "$1" in redteam-video | redteam-audiochat) return 0 ;; esac
  done
  return 1
}

RD=()
[ -z "$REPORT_DIR" ] || RD=(--report-dir "$REPORT_DIR")
Q=()
[ "$QUICK" = 0 ] || Q=(--quick)

# run_gate NAME SCRIPT ARGS... : one gate, or a NOT RUN record when skipped or missing
run_gate() {
  local name="$1" script="$2"
  shift 2
  if is_skipped "$name"; then
    if [ "$PLAN" = 1 ]; then echo "PLAN: [$name] NOT RUN (skipped by --skip)"; else
      echo ">> [$name] NOT RUN (skipped by --skip)"; gate_record "$name" 3 "NOT RUN: skipped by --skip"; fi
  elif [ ! -f "$script" ]; then
    if [ "$PLAN" = 1 ]; then echo "PLAN: [$name] NOT RUN ($script not found)"; else
      echo ">> [$name] NOT RUN ($script not found)"; gate_record "$name" 3 "NOT RUN: $script not found"; fi
  elif [ "$PLAN" = 1 ]; then
    echo "PLAN: [$name] bash $script $*"
  else
    gate_step "$name" bash "$script" "$@"
  fi
}

START="$(date +%s)"
RES="${GATE_RESULTS_FILE:-.tmp/gate/final-results.json}"
gate_init final

run_gate G0 scripts/gate_phase0.sh
run_gate G1 scripts/gate_phase1.sh "${Q[@]}" "${RD[@]}"
run_gate G2 scripts/gate_phase2.sh "${Q[@]}" "${RD[@]}"
run_gate G3 scripts/gate_phase3.sh "${Q[@]}" "${RD[@]}"
run_gate G4 scripts/gate_phase4.sh --heldout "${RD[@]}"
run_gate G5 scripts/gate_phase5.sh "${RD[@]}"
run_gate G6 scripts/gate_phase6.sh
run_gate redteam-video scripts/gate_redteam_video.sh "${Q[@]}" "${RD[@]}"
run_gate redteam-audiochat scripts/gate_redteam_audiochat.sh "${Q[@]}" "${RD[@]}"
E2E_ARGS=()
[ "$QUICK" = 0 ] || E2E_ARGS=(--duration 30)
run_gate e2e scripts/e2e_smoke.sh "${E2E_ARGS[@]}"

FINAL=(python -m eval.run final-report --since "$START" --results "$RES" --search-dir eval/reports)
[ -z "$REPORT_DIR" ] || FINAL+=(--report-dir "$REPORT_DIR")

if [ "$PLAN" = 1 ]; then
  echo "PLAN: [final-report] ${FINAL[*]}"
  echo "PLAN: report -> ${REPORT_DIR:-eval/reports}/<date>-final.md and .json (nothing was run)"
  exit 0
fi

# the table and the gate verdict (gate_finish exits, so run it in a subshell and keep its code)
mkdir -p "$(dirname "$RES")"
gate_write_results "$RES"
overall=0
(GATE_NO_REPORT=1 gate_finish) || overall=$?

frc=0
if command -v python >/dev/null 2>&1; then
  "${FINAL[@]}" || frc=$?
else
  echo "python not on PATH: run through scripts/ss.sh (final report not written)" >&2
  frc=2
fi
final="$(gate_combine "$overall" "$(gate_map_rc "$frc")")"
case "$final" in
  0) label="PASS" ;;
  2) label="ENVIRONMENT ERROR (nothing trustworthy was measured)" ;;
  3) label="INCOMPLETE: something is NOT RUN, skipped or STALE (never a pass)" ;;
  *) label="FAIL" ;;
esac
echo
echo "GATE FINAL: ${label} (exit ${final})"
exit "$final"
