#!/usr/bin/env bash
# REAL-INPUT mode (no scripted reel, no fake chat): starts the API (spawns the real engine), the web app and a keyless
# rehearsal session, then prints where OBS or your phone should publish. Run through ss.sh:
#   MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh bash scripts/real.sh --gpu
# Options: --gpu (recommended: vision on the NVIDIA GPU), --no-build, --raw (show the RAW pane on /demo, loopback only),
#          --chat (run the real chat process: needs YouTube/Twitch credentials in .env)
#          --live (GO LIVE for real to every platform that has a stream key (.env YOUTUBE_STREAM_KEY / TWITCH_STREAM_KEY);
#                  without it the session is a local-only rehearsal: nothing leaves your machine)
#          --engine-log (keep the engine's own log in /tmp/ss_engine.log: code-only messages)
#          --phone (let a phone on your LAN publish: exports INGEST_BIND_HOST=0.0.0.0 unless you already set it, and REQUIRES
#                   INGEST_SRT_PASSPHRASE (10-79 chars of A-Za-z0-9_-) in the environment or in .env; set PAIRING_HOST=<this PC's
#                   LAN IPv4> too when WSL uses NAT networking. RTMP sends the login in cleartext; SRT is encrypted: use SRT)
# Ctrl-C stops everything. Ports: 8000 (API), 3000 (web), 8890 SRT / 1935 RTMP / 9997 (engine).
# Windows: the phone/OBS must reach WSL: see docs/PLAN.md section 9 (mirrored networking + firewall rules). OBS on the
# same PC can use the printed 127.0.0.1 URL.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
GPU=0 BUILD=1 RAW=0 CHAT=0 PHONE=0
ORIG_ARGS=("$@")
for a in "$@"; do
  case "$a" in
    --gpu) GPU=1 ;;
    --no-build) BUILD=0 ;;
    --raw) RAW=1 ;;
    --chat) CHAT=1 ;;
    --phone) PHONE=1 ;;
    --live) export REAL_LIVE=1 ;;
    --engine-log) export SESSION_ENGINE_CMD="bash -c 'exec python -u -m engine.main --profile {profile} > /tmp/ss_engine.log 2>&1'" ;;
    -h | --help) sed -n '2,/^set -/{/^set -/!p}' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
# dotenv_get NAME: value of NAME from .env (same rules as relay/netenv.py: export prefix and quotes stripped); never echoed by callers.
dotenv_get() {
  local line v
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?$1[[:space:]]*=" .env 2>/dev/null | tail -1)" || true
  v="${line#*=}"; v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"
  local dq='"' sq="'"
  case "$v" in "$dq"*"$dq") v="${v#"$dq"}"; v="${v%"$dq"}" ;; "$sq"*"$sq") v="${v#"$sq"}"; v="${v%"$sq"}" ;; esac
  printf '%s' "$v"
}
if [ "$PHONE" = 1 ]; then
  PASS_VAL="${INGEST_SRT_PASSPHRASE:-}"
  [ -n "$PASS_VAL" ] || PASS_VAL="$(dotenv_get INGEST_SRT_PASSPHRASE)"
  if ! printf '%s' "$PASS_VAL" | grep -qE '^[A-Za-z0-9_-]{10,79}$'; then
    echo "--phone needs INGEST_SRT_PASSPHRASE: 10 to 79 characters of A-Za-z0-9_- (letters, digits, underscore, hyphen)." >&2
    echo "It is missing or in the wrong format (value not shown). Put your own long passphrase in .env as INGEST_SRT_PASSPHRASE=... or" >&2
    echo "set it in the environment, then run again. The ingest refuses to start on a phone-reachable address without it." >&2
    exit 2
  fi
  unset PASS_VAL
  if [ -z "${INGEST_BIND_HOST:-}" ]; then
    BIND_VAL="$(dotenv_get INGEST_BIND_HOST)"
    export INGEST_BIND_HOST="${BIND_VAL:-0.0.0.0}"
  fi
  echo "phone mode: ingest binds $INGEST_BIND_HOST (passphrase set). Use SRT in the phone app; RTMP sends the login unencrypted." >&2
  if [ -z "${PAIRING_HOST:-}" ] && [ -z "$(dotenv_get PAIRING_HOST)" ]; then
    echo "hint: if the printed phone URL shows a 172.x address (WSL NAT networking), set PAIRING_HOST=<this PC's LAN IPv4> in .env." >&2
  fi
fi
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
  # give the API up to 20 s to stop the engine and save the safety report (report.json) before any KILL
  for _ in $(seq 1 40); do
    alive=0
    for pid in "${PIDS[@]:-}"; do [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && alive=1; done
    [ "$alive" = 0 ] && break
    sleep 0.5
  done
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
