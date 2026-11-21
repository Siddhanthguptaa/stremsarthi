"""End-to-end smoke of the whole local stack with the API spawning the real engine (no API keys, dev profile).

Driven by ``scripts/e2e_smoke.sh`` (through ``scripts/ss.sh``). Chain: API (``SESSION_SPAWN=true``) ->
``POST /api/session/start`` (rehearsal: no platform destination) -> real ``python -m engine.main`` ->
``GET /api/pairing`` -> synthetic SPECIMEN sender (``eval.sender.sender``) -> egress tap (``eval.tap``) while
the API is exercised (session, risk, alerts, WebSocket, hold, config) -> stop -> report/printable/export.

Nothing here reads ``.env`` for secrets or prints a credential; the publish login and the API token are
masked in the output; the run directory holds numbers and status codes only. Exit codes: 0 all checks pass,
1 a check failed, 2 the environment could not run the chain.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CHECKS: list[dict[str, Any]] = []
NOTES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""), flush=True)
    return bool(ok)


def free_port(kind: str = "tcp") -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM if kind == "tcp" else socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def descendants(pid: int) -> list[int]:
    """Pids below ``pid`` (Linux /proc walk)."""
    kids: dict[int, list[int]] = {}
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            stat = (d / "stat").read_text()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        kids.setdefault(ppid, []).append(int(d.name))
    out: list[int] = []
    stack = [pid]
    while stack:
        for k in kids.get(stack.pop(), []):
            out.append(k)
            stack.append(k)
    return out


def alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def mask(text: str) -> str:
    text = re.sub(r"(pass=|user=)[A-Za-z0-9_-]+", r"\1***", text)
    text = re.sub(r"publish:camera:[A-Za-z0-9_-]+:[A-Za-z0-9_-]+", "publish:camera:***:***", text)
    return re.sub(r"token=[A-Za-z0-9._~-]+", "token=***", text)


async def ws_collect(url: str, token: str, seconds: float, out: dict[str, Any]) -> None:
    import websockets

    topics = [
        "engine.status",
        "safety.event",
        "guard.risk",
        "guard.meter",
        "control.hold",
        "chat.verdict",
        "caption.event",
    ]
    counts: dict[str, int] = {}
    try:
        async with websockets.connect(url, origin="http://127.0.0.1:3000", open_timeout=5) as ws:
            await ws.send(json.dumps({"type": "auth", "token": token}))
            await ws.send(json.dumps({"type": "subscribe", "topics": topics}))
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - time.monotonic()))
                except TimeoutError:
                    break
                msg = json.loads(raw)
                key = msg.get("topic") or ("ctl:" + str(msg.get("type")))
                counts[key] = counts.get(key, 0) + 1
                if msg.get("type") == "error":
                    out.setdefault("errors", []).append(msg.get("code"))
    except Exception as exc:  # the result is data, not a crash
        out["exception"] = type(exc).__name__
    out["counts"] = counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=45.0)
    ap.add_argument("--delay", type=float, default=4.0)
    ap.add_argument("--profile", default="dev")
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--ready-timeout", type=float, default=150.0)
    args = ap.parse_args()

    tag = secrets.token_hex(3)
    run = Path(args.run_dir or f"/tmp/ss_e2e_{tag}")
    run.mkdir(parents=True, exist_ok=True)
    api_port = free_port()
    ports = {"rtmp": free_port(), "srt": free_port("udp"), "rtsp": free_port(), "mtx": free_port()}
    bus_sub, bus_pub = f"ipc://{run}/bus", f"ipc://{run}/bus-in"
    secret = secrets.token_urlsafe(32)
    engine_log = run / "engine.log"

    env = dict(os.environ)
    for k in ("YOUTUBE_STREAM_KEY", "TWITCH_STREAM_KEY"):
        env.pop(k, None)  # a rehearsal never streams to a platform, whatever the environment holds
    env.update(
        {
            "SECRET_KEY": secret,
            "SESSION_SPAWN": "true",
            "API_PORT": str(api_port),
            "BUS_BACKEND": "zmq",
            "BUS_SUB_ENDPOINT": bus_sub,
            "BUS_PUB_ENDPOINT": bus_pub,
            # the child's output is discarded by the orchestrator: wrap it so the smoke can read warnings
            "SESSION_ENGINE_CMD": (
                f"bash -c 'exec {sys.executable} -u -m engine.main --profile {{profile}} "
                f"--xpub {bus_sub} --xsub {bus_pub} > {engine_log} 2>&1'"
            ),
            "SESSION_STOP_TIMEOUT_S": "15",
            "SESSIONS_DIR": str(run / "sessions"),
            "INGEST_PORT_RTMP": str(ports["rtmp"]),
            "INGEST_PORT_SRT": str(ports["srt"]),
            "INGEST_PORT_RTSP": str(ports["rtsp"]),
            "RELAY__MEDIAMTX_API_PORT": str(ports["mtx"]),
            "GPU__ENABLED": "false",
            "GPU__ORT_PROVIDER": "cpu",
            "CHAT__ENABLED": "false",
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(ROOT),
        }
    )
    env.pop("SS_FAULT", None)

    api_log = run / "api.log"
    api: subprocess.Popen[bytes] | None = None
    sender: subprocess.Popen[bytes] | None = None
    tap_thread: threading.Thread | None = None
    kids: list[int] = []
    result: dict[str, Any] = {"run_dir": str(run), "ports": ports}
    base = f"http://127.0.0.1:{api_port}"
    stop_ev = threading.Event()
    rc = 1
    try:
        with api_log.open("wb") as alog:
            api = subprocess.Popen(
                [sys.executable, "-m", "app.api.main", "--port", str(api_port)],
                cwd=ROOT, env=env, stdout=alog, stderr=subprocess.STDOUT, start_new_session=True,
            )  # fmt: skip
            t_end = time.monotonic() + 30
            token = None
            while time.monotonic() < t_end and api.poll() is None:
                m = re.search(r"#token=([A-Za-z0-9._~-]+)", api_log.read_text(errors="ignore"))
                if m:
                    token = m.group(1)
                    break
                time.sleep(0.3)
            if not token:
                print("ENV: API did not start; see " + str(api_log))
                return 2
            hdr = {"Authorization": f"Bearer {token}", "Origin": "http://127.0.0.1:3000"}
            cl = httpx.Client(base_url=base, headers=hdr, timeout=15)
            for _ in range(40):
                try:
                    if cl.get("/api/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(0.25)
            h = cl.get("/api/health").json()
            check("api up, bus connected", h.get("status") == "ok", f"bus={h.get('bus')}")
            check("whoami streamer", cl.get("/api/auth/whoami").status_code in (200, 404))

            # ---- (2) start with spawning on
            r = cl.post(
                "/api/session/start",
                json={"destinations": [], "rehearsal": True, "delay_seconds": args.delay},
            )
            check(
                f"POST /api/session/start (rehearsal, delay {args.delay:.1f})",
                r.status_code == 200,
                f"{r.status_code} {mask(r.text)[:120]}",
            )
            if r.status_code != 200:
                return 1
            sid = r.json()["session_id"]
            t0 = time.monotonic()
            ready = False
            hs: dict[str, Any] = {}
            while time.monotonic() - t0 < args.ready_timeout:
                hs = cl.get("/api/session/health").json()
                if hs.get("ready"):
                    ready = True
                    break
                eng = (hs.get("processes") or {}).get("engine") or {}
                if eng.get("state") in ("exited", "failed"):
                    break
                time.sleep(1.0)
            check(
                "engine heartbeat ready (waiting_ingest)",
                ready,
                f"{time.monotonic() - t0:.0f}s state={hs.get('state')}",
            )
            result["health_ready"] = {k: hs.get(k) for k in ("state", "ready", "engine", "chat")}
            chat = hs.get("chat") or {}
            check(
                "chat disabled path clean",
                chat.get("enabled") is False and chat.get("process") == "disabled",
                json.dumps(chat),
            )
            if not ready:
                print(
                    "engine log tail:\n" + "\n".join(engine_log.read_text(errors="ignore").splitlines()[-25:])
                )
                return 1
            kids = descendants(api.pid)
            result["children_started"] = len(kids)

            # engine log: wait for "engine ready" (the sender must start only when vision/audio are loaded)
            t1 = time.monotonic()
            while time.monotonic() - t1 < 60 and "engine ready" not in engine_log.read_text(errors="ignore"):
                time.sleep(0.5)

            # ---- (3) pairing -> sender
            pr = cl.get("/api/pairing")
            check("GET /api/pairing", pr.status_code == 200, str(pr.status_code))
            pj = pr.json()
            rtmp_url, srt_url = pj["rtmp"]["url"], pj["srt"]["url"]
            check(
                "pairing carries the configured ports",
                f":{ports['rtmp']}/" in rtmp_url and f":{ports['srt']}?" in srt_url,
            )
            check("pairing has no stream key", "key" not in json.dumps(pj).lower().replace("streamid", ""))
            se = cl.get("/api/session").json()
            check(
                "GET /api/session ingest urls",
                bool(se["ingest"]["srt_url"]) and bool(se["ingest"]["rtmp_url"]),
                f"state={se['state']}",
            )

            script = "card_static:12,card_moving:12,card_static:12,card_absent:9"
            dur = args.duration
            sender_log = (run / "sender.log").open("wb")
            sender = subprocess.Popen(
                [sys.executable, "-m", "eval.sender.sender", "--url", srt_url, "--script", script,
                 "--duration", str(dur), "--truth", str(run / "truth.jsonl")],
                cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT)), stdout=sender_log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )  # fmt: skip
            t_send = time.monotonic()

            # egress tap (the local ``shielded`` path; MediaMTX serves it to localhost readers)
            from eval.tap import Tap, card_stats, stamp_stats

            tap = Tap(f"rtmp://127.0.0.1:{ports['rtmp']}/shielded", script, ocr=False)
            holder: dict[str, Any] = {}
            tap_thread = threading.Thread(
                target=lambda: holder.update(r=tap.run(dur + args.delay + 25.0, stop_ev, 90.0)), name="tap"
            )
            tap_thread.start()

            # ---- (4) exercise the API while it runs
            ws_out: dict[str, Any] = {}
            ws_thread = threading.Thread(
                target=lambda: asyncio.run(
                    ws_collect(f"ws://127.0.0.1:{api_port}/api/ws", token, 12.0, ws_out)
                ),
                name="ws",
            )

            def at(offset: float) -> None:
                while time.monotonic() - t_send < offset:
                    time.sleep(0.1)

            at(14.0)
            ws_thread.start()
            ses = cl.get("/api/session").json()
            check("session state live while publishing", ses["state"] == "live", ses["state"])
            risk = cl.get("/api/risk")
            check(
                "GET /api/risk",
                risk.status_code == 200,
                json.dumps({k: risk.json().get(k) for k in ("level", "meter_level", "score")})
                if risk.status_code == 200
                else str(risk.status_code),
            )
            al = cl.get("/api/alerts")
            check("GET /api/alerts", al.status_code == 200, str(al.status_code))
            ev = cl.get("/api/events/summary")
            result["events_summary_mid"] = ev.json() if ev.status_code == 200 else ev.status_code

            at(18.0)
            hr = cl.post("/api/hold", json={"active": True, "source": "app"})
            check("POST /api/hold active", hr.status_code == 200, str(hr.status_code))
            t_hold = time.monotonic()
            time.sleep(1.0)
            check("hold visible in /api/session", bool(cl.get("/api/session").json()["hold"]["active"]))
            time.sleep(max(0.0, 4.0 - (time.monotonic() - t_hold)))
            rr = cl.post("/api/hold", json={"active": False, "source": "app"})
            check("POST /api/hold release", rr.status_code == 200, str(rr.status_code))
            at(28.0)
            pr1 = cl.patch("/api/config", json={"categories": {"plate": {"enabled": False}}})
            check(
                "PATCH /api/config category off",
                pr1.status_code == 200 and pr1.json()["categories"]["plate"]["enabled"] is False,
                str(pr1.status_code),
            )
            time.sleep(1.0)
            pr2 = cl.patch("/api/config", json={"categories": {"plate": {"enabled": True}}})
            check(
                "PATCH /api/config category on",
                pr2.status_code == 200 and pr2.json()["categories"]["plate"]["enabled"] is True,
                str(pr2.status_code),
            )

            # wait for the sender, then the delay window
            sender.wait(timeout=dur + 60)
            time.sleep(args.delay + 3.0)
            ws_thread.join(timeout=20)
            stop_ev.set()
            tap_thread.join(timeout=90)
            res = holder.get("r")
            if res is None:
                check("tap produced a result", False)
            else:
                st, cs = stamp_stats(res), card_stats(res.frames)
                live = [f for f in res.frames if f.kind == "live"]
                check(
                    "egress frames flowing",
                    len(live) > 100,
                    f"live={len(live)} decoded={st['decoded']} err={res.errors[:1]}",
                )
                check(
                    "card masked at the egress",
                    cs["visible_frames"] > 30 and cs["leaked_frames"] == 0,
                    f"visible={cs['visible_frames']} leaked={cs['leaked_frames']} recall={cs['unreadable_recall']}",
                )
                check("hold produced a slate", st["slate_frames"] > 5, f"slate_frames={st['slate_frames']}")
                delays = sorted(f.delay_s for f in live if getattr(f, "delay_s", None) is not None)
                if delays:
                    result["delay_p50_p95_s"] = [
                        round(delays[len(delays) // 2], 2),
                        round(delays[int(len(delays) * 0.95)], 2),
                    ]
                result["tap"] = {
                    "stamp": st,
                    "card": {k: cs[k] for k in ("visible_frames", "leaked_frames", "unreadable_recall")},
                }
            result["ws"] = ws_out
            c = ws_out.get("counts", {})
            check(
                "websocket delivered events",
                sum(v for k, v in c.items() if not k.startswith("ctl:")) > 0,
                json.dumps(c),
            )

            # ---- (5) stop, report
            ts = time.monotonic()
            sp = cl.post("/api/session/stop")
            check("POST /api/session/stop", sp.status_code == 200, str(sp.status_code))
            final = "?"
            while time.monotonic() - ts < 40:
                final = cl.get("/api/session").json()["state"]
                if final == "ended":
                    break
                time.sleep(0.5)
            check("session ended (drained)", final == "ended", f"{final} after {time.monotonic() - ts:.1f}s")
            procs = cl.get("/api/session").json()["processes"]
            check(
                "engine stopped by SIGTERM, not killed",
                (procs.get("engine") or {}).get("state") == "stopped"
                and (procs.get("engine") or {}).get("exit_code") in (0, -15),
                json.dumps(procs.get("engine")),
            )
            left = [p for p in kids if alive(p)]
            check("no child process left", not left, f"left={left}")
            rep = cl.get(f"/api/report/{sid}")
            check("GET /api/report/{id}", rep.status_code == 200, str(rep.status_code))
            blob = rep.text
            if rep.status_code == 200:
                rj = rep.json()
                evs = rj.get("events") or rj.get("timeline") or []
                result["report_keys"] = sorted(rj.keys())
                result["report_event_count"] = len(evs)
                check("report has events", len(evs) > 0, f"events={len(evs)} keys={sorted(rj.keys())[:8]}")
            pt = cl.get(f"/api/report/{sid}/printable")
            ex = cl.get(f"/api/report/{sid}/export", params={"format": "json"})
            exh = cl.get(f"/api/report/{sid}/export", params={"format": "html"})
            check(
                "printable + export json/html",
                pt.status_code == 200 and ex.status_code == 200 and exh.status_code == 200,
                f"{pt.status_code} {ex.status_code} {exh.status_code}",
            )
            allbody = blob + pt.text + ex.text + exh.text
            bad = [
                n
                for n, pat in {
                    "image": r"data:image|base64,|\.png|\.jpe?g",
                    "digits12": r"\b\d{4}\s?\d{4}\s?\d{4}\b",
                    "phone": r"(?<!\d)[6-9]\d{9}(?!\d)",
                    "email": r"[\w.+-]+@[\w-]+\.[\w.]+",
                    "upi": r"upi://|@oksbi|@okaxis|@ybl",
                    "script": r"<script",
                    "login": r"(pass=|streamid=publish)",
                }.items()
                if re.search(pat, allbody)
            ]
            ctx = ""
            if (
                "phone" in bad
            ):  # show the neighbourhood with digits blanked, to tell a real number from a counter
                m = re.search(r"(?<!\d)[6-9]\d{9}(?!\d)", allbody)
                if m:
                    ctx = re.sub(r"\d", "#", allbody[max(0, m.start() - 50) : m.end() + 20]).replace(
                        chr(10), " "
                    )
            check(
                "report privacy scan (no image, PII, script, login)",
                not bad,
                "hits=" + ",".join(bad) + (f" ctx={ctx!r}" if ctx else ""),
            )

            # ---- engine log: exception class names and warning codes only (no message text)
            text = engine_log.read_text(errors="ignore")
            warn = [
                ln for ln in text.splitlines() if " WARNING " in ln or " ERROR " in ln or "Traceback" in ln
            ]
            classes = sorted(set(re.findall(r"\b([A-Z][A-Za-z]+(?:Error|Exception))\b", text)))
            result["engine_warnings"] = [mask(w)[:200] for w in warn[:40]]
            result["engine_exception_classes"] = classes
            NOTES.append(f"engine log warnings/errors: {len(warn)}; exception classes: {classes}")
            check("engine log has no Traceback", "Traceback" not in text, f"{len(warn)} warn/error lines")
            rc = 0 if all(c_["ok"] for c_ in CHECKS) else 1
            return rc
    except Exception as exc:
        print(f"ENV/ERROR: {type(exc).__name__}: {mask(str(exc))[:200]}")
        return 2 if not CHECKS else 1
    finally:
        stop_ev.set()
        if sender is not None and sender.poll() is None:
            os.killpg(sender.pid, signal.SIGTERM)
        if tap_thread is not None:
            tap_thread.join(timeout=20)
        if api is not None and api.poll() is None:
            kids2 = descendants(api.pid)
            os.killpg(api.pid, signal.SIGTERM)
            try:
                api.wait(15)
            except subprocess.TimeoutExpired:
                os.killpg(api.pid, signal.SIGKILL)
            time.sleep(1)
            for p in set(kids) | set(kids2):
                if alive(p):
                    NOTES.append(f"leftover child {p} killed")
                    try:
                        os.kill(p, signal.SIGKILL)
                    except OSError:
                        pass
        result["checks"] = CHECKS
        result["notes"] = NOTES
        (run / "result.json").write_text(json.dumps(result, indent=1, default=str))
        n_ok = sum(1 for c_ in CHECKS if c_["ok"])
        print(f"\nsmoke: {n_ok}/{len(CHECKS)} checks passed; run dir {run}")
        for n in NOTES:
            print("note:", n)


if __name__ == "__main__":
    sys.exit(main())
