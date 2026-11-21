#!/usr/bin/env bash
# 4G impairment for the SRT ingest (spec test 10, T6.4): loss 2 %, delay 40 ms +/- 50 ms jitter
# each way (about 80 ms RTT), applied with `tc netem` to ONE UDP port on `lo`, so nothing else on
# the machine (other tests, other agents) is touched.
#
# Needs root network control (CAP_NET_ADMIN) and iproute2 with the netem kernel module. On WSL2 run
# it as root:   wsl.exe -d Ubuntu-24.04 -u root -- bash scripts/netem_4g.sh run
#
#   netem_4g.sh up [PORT]        add the impairment on UDP PORT (default 28890)
#   netem_4g.sh down [PORT]      remove it (always safe to run; `run` does it on exit too)
#   netem_4g.sh status
#   netem_4g.sh run [PORT]       up, then the manual driver twice (SRT latency 120 and 400 ms), then down
#
# Env: NETEM_LOSS=2 NETEM_DELAY_MS=40 NETEM_JITTER_MS=50 NETEM_SECONDS=30 (per run; keep <= 60 s)
set -euo pipefail
PATH="$PATH:/usr/sbin:/sbin"  # tc lives in /usr/sbin; the project wrapper uses a clean PATH

DEV=lo
PORT="${2:-28890}"
LOSS="${NETEM_LOSS:-2}"
DELAY="${NETEM_DELAY_MS:-40}"
JITTER="${NETEM_JITTER_MS:-50}"
SECONDS_PER_RUN="${NETEM_SECONDS:-30}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "netem_4g.sh needs root (tc qdisc on $DEV)" >&2
    exit 2
  fi
  command -v tc >/dev/null || { echo "tc (iproute2) not found" >&2; exit 2; }
}

down() {
  tc qdisc del dev "$DEV" root 2>/dev/null || true
}

up() {
  need_root
  down
  # prio with 3 bands; band 3 (handle 1:3) is the impaired one; everything else uses band 1.
  tc qdisc add dev "$DEV" root handle 1: prio bands 3 priomap 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
  tc qdisc add dev "$DEV" parent 1:3 handle 30: netem delay "${DELAY}ms" "${JITTER}ms" loss "${LOSS}%"
  # phone -> relay (dport) and relay -> phone (sport) on the SRT UDP port only
  tc filter add dev "$DEV" parent 1: protocol ip prio 1 u32 match ip protocol 17 0xff match ip dport "$PORT" 0xffff flowid 1:3
  tc filter add dev "$DEV" parent 1: protocol ip prio 1 u32 match ip protocol 17 0xff match ip sport "$PORT" 0xffff flowid 1:3
  echo "netem on $DEV udp/$PORT: delay ${DELAY}ms +/-${JITTER}ms, loss ${LOSS}%"
}

case "${1:-}" in
  up) up ;;
  down) need_root; down; echo "netem removed from $DEV" ;;
  status) tc qdisc show dev "$DEV"; tc filter show dev "$DEV" parent 1: 2>/dev/null || true ;;
  run)
    up
    trap down EXIT INT TERM
    cd "$REPO"
    for latency in 120 400; do
      echo "== SRT latency ${latency} ms, ${SECONDS_PER_RUN} s under impairment =="
      # one GPU is not used here, so no flock; the driver is CPU only
      python -m relay.tests.netem_4g_manual --srt-port "$PORT" --latency-ms "$latency" --seconds "$SECONDS_PER_RUN"
    done
    ;;
  *) echo "usage: $0 up|down|status|run [PORT]" >&2; exit 64 ;;
esac
