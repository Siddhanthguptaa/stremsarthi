#!/usr/bin/env bash
# Gate 2 acceptance (T2.10, docs/specs/eval.md section 9): the gate must FAIL on a seeded defect.
#
#   bash scripts/ss.sh bash scripts/gate_phase2_acceptance.sh [--data DIR] [--max-per-bucket N] [--out DIR]
#
# Runs the offline accuracy leg on a small subset of stills (aadhaar, pan, plate, text_phone) three times with one
# deliberately broken detector build each (blind cards, crashing text, absent plates) and once clean, then checks with
# eval/phase2/acceptance.py that the defect flipped exactly the rows and checks it should. Exit 0 = the gate behaved,
# 1 = a defect was NOT caught (the gate is broken), 2 = environment error (dataset or detectors missing).
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

DATA=/tmp/ss_phase2_video; PER=8; OUT="eval/runs/phase2-acceptance-$(date +%Y%m%d-%H%M%S)"
while [ $# -gt 0 ]; do
  case "$1" in
    --data) DATA="$2"; shift ;;
    --max-per-bucket) PER="$2"; shift ;;
    --out) OUT="$2"; shift ;;
    -h | --help) sed -n 2,10p "$0"; exit 0 ;;
    *) echo "gate_phase2_acceptance.sh: unknown argument $1" >&2; exit 2 ;;
  esac
  shift
done
command -v python >/dev/null 2>&1 || { echo "python not on PATH: run through scripts/ss.sh" >&2; exit 2; }
mkdir -p "$OUT"
BUCKETS=(aadhaar pan plate text_phone)
LEG=(python -m eval.run phase2 --leg offline --data "$DATA" --no-clips --buckets "${BUCKETS[@]}" --max-per-bucket "$PER"
     --latency-per-bucket 0 --min-n 5)

echo "== clean build"
"${LEG[@]}" --out "$OUT/clean" >"$OUT/clean.log" 2>&1
rc=$?
if [ ! -f "$OUT/clean/offline.json" ] || [ "$rc" = 2 ]; then
  tail -5 "$OUT/clean.log"; echo "environment error: the clean run measured nothing"; exit 2
fi
echo "   clean build leg exit $rc (a subset leg never passes every row: unscored rows are NOT RUN)"

fail=0
for spec in blind:cards crash:text absent:plates; do
  tag="${spec/:/-}"
  echo "== seeded defect $spec"
  "${LEG[@]}" --out "$OUT/$tag" --seeded-defect "$spec" >"$OUT/$tag.log" 2>&1
  drc=$?
  if [ "$drc" != 1 ]; then echo "   PROBLEM: the defective build exited $drc, the gate must exit 1"; fail=1; fi
  python -m eval.phase2.acceptance "$OUT/clean/offline.json" "$OUT/$tag/offline.json" "$spec" || fail=1
done
if [ "$fail" = 0 ]; then echo "ACCEPTANCE PASS: all seeded defects were caught"; else echo "ACCEPTANCE FAIL"; fi
exit "$fail"
