#!/usr/bin/env python3
"""Environment doctor. Exit 0 = ok, 1 = warnings only, 2 = blocking failure. ``--json`` for machines.

Reports presence only for secrets, never values.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata as md
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS: list[dict[str, str]] = []


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


def main() -> int:
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
    parser.add_argument("--json", action="store_true")
    ARGS = parser.parse_args()
    sys.exit(main())
