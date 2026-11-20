#!/usr/bin/env bash
# Run a command inside the project (WSL), with the project venv, user-level tools and a clean PATH.
#
# From the Windows side (Git Bash) use:
#   MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /home/siddhanth/stremsarthi/scripts/ss.sh <command...>
#
# Why root: files written from Windows through \\wsl$ are owned by root inside WSL, so the normal
# user cannot write beside them. Running as root avoids permission errors, and the wrapper hands
# ownership back to the normal user afterwards. It is a no-op when already run as that user.
# GPU commands: prefix the command with `flock /tmp/streamsarthi-gpu.lock`.
set -uo pipefail
USER_NAME="${SS_USER:-siddhanth}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export HOME="$HOME_DIR"
export PATH="$ROOT/.venv/bin:$HOME_DIR/.local/bin:$HOME_DIR/.local/node/bin:/usr/local/bin:/usr/bin:/bin:/usr/lib/wsl/lib"
export VIRTUAL_ENV="$ROOT/.venv"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export CUDA_CACHE_MAXSIZE="${CUDA_CACHE_MAXSIZE:-2147483648}"
cd "$ROOT"

if [ "$(id -u)" = 0 ]; then
  git config --global --get-all safe.directory 2>/dev/null | grep -qx '\*' || git config --global --add safe.directory '*'
fi

# Memory guard: the VM has ~7.8 GB and Windows has no spare RAM. A runaway process used to trigger the
# kernel OOM killer, which stalls the \wsl$ share and makes every tool call fail ("working directory no
# longer leads where it did"). Each command now runs in its own cgroup scope with a hard limit, so only the
# offender is killed (exit 137). Override with SS_MEM_MAX=6G, or disable with SS_NO_MEMCAP=1.
if [ "${SS_NO_MEMCAP:-0}" != 1 ] && command -v systemd-run >/dev/null 2>&1 && [ "$(id -u)" = 0 ]; then
  systemd-run --scope --quiet -p MemoryMax="${SS_MEM_MAX:-3500M}" -p MemorySwapMax=0 "$@"
else
  "$@"
fi
rc=$?
if [ "$rc" = 137 ]; then echo "ss.sh: command killed at the ${SS_MEM_MAX:-3500M} memory limit (likely a memory leak or an oversized test)" >&2; fi

# No ownership restore: commands run as root, and a repo-wide chown after every command hammered the 9P file server
# behind \wsl$ (kernel log: p9handler LookupFid errors), which made the share drop for every agent.
exit $rc
