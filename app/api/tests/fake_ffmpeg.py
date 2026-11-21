"""Stand-in for ``ffmpeg`` in the demo preview tests (SPECIMEN frames: a fake JPEG is just SOI + text + EOI).

Env: ``FAKE_FFMPEG_LOG`` (a file: pid, argv and stdin are recorded there as JSON), ``FAKE_FFMPEG_MODE``:
``stream`` (default, 20 frames per second forever), ``finite`` (5 frames in odd-sized chunks, then exit 0),
``die`` (exit 1 at once), ``silent`` (no frames, sleeps).
"""

from __future__ import annotations

import json
import os
import sys
import time


def main() -> None:
    argv = sys.argv[1:]
    stdin_text = ""
    if "pipe:0" in argv:
        stdin_text = sys.stdin.read()
    log = os.environ.get("FAKE_FFMPEG_LOG")
    if log:
        with open(log, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "argv": argv, "stdin": stdin_text}, fh)
    mode = os.environ.get("FAKE_FFMPEG_MODE", "stream")
    out = sys.stdout.buffer
    if mode == "die":
        sys.exit(1)
    if mode == "silent":
        time.sleep(60)
        return
    n = 0
    while True:
        n += 1
        frame = b"\xff\xd8\xff\xe0" + f"FRAME{n:04d}".encode() + b"\xff\xd9"
        if mode == "finite":
            for i in range(0, len(frame), 5):
                out.write(frame[i : i + 5])
                out.flush()
            if n >= 5:
                return
        else:
            out.write(frame)
            out.flush()
        time.sleep(0.05)


if __name__ == "__main__":
    main()
