#!/usr/bin/env bash
# Gate 6: streamer app, moderator console and safety report in a real browser (docs/specs/eval.md 5.9, T6.5).
#
# Run inside WSL (scripts/ss.sh sets the venv, node and PATH):
#   bash scripts/ss.sh bash scripts/gate_phase6.sh [options]
# Exits non-zero when the gate fails. A step that cannot run (a missing browser, a port in use, a missing script)
# is exit 2 (NOT RUN), never a pass.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/gate_lib.sh
source scripts/gate_lib.sh

usage() {
  cat <<'EOF2'
Usage: scripts/gate_phase6.sh [--api-port N] [--web-port N] [--build DIR] [--skip-static] [--plan] [--help]

Steps:
  web build        npm run build in a WSL-native build dir with NEXT_PUBLIC_API_URL pointing at the test API
  web lint         npm run lint
  web typecheck    tsc --noEmit
  web unit tests   vitest
  api tests        pytest -q app/api
  report schema    the safety-report privacy and schema tests (app/api/tests/test_api_report_risk.py)
  e2e typecheck    tsc over eval/e2e_web
  e2e browser      Playwright (Chromium headless shell) against the real API (eval/e2e_web/serve_api.py: in-process bus,
                   SESSION_SPAWN=false, fake enroller, no engine) and `next start`. Both servers are started here on their
                   own ports and stopped on exit. Includes an axe scan of every route at 1280 and 360 px.
  walkthrough      eval/e2e/walkthrough.py (fresh clone to live timing). Not written yet: NOT RUN (exit 2).

The build dir is NEVER ~/webbuild (a running demo stack may serve from it): default ~/webbuild-e2e. Ports 3000, 8000,
8890, 1935 and 9997 are refused. Screenshots go to /tmp/ss-e2e-shots (not committed).
Exit: 0 pass, 1 a criterion failed, 2 environment problem or a step that did not run, 3 a hardware leg skipped (none).
EOF2
}

die_usage() { echo "gate_phase6.sh: $1" >&2; exit 2; }

API_PORT=8010; WEB_PORT=3010; BUILD="${SS_WEBBUILD_E2E:-$HOME/webbuild-e2e}"; PLAN=0; SKIP_STATIC=0
while [ $# -gt 0 ]; do
  case "$1" in
    -h | --help) usage; exit 0 ;;
    --plan) PLAN=1 ;;
    --skip-static) SKIP_STATIC=1 ;;
    --api-port) [ $# -ge 2 ] || die_usage "--api-port needs a number"; API_PORT="$2"; shift ;;
    --web-port) [ $# -ge 2 ] || die_usage "--web-port needs a number"; WEB_PORT="$2"; shift ;;
    --build) [ $# -ge 2 ] || die_usage "--build needs a directory"; BUILD="$2"; shift ;;
    *) die_usage "unknown argument: $1" ;;
  esac
  shift
done
for p in "$API_PORT" "$WEB_PORT"; do
  case "$p" in 3000 | 8000 | 8890 | 1935 | 9997) die_usage "port $p belongs to the demo stack" ;; esac
done
[ "$BUILD" != "$HOME/webbuild" ] || die_usage "refusing to build in ~/webbuild (the demo web server may be running from it)"

STATE=/tmp/ss-e2e-state.json; OUT=/tmp/ss-e2e-results; REPORT=/tmp/ss-e2e-report.json; AXE=/tmp/ss-e2e-axe.jsonl
SUMMARY=/tmp/ss-e2e-summary.md; SHOTS=/tmp/ss-e2e-shots
API_PID=""; WEB_PID=""

step() {
  if [ "$PLAN" = 1 ]; then
    [ "${1:-}" != "--ok-rc" ] || shift 2
    local name="$1"; shift
    echo "PLAN: [$name] $*"
  else
    gate_step "$@"
  fi
}

web() { SS_WEBBUILD="$BUILD" NEXT_PUBLIC_API_URL="http://127.0.0.1:$API_PORT" bash scripts/web.sh "$@"; }

cleanup() {
  [ -z "$WEB_PID" ] || kill "$WEB_PID" 2>/dev/null || true
  [ -z "$API_PID" ] || kill "$API_PID" 2>/dev/null || true
  [ -z "$WEB_PID$API_PID" ] || wait 2>/dev/null || true
}
trap cleanup EXIT

