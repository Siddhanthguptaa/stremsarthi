#!/usr/bin/env bash
# Run a command inside the project (WSL), with the project venv, user-level tools and a clean PATH.
#
# The repo lives on the Windows disk (C:\Users\suresh\OneDrive\Desktop\stremsarthi) and WSL
# reaches it at /mnt/c/Users/suresh/OneDrive/Desktop/stremsarthi. This is deliberate: the old
# location inside WSL was reached through the \\wsl$ network share, which kept dropping and made
# every tool call fail with "The working directory no longer leads where it did". Local Windows
# paths always resolve.
#
# From the Windows side (PowerShell) use:
#   wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/suresh/OneDrive/Desktop/stremsarthi/scripts/ss.sh <command...>
#
# The Python venv stays in WSL's own filesystem (fast, supports symlinks): ~/.venvs/stremsarthi.
# GPU commands: prefix the command with `flock /tmp/streamsarthi-gpu.lock`.
set -uo pipefail
USER_NAME="${SS_USER:-suresh}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${SS_VENV:-$HOME_DIR/.venvs/stremsarthi}"

export HOME="$HOME_DIR"
export PATH="$VENV/bin:$HOME_DIR/.local/bin:$HOME_DIR/.local/node/bin:/usr/local/bin:/usr/bin:/bin:/usr/lib/wsl/lib"
export VIRTUAL_ENV="$VENV"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export CUDA_CACHE_MAXSIZE="${CUDA_CACHE_MAXSIZE:-2147483648}"
export SS_ROOT="$ROOT" SS_VENV="$VENV"
cd "$ROOT"

if [ "$(id -u)" = 0 ]; then
  git config --global --get-all safe.directory 2>/dev/null | grep -qx '\*' || git config --global --add safe.directory '*'
fi

# Memory guard: the VM has ~6 GB and Windows has little spare RAM. A runaway process can trigger the kernel OOM
# killer and freeze WSL. Each command runs in its own cgroup scope with a hard limit, so only the offender is
# killed (exit 137). Override with SS_MEM_MAX=6G, or disable with SS_NO_MEMCAP=1.
if [ "${SS_NO_MEMCAP:-0}" != 1 ] && command -v systemd-run >/dev/null 2>&1 && [ "$(id -u)" = 0 ]; then
  systemd-run --scope --quiet -p MemoryMax="${SS_MEM_MAX:-3500M}" -p MemorySwapMax=0 "$@"
else
  "$@"
fi
rc=$?
if [ "$rc" = 137 ]; then echo "ss.sh: command killed at the ${SS_MEM_MAX:-3500M} memory limit (likely a memory leak or an oversized test)" >&2; fi
exit $rc
