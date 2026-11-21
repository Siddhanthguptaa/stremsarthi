#!/usr/bin/env bash
# Video red-team gate (T7.1, docs/specs/eval.md 5.8 and 5.9): publishes the per-attack leak rates and fails only on a
# regression, never on an absolute rate (the plan's red-team criterion is "recorded and published").
#
#   bash scripts/ss.sh bash scripts/gate_redteam_video.sh [--quick] [--full] [--attacks A ...] [--groups G ...]
#                                                       [--write-baseline] [--out DIR] [--report-dir DIR]
#
# Steps:
#   1. self-check (no models): a blind detector, a displaced mask, a half-covering mask and a perfect detector are
#      pushed through the scoring. The gate exits 1 when a seeded defect is NOT flagged (the harness is broken).
#   2. the red-team run: SPECIMEN adversarial clips through the real detectors and the production mask painter, leak
#      rate per attack (coverage proxy + output read-back), ranked misses, repro frames under --out (default
#      /tmp/ss_redteam_video). Writes eval/reports/<date>-redteam-video.{md,json}.
#   3. regression vs eval/baselines/redteam_video.json when it exists: any group or attack whose leak rate rose by more
#      than 5 points fails (exit 1). --write-baseline stores this run as the baseline.
# Default is --quick (a subset: one or two mid-severity parameters per attack). --full runs every parameter.
#
# Exit codes: 0 pass, 1 a regression or a seeded defect was not flagged, 2 environment error (nothing measured),
# 3 reserved (a required hardware leg was skipped; this gate needs no GPU).
# Every GPU command would be wrapped in `flock /tmp/streamsarthi-gpu.lock`; the red team runs the detectors on the CPU.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

MODE=--quick
PASS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --quick) MODE=--quick ;;
    --full) MODE="" ;;
    -h | --help) sed -n 2,22p "$0"; exit 0 ;;
    *) PASS+=("$1") ;;
  esac
  shift
done
command -v python >/dev/null 2>&1 || { echo "python not on PATH: run through scripts/ss.sh" >&2; exit 2; }

echo "== 1/2 self-check: seeded broken detectors must be flagged"
python -m eval.run redteam-video --selftest
rc=$?
if [ "$rc" != 0 ]; then
  echo "GATE FAIL: the scoring did not flag a seeded defect (exit $rc)"
  [ "$rc" = 2 ] && exit 2
  exit 1
fi

echo "== 2/2 red-team run ${MODE:-(full)}"
# shellcheck disable=SC2086
python -m eval.run redteam-video $MODE "${PASS[@]}"
rc=$?
case "$rc" in
  0) echo "GATE PASS: leak rates published, no regression" ;;
  1) echo "GATE FAIL: leak-rate regression vs eval/baselines/redteam_video.json" ;;
  2) echo "GATE ERROR: environment problem, nothing was measured" ;;
  *) echo "GATE FAIL: unexpected exit code $rc"; rc=1 ;;
esac
exit "$rc"
