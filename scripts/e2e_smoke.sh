#!/usr/bin/env bash
# End-to-end smoke of the local stack with no API keys (dev profile): the API spawns the real engine, the synthetic
# SPECIMEN sender publishes, an egress tap checks frames, the card mask and the hold slate, then stop + report.
#
# Run through the wrapper (it applies the memory cap and the venv):
#   MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh \
#       bash scripts/e2e_smoke.sh [--duration 45] [--delay 4.0] [--run-dir DIR]
# Exit codes: 0 all checks pass, 1 a check failed, 2 the environment could not run the chain.
# Heavy: one engine (vision + audio hosts), the API, MediaMTX, ffmpeg and a tap. Run one at a time.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
for tool in ffmpeg python; do
  command -v "$tool" >/dev/null 2>&1 || { echo "ENV: $tool not found (run through scripts/ss.sh)"; exit 2; }
done
ls .tmp/tools/mediamtx-v*/mediamtx >/dev/null 2>&1 || { echo "ENV: MediaMTX missing (bash scripts/fetch_tools.sh)"; exit 2; }
python -u scripts/e2e_smoke.py "$@"
rc=$?
# belt and braces: nothing from this run may survive (the driver already stops its own tree)
if pgrep -f "ss_e2e_.*(bus|engine)" >/dev/null 2>&1; then
  echo "note: leftover smoke processes found, killing them"
  pkill -TERM -f "ss_e2e_.*(bus|engine)" || true
  sleep 2
  pkill -KILL -f "ss_e2e_.*(bus|engine)" || true
fi
exit "$rc"
