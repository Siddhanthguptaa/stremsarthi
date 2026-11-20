#!/usr/bin/env bash
# Shared helpers for scripts/gate_phase*.sh (docs/specs/eval.md 5.9). Source this file, do not run it.
#
# Exit codes of a gate: 0 pass, 1 a criterion failed, 2 environment/setup error (nothing measured),
# 3 a required hardware leg was skipped.
#
#   gate_init phase0
#   gate_step [--ok-rc N] <name> <command...>    # run a step, record its exit code, never abort
#   gate_finish                                  # print the table, write the report, exit with the code
#
# gate_map_rc maps one step's raw exit code to a gate code: 0->0, 3->3, 2/126/127->2, anything else->1.
# gate_combine takes mapped codes and returns the worst by precedence 2 > 1 > 3 > 0.

GATE_PHASE=""
GATE_NAMES=()
GATE_CMDS=()
GATE_RAW=()
GATE_MAPPED=()
GATE_SECS=()
GATE_NOTES=()
GATE_ATTACH_FILES=()

gate_map_rc() {
  case "$1" in
    0) echo 0 ;;
    3) echo 3 ;;
    2 | 126 | 127) echo 2 ;;
    *) echo 1 ;;
  esac
}

gate_combine() {
  local has1=0 has2=0 has3=0 rc
  for rc in "$@"; do
    case "$rc" in
      2) has2=1 ;;
      1) has1=1 ;;
      3) has3=1 ;;
    esac
  done
  if [ "$has2" = 1 ]; then echo 2; elif [ "$has1" = 1 ]; then echo 1; elif [ "$has3" = 1 ]; then echo 3; else echo 0; fi
}

gate_init() {
  GATE_PHASE="$1"
  GATE_NAMES=(); GATE_CMDS=(); GATE_RAW=(); GATE_MAPPED=(); GATE_SECS=(); GATE_NOTES=(); GATE_ATTACH_FILES=()
}

gate_step() {
  local ok_rc="" note=""
  if [ "${1:-}" = "--ok-rc" ]; then ok_rc="$2"; shift 2; fi
  local name="$1"; shift
  echo ">> [$name] $*"
  local t0 t1 rc
  t0=$(date +%s.%N)
  set +e
  "$@"
  rc=$?
  set -e
  t1=$(date +%s.%N)
  local mapped
  if [ -n "$ok_rc" ] && [ "$rc" = "$ok_rc" ]; then mapped=0; note="exit $rc accepted (warnings only)"; else mapped=$(gate_map_rc "$rc"); fi
  GATE_NAMES+=("$name"); GATE_CMDS+=("$*"); GATE_RAW+=("$rc"); GATE_MAPPED+=("$mapped")
  GATE_SECS+=("$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.1f", b-a}')"); GATE_NOTES+=("$note")
}

# Record a step that was decided without running a command (for example a missing prerequisite file).
gate_record() {
  local name="$1" rc="$2" note="${3:-}"
  GATE_NAMES+=("$name"); GATE_CMDS+=("(check)"); GATE_RAW+=("$rc"); GATE_MAPPED+=("$(gate_map_rc "$rc")")
  GATE_SECS+=("0.0"); GATE_NOTES+=("$note")
}

gate_json_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

gate_write_results() {
  local out="$1" i sep=""
  {
    printf '{"phase":"%s","steps":[' "$GATE_PHASE"
    for i in "${!GATE_NAMES[@]}"; do
      printf '%s{"name":"%s","command":"%s","rc":%s,"mapped_rc":%s,"seconds":%s,"note":"%s"}' "$sep" \
        "$(gate_json_escape "${GATE_NAMES[$i]}")" "$(gate_json_escape "${GATE_CMDS[$i]}")" \
        "${GATE_RAW[$i]}" "${GATE_MAPPED[$i]}" "${GATE_SECS[$i]}" "$(gate_json_escape "${GATE_NOTES[$i]}")"
      sep=","
    done
    printf ']}\n'
  } >"$out"
}

gate_finish() {
  local i overall label
  echo
  printf '%-34s %-6s %-8s %s\n' "STEP" "EXIT" "RESULT" "SECONDS"
  printf '%-34s %-6s %-8s %s\n' "----" "----" "------" "-------"
  for i in "${!GATE_NAMES[@]}"; do
    case "${GATE_MAPPED[$i]}" in 0) label=PASS ;; 2) label=ENV ;; 3) label=SKIPPED ;; *) label=FAIL ;; esac
    printf '%-34s %-6s %-8s %s\n' "${GATE_NAMES[$i]}" "${GATE_RAW[$i]}" "$label" "${GATE_SECS[$i]}"
  done
  overall=$(gate_combine "${GATE_MAPPED[@]}")
  case "$overall" in
    0) label="PASS" ;;
    2) label="ENVIRONMENT ERROR (nothing trustworthy was measured)" ;;
    3) label="REQUIRED HARDWARE LEG SKIPPED" ;;
    *) label="FAIL" ;;
  esac
  echo
  echo "GATE ${GATE_PHASE}: ${label} (exit ${overall})"
  if [ -z "${GATE_NO_REPORT:-}" ]; then
    local results="${GATE_RESULTS_FILE:-.tmp/gate/${GATE_PHASE}-results.json}"
    mkdir -p "$(dirname "$results")"
    gate_write_results "$results"
    if [ -n "${GATE_REPORT_HOOK:-}" ]; then
      # a gate with its own report (phase 1 builds one from its leg files) names a function taking the results file
      "$GATE_REPORT_HOOK" "$results" || true
    else
      local attach=() f
      for f in "${GATE_ATTACH_FILES[@]:-}"; do [ -n "$f" ] && attach+=(--attach "$f"); done
      python -m eval.run gate-report --phase "$GATE_PHASE" --results "$results" "${attach[@]}" || true
    fi
  fi
  exit "$overall"
}
