#!/usr/bin/env bash
# REAL-INPUT mode (no scripted reel, no fake chat): starts the API (spawns the real engine), the web app and a keyless
# rehearsal session, then prints where OBS or your phone should publish. Run through ss.sh:
#   MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh bash scripts/real.sh --gpu
# Options: --gpu (recommended: vision on the NVIDIA GPU), --no-build, --raw (show the RAW pane on /demo, loopback only),
#          --chat (run the real chat process: needs YouTube/Twitch credentials in .env)
#          --live (GO LIVE for real to every platform that has a stream key (.env YOUTUBE_STREAM_KEY / TWITCH_STREAM_KEY);
#                  without it the session is a local-only rehearsal: nothing leaves your machine)
#          --engine-log (keep the engine's own log in /tmp/ss_engine.log: code-only messages)
# Ctrl-C stops everything. Ports: 8000 (API), 3000 (web), 8890 SRT / 1935 RTMP / 9997 (engine).
# Windows: the phone/OBS must reach WSL: see docs/PLAN.md section 9 (mirrored networking + firewall rules). OBS on the
# same PC can use the printed 127.0.0.1 URL.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
GPU=0 BUILD=1 RAW=0 CHAT=0
ORIG_ARGS=("$@")
for a in "$@"; do
  case "$a" in
    --gpu) GPU=1 ;;
    --no-build) BUILD=0 ;;
    --raw) RAW=1 ;;
    --chat) CHAT=1 ;;
    --live) export REAL_LIVE=1 ;;
    --engine-log) export SESSION_ENGINE_CMD="bash -c 'exec python -u -m engine.main --profile {profile} > /tmp/ss_engine.log 2>&1'" ;;
    -h | --help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
if [ "$GPU" = 1 ]; then
  if [ -z "${SS_REAL_GPU_LOCKED:-}" ]; then
    exec flock -E 2 -w 600 /tmp/streamsarthi-gpu.lock env SS_REAL_GPU_LOCKED=1 bash "$0" "${ORIG_ARGS[@]}"
  fi
  export GPU__ENABLED=true GPU__ORT_PROVIDER=cuda
fi
for p in 8000 3000 8890 1935 9997; do
  if ss -ltn 2>/dev/null | grep -qE "[:.]$p\b"; then echo "port $p is in use: stop the other stack first." >&2; exit 2; fi
done
PIDS=()
cleanup() {
  echo; echo "stopping..."
  for pid in "${PIDS[@]:-}"; do [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null; done
  sleep 3
  for pid in "${PIDS[@]:-}"; do [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null; done
  pkill -f "[e]ngine.main" 2>/dev/null; pkill -f "[m]ediamtx" 2>/dev/null
  exit 0
}
trap cleanup INT TERM
if [ "$BUILD" = 1 ]; then bash scripts/web.sh run build >/tmp/ss_webbuild.log 2>&1 || { echo "web build failed: /tmp/ss_webbuild.log"; exit 1; }; fi
export PYTHONUNBUFFERED=1
SECRET_KEY="$(head -c 24 /dev/urandom | base64 | tr -d '=/+\n')"; export SECRET_KEY
[ "$RAW" = 1 ] && export DEMO_RAW_PREVIEW=1
if [ "$CHAT" = 1 ]; then export CHAT__ENABLED=true; fi
python -u -m app.api.main >/tmp/ss_api.log 2>&1 &
PIDS+=("$!")
for _ in $(seq 1 40); do grep -q "#token=" /tmp/ss_api.log 2>/dev/null && break; sleep 0.5; done
grep -q "#token=" /tmp/ss_api.log || { echo "API did not start: /tmp/ss_api.log"; cleanup; }
bash scripts/web.sh run start -- -p 3000 -H 127.0.0.1 >/tmp/ss_web.log 2>&1 &
PIDS+=("$!")
for _ in $(seq 1 40); do grep -q "Ready" /tmp/ss_web.log 2>/dev/null && break; sleep 0.5; done
echo "Open this link (local only, it carries your login):"
grep -o 'http://127.0.0.1:3000/#token=[A-Za-z0-9_.-]*' /tmp/ss_api.log | head -1
echo
python -u scripts/real_run.py &
PIDS+=("$!")
wait "${PIDS[-1]}"
cleanup
