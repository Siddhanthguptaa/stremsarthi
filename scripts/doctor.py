#!/usr/bin/env python3
"""Environment doctor. Exit 0 = ok, 1 = warnings only, 2 = blocking failure. ``--json`` for machines.

Reports presence only for secrets, never values.

``--real`` prints a short real-live-stream readiness checklist (PASS / FIX / OPTIONAL with the exact fix command).
Exit 0 only if every REQUIRED item passes (ports free, ffmpeg with libx264 and srt, dev-profile weights). Run it in WSL:
  MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh python scripts/doctor.py --real
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata as md
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS: list[dict[str, str]] = []


ARGS: argparse.Namespace = argparse.Namespace(json=False)


def record(status: str, name: str, detail: str = "", fix: str = "") -> None:
    RESULTS.append({"status": status, "name": name, "detail": detail, "fix": fix})
    if not ARGS.json:
        tail = f"  -> {fix}" if fix and status != "PASS" else ""
        print(f"[{status:4}] {name}: {detail}{tail}")


def run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def check_python() -> None:
    v = sys.version_info
    ok = (v.major, v.minor) == (3, 11)
    record(
        "PASS" if ok else "FAIL",
        "python",
        f"{v.major}.{v.minor}.{v.micro}",
        "use Python 3.11 (uv python install 3.11)",
    )
    in_venv = sys.prefix != sys.base_prefix
    record("PASS" if in_venv else "WARN", "venv", sys.prefix, "activate .venv")
    record("PASS" if shutil.which("uv") else "WARN", "uv", shutil.which("uv") or "missing")


def check_gpu(profile: str) -> None:
    if not shutil.which("nvidia-smi"):
        record("WARN", "nvidia-smi", "not found (CPU-only is supported)")
        return
    rc, out = run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version,compute_cap",
            "--format=csv,noheader,nounits",
        ]
    )
    if rc != 0:
        record("WARN", "gpu", out)
        return
    name, mem, drv, cc = [x.strip() for x in out.splitlines()[0].split(",")]
    record("PASS", "gpu", f"{name}, {mem} MiB, driver {drv}, compute {cc}")
    cap = 3500 if profile == "dev" else 7000
    if int(float(mem)) * 0.85 < cap:
        record("WARN", "vram", f"{profile} cap {cap} MB exceeds 85% of {mem} MiB")
    else:
        record("PASS", "vram", f"{profile} cap {cap} MB fits {mem} MiB")
    if float(drv.split(".")[0]) < 570:
        record("FAIL", "driver", drv, "update the Windows NVIDIA driver to 570+")


def check_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        record("FAIL", "ffmpeg", "missing", "sudo apt install ffmpeg")
        return
    _, enc = run(["ffmpeg", "-hide_banner", "-encoders"])
    _, proto = run(["ffmpeg", "-hide_banner", "-protocols"])
    record(
        "PASS" if "libx264" in enc else "FAIL", "ffmpeg libx264", "present" if "libx264" in enc else "missing"
    )
    record(
        "PASS" if "h264_nvenc" in enc else "WARN",
        "ffmpeg h264_nvenc",
        "present" if "h264_nvenc" in enc else "missing (x264 fallback)",
    )
    record("PASS" if re.search(r"^\s*srt\s*$", proto, re.M) else "FAIL", "ffmpeg srt", "present")
    record(
        "PASS" if Path("/usr/lib/wsl/lib/libnvidia-encode.so.1").exists() else "WARN",
        "nvenc lib",
        "/usr/lib/wsl/lib/libnvidia-encode.so.1",
    )


def check_onnxruntime(want_gpu: bool) -> None:
    dists = [d for d in ("onnxruntime", "onnxruntime-gpu") if _has(d)]
    if len(dists) != 1:
        record(
            "FAIL", "onnxruntime dist", f"installed: {dists}", "exactly one of onnxruntime / onnxruntime-gpu"
        )
        return
    record("PASS", "onnxruntime dist", f"{dists[0]} {md.version(dists[0])}")
    try:
        ort = importlib.import_module("onnxruntime")
        if hasattr(ort, "preload_dlls") and dists[0] == "onnxruntime-gpu":
            try:
                ort.preload_dlls()
            except Exception as exc:  # noqa: BLE001
                record("WARN", "ort preload_dlls", str(exc)[:200])
        providers = ort.get_available_providers()
        has_cuda = "CUDAExecutionProvider" in providers
        record("PASS" if (has_cuda or not want_gpu) else "WARN", "ort providers", ",".join(providers))
    except Exception as exc:  # noqa: BLE001
        record("FAIL", "onnxruntime import", str(exc)[:200])


def _has(dist: str) -> bool:
    try:
        md.version(dist)
        return True
    except md.PackageNotFoundError:
        return False


def check_torch(profile: str) -> None:
    if not _has("torch"):
        record("PASS", "torch", "not installed (optional extra)")
        return
    import torch  # type: ignore[import-not-found]

    arches = torch.cuda.get_arch_list() if torch.cuda.is_available() else []
    need = "sm_86" if profile == "dev" else "sm_120"
    record("PASS" if need in arches else "WARN", "torch arch", f"cuda={torch.cuda.is_available()} {arches}")


def check_cv() -> None:
    dists = [
        d
        for d in (
            "opencv-python",
            "opencv-python-headless",
            "opencv-contrib-python",
            "opencv-contrib-python-headless",
        )
        if _has(d)
    ]
    record(
        "PASS" if dists == ["opencv-contrib-python-headless"] else "FAIL",
        "opencv dists",
        ",".join(dists) or "none",
        "keep only opencv-contrib-python-headless",
    )
    try:
        import cv2

        record("PASS" if hasattr(cv2, "FaceDetectorYN") else "FAIL", "cv2.FaceDetectorYN", cv2.__version__)
        record(
            "PASS" if hasattr(cv2, "wechat_qrcode") else "WARN",
            "cv2.wechat_qrcode",
            "present" if hasattr(cv2, "wechat_qrcode") else "missing",
        )
    except Exception as exc:  # noqa: BLE001
        record("FAIL", "cv2 import", str(exc)[:200])


def check_raqm() -> None:
    try:
        from PIL import features

        ok = features.check("raqm")
        record(
            "PASS" if ok else "WARN",
            "libraqm (Indic caption shaping)",
            "available" if ok else "missing",
            "sudo apt install libraqm0",
        )
    except Exception as exc:  # noqa: BLE001
        record("WARN", "pillow", str(exc)[:200])


def check_tools() -> None:
    lock = ROOT / "models" / "tools.lock.json"
    if not lock.exists():
        record("WARN", "mediamtx", "no models/tools.lock.json", "bash scripts/fetch_tools.sh")
        return
    ver = json.loads(lock.read_text())["mediamtx"]["version"]
    exe = ROOT / ".tmp" / "tools" / f"mediamtx-v{ver}" / "mediamtx"
    record(
        "PASS" if exe.exists() else "WARN",
        "mediamtx",
        f"v{ver} {'present' if exe.exists() else 'missing'}",
        "bash scripts/fetch_tools.sh",
    )


def check_models() -> None:
    manifest = ROOT / "models" / "manifest.json"
    if not manifest.exists():
        record("WARN", "models manifest", "models/manifest.json not created yet (task T0.5)")
        return
    record("PASS", "models manifest", "present (run models/prepare.py to fetch and verify SHA-256)")


def check_network() -> None:
    rc, out = run(["wslinfo", "--networking-mode"])
    if rc == 0:
        record(
            "PASS" if "mirrored" in out else "WARN",
            "wsl networking",
            out,
            "set networkingMode=mirrored in %UserProfile%\\.wslconfig (phone SRT ingest)",
        )
    else:
        record("WARN", "wsl networking", "unknown (wslinfo unavailable)")


def check_env_file() -> None:
    env = ROOT / ".env"
    if not env.exists():
        record("WARN", ".env", "missing", "cp .env.example .env and fill in keys")
        return
    keys = {}
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            keys[k.strip()] = bool(v.strip())
    want = [
        "YOUTUBE_STREAM_KEY",
        "GROQ_API_KEY",
        "SARVAM_API_KEY",
        "YOUTUBE_OAUTH_CLIENT_ID",
        "TWITCH_CLIENT_ID",
    ]
    for k in want:
        record(
            "PASS" if keys.get(k) else "WARN",
            f"env {k}",
            "set" if keys.get(k) else "empty (features that need it will use mocks/fallbacks)",
        )


def check_misc() -> None:
    for tool, note in (("fpcalc", "optional: chromaprint CLI"), ("espeak-ng", "optional: offline TTS")):
        record(
            "PASS" if shutil.which(tool) else "WARN",
            tool,
            "present" if shutil.which(tool) else f"missing ({note})",
        )
    free_gb = shutil.disk_usage(ROOT).free / 1e9
    record("PASS" if free_gb >= 10 else "WARN", "disk", f"{free_gb:.0f} GB free")
    try:
        lock = Path("/tmp/streamsarthi-gpu.lock")
        lock.touch(exist_ok=True)
        record("PASS", "gpu lock", str(lock))
    except OSError as exc:
        record("WARN", "gpu lock", str(exc))
    try:
        sys.path.insert(0, str(ROOT))
        import contracts

        record("PASS", "contracts", f"v{contracts.CONTRACT_VERSION}")
    except Exception as exc:  # noqa: BLE001
        record("FAIL", "contracts import", str(exc)[:200])


# ---------------------------------------------------------------------------------------------------------------
# --real: readiness for a real live stream. Pure functions return items; nothing here prints a secret value.
# ---------------------------------------------------------------------------------------------------------------

PORTS: dict[int, str] = {
    8000: "API",
    3000: "web app",
    8890: "SRT ingest (UDP)",
    1935: "RTMP ingest",
    9997: "engine/MediaMTX control",
}
WSL_FIX = (
    "MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash "
    "/mnt/c/Users/siddh/stremsarthi/scripts/ss.sh python scripts/doctor.py --real"
)
# name -> (what it unlocks, what runs without it)
KEYS: dict[str, tuple[str, str]] = {
    "YOUTUBE_STREAM_KEY": ("live output to YouTube", "no YouTube output (rehearsal / local file only)"),
    "TWITCH_STREAM_KEY": ("live output to Twitch", "no Twitch output"),
    "YOUTUBE_OAUTH_CLIENT_ID": ("YouTube live chat + delete/timeout", "chat shield off for YouTube"),
    "YOUTUBE_OAUTH_CLIENT_SECRET": ("YouTube live chat + delete/timeout", "chat shield off for YouTube"),
    "TWITCH_CLIENT_ID": ("Twitch chat + timeout", "chat shield off for Twitch"),
    "TWITCH_CLIENT_SECRET": ("Twitch chat + timeout", "chat shield off for Twitch"),
    "GROQ_API_KEY": (
        "Whisper STT + chat/PII classification",
        "local faster-whisper (CPU) + rule-based chat/PII classifiers",
    ),
    "SARVAM_API_KEY": (
        "Indian-language STT + live captions translation",
        "Groq/local STT (Hinglish only); captions off",
    ),
    "HF_TOKEN": ("gated IndicTrans2 (demo profile captions only)", "not needed in dev profile"),
    "INGEST_PUBLISH_USER": ("stable OBS/phone pairing login", "random login per run (re-pair each time)"),
    "INGEST_PUBLISH_PASSWORD": ("stable OBS/phone pairing login", "random login per run (re-pair each time)"),
}


@dataclass
class Item:
    status: str  # PASS | FIX | OPTIONAL
    name: str
    detail: str = ""
    fix: str = ""
    required: bool = False


def env_names_present(
    env_file: Path | None = None, environ: Mapping[str, str] | None = None, names: Iterable[str] | None = None
) -> set[str]:
    """Names (never values) of variables that have a non-empty value in the environment or the ``.env`` file."""
    present: set[str] = set()
    if env_file is not None and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if value.strip().strip("'\"").strip():
                present.add(key.strip().removeprefix("export ").strip())
    for key, value in (os.environ if environ is None else environ).items():
        if value.strip():
            present.add(key)
    return present & set(names) if names is not None else present


def port_free(port: int, udp: bool = False) -> bool:
    """True if nothing is bound to ``port`` (a bind attempt on all interfaces, closed immediately)."""
    kind = socket.SOCK_DGRAM if udp else socket.SOCK_STREAM
    with socket.socket(socket.AF_INET, kind) as sock:
        try:
            sock.bind(("0.0.0.0", port))  # noqa: S104 - probing, never listens
        except OSError:
            return False
    return True


def real_keys(present: set[str]) -> list[Item]:
    items = []
    for name, (unlocks, fallback) in KEYS.items():
        if name in present:
            items.append(Item("PASS", f"key {name}", f"set; unlocks {unlocks}"))
        else:
            items.append(
                Item(
                    "OPTIONAL",
                    f"key {name}",
                    f"missing; unlocks {unlocks}. Without it: {fallback}",
                    f"add {name}=... to .env (names only are checked, values are never shown)",
                )
            )
    return items


def real_ports(free: Callable[[int, bool], bool] = port_free) -> list[Item]:
    busy = [p for p in PORTS if not free(p, p == 8890)]
    if not busy:
        return [Item("PASS", "ports", ", ".join(f"{p} {n}" for p, n in PORTS.items()) + " all free", required=True)]
    detail = ", ".join(f"{p} ({PORTS[p]})" for p in busy) + " in use"
    fix = "stop the other stack: pkill -f 'engine.main'; pkill -f mediamtx; pkill -f 'app.api.main'; pkill -f 'next-server'"
    return [Item("FIX", "ports", detail, fix, required=True)]


def real_gpu(run_cmd: Callable[[list[str]], tuple[int, str]] = run) -> list[Item]:
    items: list[Item] = []
    if not shutil.which("nvidia-smi"):
        items.append(
            Item("OPTIONAL", "gpu", "nvidia-smi not found: CPU path only", "update the Windows NVIDIA driver (570+)")
        )
    else:
        rc, out = run_cmd(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.free", "--format=csv,noheader,nounits"]
        )
        if rc != 0 or not out:
            items.append(Item("OPTIONAL", "gpu", "nvidia-smi failed: CPU path only", "run nvidia-smi in WSL and read the error"))
        else:
            name, drv, total, free = [x.strip() for x in out.splitlines()[0].split(",")]
            items.append(Item("PASS", "gpu", f"{name}, driver {drv}, {free} of {total} MiB VRAM free"))
            if float(free) < 3500:
                items.append(
                    Item("OPTIONAL", "vram free", f"{free} MiB free, dev profile needs 3500", "close other GPU apps (browser, games)")
                )
    # onnxruntime / torch without loading any model
    try:
        providers = importlib.import_module("onnxruntime").get_available_providers()
        has = "CUDAExecutionProvider" in providers
        items.append(
            Item(
                "PASS" if has else "OPTIONAL",
                "onnxruntime CUDA provider",
                ",".join(providers),
                "" if has else "pip install onnxruntime-gpu (CUDA 12.8+ build), uninstall plain onnxruntime",
            )
        )
    except Exception as exc:  # noqa: BLE001
        items.append(Item("OPTIONAL", "onnxruntime", str(exc)[:80], "pip install -r requirements/base.txt"))
    try:
        ver = md.version("torch")
        items.append(Item("PASS" if "+cu" in ver else "OPTIONAL", "torch build", ver, "install the cu128 torch wheel"))
    except md.PackageNotFoundError:
        items.append(Item("OPTIONAL", "torch", "not installed (not needed for the CPU path)"))
    items.append(_gpu_lock())
    return items


def _gpu_lock(path: str = "/tmp/streamsarthi-gpu.lock") -> Item:
    try:
        import fcntl

        with open(path, "a") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return Item("FIX", "gpu lock", "held by another GPU job", "wait for it to finish (scripts/mem_check.sh, gates)")
            fcntl.flock(fh, fcntl.LOCK_UN)
        return Item("PASS", "gpu lock", "free")
    except (ImportError, OSError) as exc:
        return Item("OPTIONAL", "gpu lock", f"not checked ({exc.__class__.__name__}); run inside WSL")


def real_ffmpeg(run_cmd: Callable[[list[str]], tuple[int, str]] = run) -> list[Item]:
    if not shutil.which("ffmpeg"):
        return [Item("FIX", "ffmpeg", "missing", "sudo apt install ffmpeg", required=True)]
    _, enc = run_cmd(["ffmpeg", "-hide_banner", "-encoders"])
    _, proto = run_cmd(["ffmpeg", "-hide_banner", "-protocols"])
    srt = bool(re.search(r"^\s*srt\s*$", proto, re.M))
    nv = "h264_nvenc" in enc
    return [
        Item("PASS" if "libx264" in enc else "FIX", "ffmpeg libx264", "x264 encoder", "sudo apt install ffmpeg", required=True),
        Item("PASS" if srt else "FIX", "ffmpeg libsrt", "SRT protocol", "install an ffmpeg build with libsrt (scripts/setup_wsl.sh)", required=True),
        Item("PASS" if nv else "OPTIONAL", "ffmpeg h264_nvenc", "NVENC" if nv else "absent: x264 on the CPU is used"),
    ]


def real_weights(profile: str = "dev", env: Mapping[str, str] | None = None) -> list[Item]:
    """Vision weights through ``models.prepare.verified_path`` (hash check, no network) plus local STT/VAD."""
    sys.path.insert(0, str(ROOT))
    items: list[Item] = []
    try:
        from models import prepare

        names = prepare.profile_names(profile)
        bad = []
        for n in names:
            try:
                prepare.verified_path(n)
            except (FileNotFoundError, ValueError):
                bad.append(n)
        if bad:
            items.append(
                Item(
                    "FIX",
                    f"{profile} model weights",
                    f"{len(names) - len(bad)}/{len(names)} verified; missing or corrupt: {', '.join(bad)}",
                    f"python -m models.prepare --profile {profile}",
                    required=True,
                )
            )
        else:
            items.append(Item("PASS", f"{profile} model weights", f"{len(names)}/{len(names)} hash-verified", required=True))
    except Exception as exc:  # noqa: BLE001
        items.append(Item("FIX", f"{profile} model weights", str(exc)[:100], f"python -m models.prepare --profile {profile}", required=True))
    try:
        from audio.models import WHISPER_REPOS, weights_dir

        size = "base"
        try:
            from contracts import load_config

            size = load_config(profile).audio.local_stt_model
        except Exception:  # noqa: BLE001
            pass
        d = weights_dir() / f"faster-whisper-{size}"
        fetch = (
            "python -c \"from audio.models import fetch_hf_model, WHISPER_REPOS, weights_dir; "
            f"fetch_hf_model(WHISPER_REPOS['{size}'], weights_dir() / 'faster-whisper-{size}')\""
        )
        ok = (d / "model.bin").exists()
        items.append(
            Item(
                "PASS" if ok else "OPTIONAL",
                f"local STT weights (faster-whisper-{size})",
                f"{d}" if ok else f"absent at {d}: no offline speech fallback ({WHISPER_REPOS.get(size)})",
                fetch,
            )
        )
        vad = weights_dir() / "silero_vad.onnx"
        items.append(
            Item(
                "PASS" if vad.exists() else "OPTIONAL",
                "silero VAD weights",
                "present" if vad.exists() else "absent: energy VAD fallback",
                'python -c "from audio.models import ensure_silero_vad; ensure_silero_vad()"',
            )
        )
    except Exception as exc:  # noqa: BLE001
        items.append(Item("OPTIONAL", "local STT weights", str(exc)[:100]))
    return items


NET_HOSTS = {
    "api.sarvam.ai": "Indian-language STT + captions",
    "api.groq.com": "Whisper STT + chat classification",
    "www.googleapis.com": "YouTube chat",
    "id.twitch.tv": "Twitch chat",
}


def _reach(host: str) -> bool:
    try:
        with socket.create_connection((host, 443), timeout=3):  # connect only: nothing is sent
            return True
    except OSError:
        return False


def real_network(reach: Callable[[str], bool] = _reach) -> list[Item]:
    with ThreadPoolExecutor(max_workers=len(NET_HOSTS)) as pool:
        results = dict(zip(NET_HOSTS, pool.map(reach, NET_HOSTS), strict=True))
    items = []
    for host, what in NET_HOSTS.items():
        ok = results[host]
        items.append(
            Item(
                "PASS" if ok else "OPTIONAL",
                f"network {host}",
                "OK" if ok else f"blocked or no DNS: {what} falls back to local/rules",
                "" if ok else "check Wi-Fi/VPN/firewall; in WSL try: getent hosts " + host,
            )
        )
    return items


def real_wsl_network() -> list[Item]:
    try:
        version = Path("/proc/version").read_text()
    except OSError:
        return [Item("OPTIONAL", "WSL networking", "not inside WSL", f"run via ss.sh: {WSL_FIX}")]
    if "microsoft" not in version.lower():
        return [Item("PASS", "networking", "native Linux: phone can reach the SRT port if the LAN firewall allows UDP 8890")]
    rc, out = run(["wslinfo", "--networking-mode"])
    mode = out.strip().lower() if rc == 0 else ""
    if not mode:  # heuristic: NAT puts eth0 on a private 172.16/12 or 192.168.x vEthernet that differs from the LAN
        _, addrs = run(["ip", "-4", "-o", "addr", "show", "eth0"])
        mode = "nat" if re.search(r"inet (172\.(1[6-9]|2\d|3[01])|192\.168\.\d+\.\d+/20)", addrs) else "unknown"
    fix = (
        "docs/PLAN.md section 9: .wslconfig [wsl2] networkingMode=mirrored + wsl --shutdown, then an admin "
        "PowerShell New-NetFirewallHyperVRule for UDP 8890 and TCP 1935"
    )
    if "mirrored" in mode:
        return [Item("PASS", "WSL networking", "mirrored: a phone on the same Wi-Fi can reach SRT 8890 once the Hyper-V firewall rule exists")]
    return [Item("OPTIONAL", "WSL networking", f"{mode or 'unknown'}: phones cannot reach the SRT port; OBS on this PC still works via 127.0.0.1", fix)]


def real_resources(environ: Mapping[str, str] | None = None) -> list[Item]:
    env = os.environ if environ is None else environ
    items: list[Item] = []
    try:
        info = {k: int(v.split()[0]) for k, v in (ln.split(":", 1) for ln in Path("/proc/meminfo").read_text().splitlines())}
        total, avail = info["MemTotal"] / 1048576, info["MemAvailable"] / 1048576
        ok = total >= 6.0 and avail >= 4.0
        items.append(
            Item(
                "PASS" if ok else "OPTIONAL",
                "memory (WSL)",
                f"{total:.1f} GB total, {avail:.1f} GB available; the engine uses 3.5-5.5 GB",
                "" if ok else "%UserProfile%\\.wslconfig [wsl2] memory=8GB, then wsl --shutdown; close browsers",
            )
        )
    except (OSError, KeyError, ValueError):
        items.append(Item("OPTIONAL", "memory (WSL)", "not readable (not Linux)"))
    cap = env.get("SS_MEM_MAX", "3500M")
    capped = env.get("SS_NO_MEMCAP", "0") != "1"
    if capped and cap.upper().endswith("M") and cap[:-1].isdigit() and int(cap[:-1]) < 6000:
        items.append(
            Item(
                "OPTIONAL",
                "ss.sh memory cap",
                f"ss.sh kills a command at {cap}; the real engine can need 5.5 GB",
                "prefix the real run with SS_MEM_MAX=7G (or SS_NO_MEMCAP=1)",
            )
        )
    free_gb = shutil.disk_usage(ROOT).free / 1e9
    items.append(
        Item("PASS" if free_gb >= 10 else "OPTIONAL", "disk", f"{free_gb:.0f} GB free", "" if free_gb >= 10 else "free up 10 GB")
    )
    return items


def real_checklist(profile: str = "dev") -> list[Item]:
    present = env_names_present(ROOT / ".env", names=KEYS)
    return [
        *real_gpu(),
        *real_ports(),
        *real_ffmpeg(),
        *real_weights(profile),
        *real_keys(present),
        *real_network(),
        *real_wsl_network(),
        *real_resources(),
    ]


def real_exit_code(items: list[Item]) -> int:
    return 1 if any(i.required and i.status != "PASS" for i in items) else 0


def run_real(profile: str, as_json: bool = False) -> int:
    items = real_checklist(profile)
    code = real_exit_code(items)
    if as_json:
        print(json.dumps([asdict(i) for i in items], indent=2))
        return code
    print(f"Stream Sarthi real-mode readiness (profile {profile})")
    for i in items:
        mark = " (required)" if i.required and i.status != "PASS" else ""
        print(f"[{i.status:8}] {i.name}: {i.detail}{mark}")
        if i.fix and i.status != "PASS":
            print(f"           fix: {i.fix}")
    n_fix = sum(1 for i in items if i.status == "FIX")
    n_opt = sum(1 for i in items if i.status == "OPTIONAL")
    verdict = "READY for a real stream" if code == 0 else "NOT READY: fix the required items above"
    print(f"\n{verdict} ({n_fix} FIX, {n_opt} OPTIONAL). Then run: bash scripts/real.sh --gpu")
    return code


def main() -> int:
    if ARGS.real:
        return run_real(ARGS.profile, ARGS.json)
    check_python()
    profile = ARGS.profile
    check_gpu(profile)
    check_ffmpeg()
    check_onnxruntime(want_gpu=profile == "demo" or ARGS.gpu)
    check_torch(profile)
    check_cv()
    check_raqm()
    check_tools()
    check_models()
    check_network()
    check_env_file()
    check_misc()
    if ARGS.json:
        print(json.dumps(RESULTS, indent=2))
    worst = (
        2
        if any(r["status"] == "FAIL" for r in RESULTS)
        else 1
        if any(r["status"] == "WARN" for r in RESULTS)
        else 0
    )
    if not ARGS.json:
        print(
            f"\nresult: {'blocking failure' if worst == 2 else 'warnings only' if worst == 1 else 'ok'} (exit {worst})"
        )
    return worst


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", choices=["dev", "demo"], default=os.environ.get("STREAMSARTHI_PROFILE", "dev")
    )
    parser.add_argument("--gpu", action="store_true", help="expect the CUDA execution provider")
    parser.add_argument("--real", action="store_true", help="real live-stream readiness checklist (run in WSL)")
    parser.add_argument("--json", action="store_true")
    ARGS = parser.parse_args()
    sys.exit(main())