# e2e_browser: start both servers, run Playwright, summarise. Exit 2 when the setup is broken, else the tests' result.
e2e_browser() {
  local rc=0
  [ -d "$BUILD/node_modules/@playwright/test" ] || { echo "Playwright is not installed in $BUILD (npm install -D @playwright/test @axe-core/playwright)"; return 2; }
  [ -d "$BUILD/.next" ] || { echo "no web build in $BUILD"; return 2; }
  for p in "$API_PORT" "$WEB_PORT"; do
    if (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then echo "port $p is already in use"; return 2; fi
  done
  rm -rf "$STATE" "$OUT" "$REPORT" "$AXE" "$SHOTS"; mkdir -p "$SHOTS"
  python -m eval.e2e_web.serve_api --port "$API_PORT" --web-port "$WEB_PORT" --state-file "$STATE" >/tmp/ss-e2e-api.log 2>&1 &
  API_PID=$!
  (cd "$BUILD" && NEXT_TELEMETRY_DISABLED=1 exec npx next start -p "$WEB_PORT" -H 127.0.0.1) >/tmp/ss-e2e-web.log 2>&1 &
  WEB_PID=$!
  local i
  for i in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$API_PORT/api/health" >/dev/null 2>&1 && curl -fsS "http://127.0.0.1:$WEB_PORT/" >/dev/null 2>&1 && [ -s "$STATE" ]; then break; fi
    kill -0 "$API_PID" 2>/dev/null || { echo "test API exited:"; tail -20 /tmp/ss-e2e-api.log; return 2; }
    sleep 1
  done
  curl -fsS "http://127.0.0.1:$API_PORT/api/health" >/dev/null 2>&1 || { echo "test API did not come up"; tail -20 /tmp/ss-e2e-api.log; return 2; }
  curl -fsS "http://127.0.0.1:$WEB_PORT/" >/dev/null 2>&1 || { echo "web server did not come up"; tail -20 /tmp/ss-e2e-web.log; return 2; }
  rsync -rlt --delete "eval/e2e_web/" "$BUILD/e2e/" --exclude=__pycache__ --exclude='*.py'
  (cd "$BUILD" && E2E_STATE="$STATE" E2E_OUT="$OUT" E2E_JSON="$REPORT" E2E_AXE="$AXE" npx playwright test -c e2e/playwright.config.ts) || rc=$?
  python -m eval.e2e_web.summarize --report "$REPORT" --axe "$AXE" --out "$SUMMARY" || rc=$(( rc == 0 ? $? : rc ))
  # Playwright exits 1 for test failures; anything else with no report is an environment problem.
  if [ ! -f "$REPORT" ]; then return 2; fi
  [ "$rc" = 0 ] && return 0
  return 1
}

# e2e_typecheck: the specs compile (run in the build dir where @playwright/test lives).
e2e_typecheck() {
  rsync -rlt --delete "eval/e2e_web/" "$BUILD/e2e/" --exclude=__pycache__ --exclude='*.py'
  (cd "$BUILD" && npx tsc --noEmit -p e2e/tsconfig.json)
}

walkthrough() {
  [ -f eval/e2e/walkthrough.py ] || { echo "eval/e2e/walkthrough.py does not exist yet: NOT RUN"; return 2; }
  python eval/e2e/walkthrough.py
}

if [ "$PLAN" = 0 ]; then
  command -v node >/dev/null 2>&1 || { echo "node not on PATH: run through scripts/ss.sh" >&2; exit 2; }
  gate_init phase6
fi

if [ "$SKIP_STATIC" = 0 ]; then
  if [ "$PLAN" = 1 ] || [ ! -d "$BUILD/node_modules" ]; then step "web install" web ci; fi
  step "web build" web run build
  step "web lint" web run lint
  step "web typecheck" web run typecheck
  step "web unit tests" web test
fi
step "api tests" python -m pytest -q -x app/api
step "report schema" python -m pytest -q app/api/tests/test_api_report_risk.py
step "e2e typecheck" e2e_typecheck
step "e2e browser" e2e_browser
step "walkthrough" walkthrough

if [ "$PLAN" = 1 ]; then
  echo "PLAN: summary -> $SUMMARY; screenshots -> $SHOTS; axe findings -> $AXE"
  exit 0
fi

gate_finish
