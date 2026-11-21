"""Real-input driver (run by scripts/real.sh): start a rehearsal session (no platform key needed) on the running API and
print where OBS or a phone should publish. No scripted reel, no fake chat: whatever you send is what gets protected.
Ctrl-C / SIGTERM stops the session. Prints local URLs only; the publish login is shown once because OBS needs it.
"""

from __future__ import annotations

import os
import re
import signal
import sys
import time
from pathlib import Path

import httpx

API = os.environ.get("DEMO_API", "http://127.0.0.1:8000")
LOG = Path(os.environ.get("DEMO_API_LOG", "/tmp/ss_api.log"))
text = LOG.read_text(errors="ignore")
m = re.search(r"#token=([A-Za-z0-9_.\-]+)", text)
if not m:
    print("real_run: no token line in", LOG, file=sys.stderr)
    sys.exit(2)
cl = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {m.group(1)}"}, timeout=30)

LIVE = os.environ.get("REAL_LIVE") == "1"
body: dict[str, object] = {"destinations": [], "rehearsal": True, "delay_seconds": 4.0}
if LIVE:
    # Go live for real: send to every platform whose stream key is configured (.env or typed on /go-live).
    dests = cl.get("/api/destinations").json().get("destinations", [])
    kinds = [d["kind"] for d in dests if d.get("configured")]
    if not kinds:
        print(
            "\nNo stream key is configured, so there is nothing to go live to.\n"
            "Add YOUTUBE_STREAM_KEY and/or TWITCH_STREAM_KEY to .env (edit the file yourself), or paste a key on the\n"
            "Go live page, then run this again. (Without --live this launcher runs a local-only rehearsal.)",
            file=sys.stderr,
        )
        sys.exit(3)
    body = {
        "destinations": [{"kind": k, "enabled": True} for k in kinds],
        "rehearsal": False,
        "delay_seconds": 4.0,
    }
    print("going live to:", ", ".join(kinds), flush=True)
r = cl.post("/api/session/start", json=body)
print("start session:", r.status_code, flush=True)
if r.status_code != 200:
    print(r.text[:300], file=sys.stderr)
    sys.exit(1)
t0 = time.monotonic()
ready = False
while time.monotonic() - t0 < 180:
    h = cl.get("/api/session/health").json()
    if h.get("ready"):
        ready = True
        break
    time.sleep(1)
print("engine ready:", ready, flush=True)
if not ready:
    sys.exit(1)
try:
    cl.patch("/api/config", json={"captions": {"enabled": True}})
except Exception:  # noqa: BLE001 - cosmetic
    pass
pr = cl.get("/api/pairing")
p = pr.json() if pr.status_code == 200 else {}
if pr.status_code != 200:
    print(
        f"\nNo publish address yet (pairing answered {pr.status_code}). On a phone-reachable bind set PAIRING_HOST=<this PC's LAN IPv4> "
        "in .env when WSL uses NAT networking, then run again.",
        file=sys.stderr,
    )
print("\n=== Point your streaming app here (SRT is best on a phone, RTMP works in OBS) ===")
print("  RTMP:", p.get("rtmp", {}).get("url"))
print("  SRT :", p.get("srt", {}).get("url"))
if os.environ.get("INGEST_BIND_HOST", "127.0.0.1") not in ("127.0.0.1", ""):
    print(
        "  (phone-reachable ingest: RTMP sends the login in cleartext on your network, SRT is encrypted: use SRT)"
    )
if p.get("moblin_url"):
    print("  Moblin import link:", p["moblin_url"])
print("\nThen open  http://127.0.0.1:3000/demo  (left: what your camera sees, right: what viewers get).")
print("Enrol your own face first on the Faces page if you want it to stay visible.\n", flush=True)

stopping = False


def stop(*_a: object) -> None:
    global stopping
    stopping = True


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
while not stopping:
    time.sleep(1)
try:
    cl.post("/api/session/stop")
except Exception:  # noqa: BLE001 - best effort on shutdown
    pass
