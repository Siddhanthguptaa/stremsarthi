#!/usr/bin/env bash
# One-command local demo (SPECIMEN data only, no keys needed). Run through ss.sh from Git Bash / PowerShell:
#   MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/suresh/OneDrive/Desktop/stremsarthi/scripts/ss.sh bash scripts/demo.sh
# Starts: API (spawns the real engine), web app, scripted demo chat, then loops the demo reel into a rehearsal session.
# It prints the local login URL. Ctrl-C stops everything. Ports used: 8000 (API), 3000 (web), 8890/1935/9997 (engine).
# Options: --no-build (skip the web build check), --no-chat, --raw (enable the RAW preview pane: loopback only),
#          --no-audio (skip the speech/music audio build)
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$PWD"
BUILD=1 CHAT=1 RAW=0 AUDIO=1 GPU=0 ENGINE_LOG=0
ORIG_ARGS=("$@")
for a in "$@"; do
  case "$a" in
    --gpu) GPU=1 ;;
    --engine-log) ENGINE_LOG=1 ;;
    --no-build) BUILD=0 ;;
    --no-chat) CHAT=0 ;;
    --raw) RAW=1 ;;
    --no-audio) AUDIO=0 ;;
    -h | --help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

# --gpu: run the vision models on the NVIDIA GPU (much faster OCR on text-heavy scenes; CUDA start-up ~50 s).
# One GPU job at a time: the whole demo holds /tmp/streamsarthi-gpu.lock.
if [ "$GPU" = 1 ]; then
  if [ -z "${SS_DEMO_GPU_LOCKED:-}" ]; then
    exec flock -E 2 -w 600 /tmp/streamsarthi-gpu.lock env SS_DEMO_GPU_LOCKED=1 bash "$0" "${ORIG_ARGS[@]}"
  fi
  export GPU__ENABLED=true GPU__ORT_PROVIDER=cuda DEMO_WARMUP_S=45
fi

for p in 8000 3000 8890 1935 9997; do
  if ss -ltn 2>/dev/null | grep -qE "[:.]$p\b"; then
    echo "port $p is already in use: stop the other stack first (a running demo, gate or smoke)." >&2
    exit 2
  fi
done

PIDS=()
cleanup() {
  echo; echo "stopping the demo..."
  for pid in "${PIDS[@]:-}"; do [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null; done
  sleep 3
  for pid in "${PIDS[@]:-}"; do [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null; done
  pkill -f "[e]ngine.main" 2>/dev/null; pkill -f "[m]ediamtx" 2>/dev/null; pkill -f "[e]val.sender.sender" 2>/dev/null
  exit 0
}
trap cleanup INT TERM

echo "== 1/5 demo assets (speech audio, face portraits, chat plan)"
if [ "$AUDIO" = 1 ]; then bash scripts/demo_assets.sh >/tmp/ss_demo_assets.log 2>&1 || { echo "demo_assets failed: see /tmp/ss_demo_assets.log"; exit 1; }
else bash scripts/demo_assets.sh --no-audio >/tmp/ss_demo_assets.log 2>&1 || { echo "demo_assets failed: see /tmp/ss_demo_assets.log"; exit 1; }; fi

echo "== 2/5 web app build"
if [ "$BUILD" = 1 ]; then
  bash scripts/web.sh run build >/tmp/ss_webbuild.log 2>&1 || { echo "web build failed: see /tmp/ss_webbuild.log"; exit 1; }
fi

echo "== 3/5 API (spawns the real engine when the session starts)"
export PYTHONUNBUFFERED=1
SECRET_KEY="$(head -c 24 /dev/urandom | base64 | tr -d '=/+\n')"
export SECRET_KEY
[ "$RAW" = 1 ] && export DEMO_RAW_PREVIEW=1
# --engine-log: keep the engine's own log (code-only messages, no frames/text/secrets) in /tmp/ss_engine.log: it holds the
# per-lane coverage diagnostics ('coverage summary ... lane_diag') printed when the engine stops.
if [ "$ENGINE_LOG" = 1 ]; then
  export SESSION_ENGINE_CMD="bash -c 'exec python -u -m engine.main --profile {profile} > /tmp/ss_engine.log 2>&1'"
fi
if [ "$CHAT" = 1 ]; then
  export CHAT__ENABLED=true
  export SESSION_CHAT_CMD="python -m eval.demo.chat_feeder --profile {profile} --loop"
fi
python -u -m app.api.main >/tmp/ss_api.log 2>&1 &
PIDS+=("$!")
for _ in $(seq 1 40); do grep -q "#token=" /tmp/ss_api.log 2>/dev/null && break; sleep 0.5; done
grep -q "#token=" /tmp/ss_api.log || { echo "API did not start: see /tmp/ss_api.log"; cleanup; }

echo "== 4/5 web server"
bash scripts/web.sh run start -- -p 3000 -H 127.0.0.1 >/tmp/ss_web.log 2>&1 &
PIDS+=("$!")
for _ in $(seq 1 40); do grep -q "Ready" /tmp/ss_web.log 2>/dev/null && break; sleep 0.5; done

echo "== 5/5 rehearsal session + demo reel"
echo
echo "Open this link in your browser (local only, it carries your login):"
grep -o 'http://127.0.0.1:3000/#token=[A-Za-z0-9_.-]*' /tmp/ss_api.log | head -1
echo "Then go to the Demo page in the top menu (or http://127.0.0.1:3000/demo)."
echo
python -u scripts/demo_run.py &
PIDS+=("$!")
wait "${PIDS[-1]}"
cleanup
