"""Demo driver (run by scripts/demo.sh): start a keyless rehearsal session on the running API, then loop the SPECIMEN
demo reel (video + speech audio) into it. Prints only local URLs. SIGTERM / Ctrl-C stops the sender and the session.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("DEMO_OUT", "/tmp/streamsarthi-demo"))
API = os.environ.get("DEMO_API", "http://127.0.0.1:8000")
LOG = Path(os.environ.get("DEMO_API_LOG", "/tmp/ss_api.log"))

text = LOG.read_text(errors="ignore")
m = re.search(r"#token=([A-Za-z0-9_.\-]+)", text)
if not m:
    print("demo_run: no token line in", LOG, file=sys.stderr)
    sys.exit(2)
token = m.group(1)
cl = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=30)

r = cl.post("/api/session/start", json={"destinations": [], "rehearsal": True, "delay_seconds": 4.0})
print("start session:", r.status_code, flush=True)
if r.status_code != 200:
    print(r.text[:300], file=sys.stderr)
    sys.exit(1)
t0 = time.monotonic()
ready = False
while time.monotonic() - t0 < 150:
    h = cl.get("/api/session/health").json()
    if h.get("ready"):
        ready = True
        break
    time.sleep(1)
print("engine ready:", ready, flush=True)
if not ready:
    sys.exit(1)
time.sleep(float(os.environ.get("DEMO_WARMUP_S", "8")))  # vision and audio models finish loading (CUDA init ~50 s)
try:  # captions on by default for the demo (the strip shows 'Waiting for speech' otherwise)
    cl.patch("/api/config", json={"captions": {"enabled": True}})
except Exception:  # noqa: BLE001 - cosmetic
    pass
try:  # enrol the reel's 'ENROLLED' person (keyless, RAM only) BEFORE the sender starts, so that face stays visible
    sys.path.insert(0, str(ROOT))
    from scripts.demo_enrol import engine_allowlist_version, enrol_demo

    if enrol_demo(cl):
        print("engine allowlist_version:", engine_allowlist_version(cl, want=1), flush=True)
    else:
        print("demo enrol failed: the enrolled-face scene will blur both faces", flush=True)
except Exception as exc:  # noqa: BLE001 - the demo still runs without it
    print("demo enrol skipped:", type(exc).__name__, flush=True)
srt = cl.get("/api/pairing").json()["srt"]["url"]
sender: subprocess.Popen[bytes] | None = None
stopping = False


def stop(*_a: object) -> None:
    global stopping
    stopping = True
    if sender is not None and sender.poll() is None:
        sender.terminate()
    try:
        cl.post("/api/session/stop")
    except Exception:  # noqa: BLE001 - best effort on shutdown
        pass


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

audio = OUT / "demo_reel.wav"
# One long sender process: a sender restart makes the engine start a new ingest generation (and the protected
# output connection did not come back after one in the first real run), so the reel loops inside one process.
cmd = [sys.executable, "-m", "eval.sender.sender", "--url", srt, "--script", "demo_reel", "--duration", "7200"]
if audio.exists():
    cmd += ["--audio-file", str(audio)]
print("streaming the SPECIMEN demo reel (loops every ~90 s)", flush=True)
print(
    "  open: http://127.0.0.1:3000/demo   (the login link is in the API log line printed above)", flush=True
)
while not stopping:
    with open("/tmp/ss_demo_sender.log", "ab") as lg:
        sender = subprocess.Popen(cmd, cwd=ROOT, stdout=lg, stderr=subprocess.STDOUT)
        while sender.poll() is None and not stopping:
            time.sleep(1)
    if not stopping:
        time.sleep(1)
sys.exit(0)
