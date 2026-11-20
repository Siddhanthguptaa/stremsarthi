#!/usr/bin/env bash
# Run the engine: ingest -> delay buffer -> mask -> egress, with the ZeroMQ bus on ipc:///tmp/streamsarthi-bus.
#
# Usage:  scripts/run_engine.sh [dev|demo] [extra engine.main options]
#   scripts/run_engine.sh                  # dev profile
#   scripts/run_engine.sh dev --skip audio # drop one module on purpose (logged as a warning)
#   scripts/run_engine.sh demo             # demo profile (GPU extras): runs under the GPU lock
#
# Inside WSL (or through scripts/ss.sh from Windows). Stops cleanly on Ctrl-C or SIGTERM.
#
# Egress (relay.runtime): YOUTUBE_STREAM_KEY and TWITCH_STREAM_KEY come from .env (never printed). A destination
# without a key is skipped with a warning code. Under the GPU lock STREAMSARTHI_GPU_LOCK_HELD=1 tells the
# NVENC probe not to take the same lock again (it would deadlock against this process).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="dev"
if [ "${1:-}" = "dev" ] || [ "${1:-}" = "demo" ]; then
  PROFILE="$1"
  shift
fi
RUN=(bash "$ROOT/scripts/ss.sh")
if [ "$PROFILE" = "demo" ]; then
  RUN+=(flock /tmp/streamsarthi-gpu.lock env STREAMSARTHI_GPU_LOCK_HELD=1)  # one GPU job at a time
fi
exec "${RUN[@]}" python -m engine.main --profile "$PROFILE" "$@"
