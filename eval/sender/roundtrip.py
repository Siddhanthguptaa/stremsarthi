"""Stamp survival through x264: encode stamped frames to a file, decode, read the stamps back."""

from __future__ import annotations

import tempfile
from fractions import Fraction
from pathlib import Path

import av

from .scenes import ScenePlayer
from .stamp import CANVAS_H, CANVAS_W, draw_stamp, monotonic_ms, read_stamp


def x264_roundtrip(
    crf: int, n_frames: int = 60, script: str = "card_moving:10", preset: str = "veryfast"
) -> float:
    """Fraction of frames whose stamp decodes (CRC ok, correct counter) after x264 at `crf`."""
    player = ScenePlayer(script, 30)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "rt.mp4"
        out = av.open(str(path), "w")
        vs = out.add_stream("libx264", rate=30)
        vs.width, vs.height, vs.pix_fmt = CANVAS_W, CANVAS_H, "yuv420p"
        vs.options = {"crf": str(crf), "preset": preset, "g": "30", "bf": "0"}
        expect: dict[int, int] = {}
        for i in range(n_frames):
            frame, _, _ = player.render(i)
            ms = monotonic_ms(1000.0 + i * 0.0337)
            expect[i] = ms
            draw_stamp(frame, i, ms, i % 30 == 0)
            vf = av.VideoFrame.from_ndarray(frame, format="rgb24").reformat(format="yuv420p")
            vf.pts, vf.time_base = i, Fraction(1, 30)
            for pkt in vs.encode(vf):
                out.mux(pkt)
        for pkt in vs.encode(None):
            out.mux(pkt)
        out.close()
        ok = 0
        with av.open(str(path)) as inp:
            for n, fr in enumerate(inp.decode(video=0)):
                st = read_stamp(fr.to_ndarray(format="gray"))
                if st is not None and st.counter == n and st.capture_ms == expect.get(n):
                    ok += 1
    return ok / n_frames
