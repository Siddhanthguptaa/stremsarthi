"""GPU lock: one GPU job at a time, same file as ``flock /tmp/streamsarthi-gpu.lock`` and scripts/gpu_run.sh.

    with gpu_lock() as lock:         # blocks until free; lock.waited_s reports the wait
        ...run the GPU job...

    python -m eval.harness.gpu_lock -- python -m something   # run a command under the lock

``fcntl.flock`` and flock(1) use the same flock(2) semantics, so Python and shell callers exclude each other.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

LOCK_PATH = "/tmp/streamsarthi-gpu.lock"


class GpuLockTimeout(TimeoutError):
    pass


@dataclass
class GpuLockInfo:
    path: str
    waited_s: float = 0.0


@contextmanager
def gpu_lock(
    timeout_s: float | None = None, path: str = LOCK_PATH, poll_s: float = 0.1
) -> Iterator[GpuLockInfo]:
    """Hold the GPU lock. `timeout_s=None` waits forever. Raises GpuLockTimeout when it cannot be taken."""
    info = GpuLockInfo(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    start = time.monotonic()
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if timeout_s is not None and time.monotonic() - start >= timeout_s:
                    raise GpuLockTimeout(f"GPU lock {path} busy for {timeout_s:.1f}s") from None
                time.sleep(poll_s)
        info.waited_s = time.monotonic() - start
        yield info
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def run_locked(cmd: list[str], timeout_s: float | None = None, **kwargs) -> subprocess.CompletedProcess:
    with gpu_lock(timeout_s):
        return subprocess.run(cmd, check=False, **kwargs)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        print("usage: python -m eval.harness.gpu_lock -- <command...>", file=sys.stderr)
        return 2
    return run_locked(args).returncode


if __name__ == "__main__":
    sys.exit(main())
