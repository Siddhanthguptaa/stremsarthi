#!/usr/bin/env bash
# Run pytest one directory per process (each under the ss.sh memory cap when called through it).
# Usage (through the wrapper): bash scripts/mem_check.sh contracts/tests engine vision ...
# Directories without tests are skipped. Prints one summary line per directory.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.."
rc_all=0
for d in "$@"; do
  [ -d "$d" ] || { echo "SKIP $d (missing)"; continue; }
  out=$(python -m pytest "$d" -q -x -p no:cacheprovider 2>&1)
  rc=$?
  echo "== $d (rc=$rc): $(echo "$out" | tail -n 1)"
  if [ "$rc" != 0 ] && [ "$rc" != 5 ]; then
    rc_all=1
    echo "$out" | tail -n 25
  fi
done
exit $rc_all
