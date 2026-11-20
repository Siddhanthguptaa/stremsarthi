"""Reference engine: a tiny stand-in for ``engine/`` + ``relay/`` + ``vision/`` used to validate the gate.

It exists so that the tap reader, card checker, hold test, fault protocol and ``gate_phase1.sh`` can be
exercised end to end before (and independently of) the real engine, and so the gate can be shown to FAIL on a
deliberately broken build (docs/specs/eval.md acceptance T1.4). It is NOT product code and it cheats on
detection: the "detector" reads the scene ground truth (the same scene script the sender plays) for the
frame's stamp counter.

What it mimics from the real engine (docs/specs/engine.md): a delay buffer of ``D`` seconds with release at
``origin + pts + D``; the engine starts in PROTECT; a frame is LIVE only when the (simulated) accurate lane
has a result for it, otherwise PROTECT (slate); HOLD slates immediately, mutes audio, covers the buffered
retro window and fences until a newer frame arrives; hold commands arrive as ``contracts`` wire messages
on a ZeroMQ SUB socket bound to the bus's publisher endpoint; the engine timing log format; and the SS_FAULT
protocol (``eval/fault.py``): ``lane:accurate:hang`` stops detector results, ``sink:stall`` stops egress.

``--broken`` disables the fail-safe: frames without detector coverage go out unmasked (and holds are ignored),
which the gate must catch.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import av
import cv2
import numpy as np

from contracts import HoldCommand, loads

from .fault import FaultInjector
from .sender.scenes import ScenePlayer
from .sender.sender import open_output
from .sender.stamp import CANVAS_H, CANVAS_W, read_stamp

FIELD = (28, 22, 18)  # BGR of the slate colour
SOLID = (32, 32, 32)
PAD = 12
HANG_COVERAGE_S = 0.4  # how long the last detector result still covers frames (lane max age 12 frames)
RATE = 48000


@dataclass
class _Frame:
    seq: int
    pts: float  # rebased seconds
    arrival: float
    counter: int | None
    bgr: np.ndarray
    held: bool = False


@dataclass
class _Chunk:
    pts: float
    arrival: float
    pcm: np.ndarray
    held: bool = False


class RefEngine:
    def __init__(
        self,
        in_url: str,
        out_url: str,
        script: str,
        delay_s: float = 4.0,
        fps: int = 30,
        broken: bool = False,
        timing_path: Path | None = None,
        bus_endpoint: str | None = None,
        retro_s: float = 3.0,
        start_protect_s: float = 0.5,
    ) -> None:
        self.in_url, self.out_url = in_url, out_url
        self.player = ScenePlayer(script, fps)
        self.delay = delay_s
        self.fps = fps
        self.broken = broken
        self.retro = min(retro_s, delay_s - 0.5)
        self.start_protect_s = start_protect_s
        self.faults = FaultInjector.from_env("refengine")  # lane faults: clock starts at the first frame
        self.sink_faults = FaultInjector.from_env(
            "refengine-sink"
        )  # sink faults: clock starts at the first write
        self.timing_path = timing_path
        self.bus_endpoint = bus_endpoint
        self.vq: deque[_Frame] = deque()
        self.aq: deque[_Chunk] = deque()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.holds: dict[str, bool] = {}
        self.fence_seq: int | None = None
        self.max_seq = -1
        self.origin: float | None = None
        self.pts0: float | None = None
        self.t_first_emit: float | None = None
        self.slate = self._make_slate()
        self.counts = {"LIVE": 0, "PROTECT": 0, "HOLD": 0}
        self._timing = timing_path.open("w", encoding="utf-8") if timing_path else None

    # ------------------------------------------------------------ pieces

    @staticmethod
    def _make_slate() -> np.ndarray:
        img = np.full((CANVAS_H, CANVAS_W, 3), FIELD, np.uint8)
        cv2.putText(
            img,
            "Privacy hold - checking coverage",
            (260, 360),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.2,
            (243, 246, 249),
            2,
        )
        return img

    def _log(self, stage: str, seq: int, pts: float, t0: float, t1: float) -> None:
        if self._timing is None:
            return
        line = {"stage": stage, "seq": seq, "pts": round(pts, 6), "t_start": round(t0, 6), "t_end": round(t1, 6),
                "ms": round((t1 - t0) * 1000.0, 3)}  # fmt: skip
        self._timing.write(json.dumps(line, separators=(",", ":")) + "\n")

    def _covered(self, f: _Frame) -> bool:
        """Does the simulated accurate lane have a result that covers this frame?"""
        # results that completed before the hang still cover the frames up to the lane max age after them
        return self.faults.check("lane", "accurate", now=f.arrival - HANG_COVERAGE_S) != "hang"

    def _paint(self, f: _Frame) -> np.ndarray:
        out = f.bgr.copy()
        if f.counter is None:
            return out
        _, targets = self.player.targets(f.counter)
        for t in targets:
            poly = np.array(t.polygon(), dtype=np.float32)
            c = poly.mean(axis=0)
            grown = c + (poly - c) * 1.0
            hull = cv2.convexHull(grown.astype(np.int32))
            x, y, w, h = cv2.boundingRect(hull)
            pad = PAD
            cv2.rectangle(out, (x - pad, y - pad), (x + w + pad, y + h + pad), SOLID, -1)
        return out

    # ------------------------------------------------------------ threads

    def _ingest(self) -> None:
        container = None
        while container is None and not self.stop.is_set():
            try:
                container = av.open(self.in_url, mode="r", timeout=(5.0, 10.0))
            except (av.error.FFmpegError, OSError):
                time.sleep(0.3)
        if container is None:
            return
        vs = container.streams.video[0]
        astream = container.streams.audio[0] if container.streams.audio else None
        streams = [s for s in (vs, astream) if s is not None]
        seq = 0
        try:
            for packet in container.demux(*streams):
                if self.stop.is_set():
                    break
                for fr in packet.decode():
                    now = time.monotonic()
                    pts = float(fr.pts * fr.time_base) if fr.pts is not None else 0.0
                    if isinstance(fr, av.VideoFrame):
                        if self.origin is None:
                            self.origin, self.pts0 = now, pts
                            self.faults.start(now)
                        # a late joiner gets a burst from the last keyframe: keep the earliest (arrival - pts)
                        self.origin = min(self.origin, now - (pts - (self.pts0 or 0.0)))
                        gray = fr.to_ndarray(format="gray")
                        st = read_stamp(gray)
                        f = _Frame(
                            seq,
                            pts - (self.pts0 or 0.0),
                            now,
                            st.counter if st else None,
                            fr.to_ndarray(format="bgr24"),
                        )
                        with self.lock:
                            self.vq.append(f)
                            self.max_seq = seq
                        seq += 1
                    elif self.origin is not None and pts - (self.pts0 or 0.0) >= 0.0:
                        pcm = fr.to_ndarray().astype(np.float32).reshape(-1)
                        with self.lock:
                            self.aq.append(_Chunk(pts - (self.pts0 or 0.0), now, pcm))
        except (av.error.FFmpegError, OSError):
            pass
        finally:
            try:
                container.close()
            except Exception:  # noqa: BLE001
                pass

    def _bus(self) -> None:
        if not self.bus_endpoint:
            return
        import zmq

        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.SUB)
        sock.setsockopt(zmq.SUBSCRIBE, b"control.hold")
        sock.bind(self.bus_endpoint)
        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)
        try:
            while not self.stop.is_set():
                if not poller.poll(100):
                    continue
                frames = sock.recv_multipart()
                try:
                    cmd = loads(frames[-1])
                except ValueError:
                    continue
                if isinstance(cmd, HoldCommand):
                    self._on_hold(cmd)
        finally:
            sock.close(0)

    def _on_hold(self, cmd: HoldCommand) -> None:
        if self.broken:
            return
        now = time.monotonic()
        with self.lock:
            self.holds[cmd.source.value] = cmd.active
            if cmd.active:
                for f in self.vq:  # retroactive hold: everything captured in the last retro seconds
                    if f.arrival >= now - self.retro:
                        f.held = True
                for c in self.aq:
                    if c.arrival >= now - self.retro:
                        c.held = True
                self.fence_seq = self.max_seq

    # ------------------------------------------------------------ emit loop

    def run(self) -> int:
        threads = [
            threading.Thread(target=self._ingest, daemon=True),
            threading.Thread(target=self._bus, daemon=True),
        ]
        for t in threads:
            t.start()
        sink = None
        vout = aout = None
        try:
            while not self.stop.is_set():
                now = time.monotonic()
                emitted = False
                if self.origin is not None:
                    while True:
                        with self.lock:
                            f = self.vq[0] if self.vq else None
                            due = f is not None and self.origin + f.pts + self.delay <= now
                            if due:
                                self.vq.popleft()
                        if not due or f is None:
                            break
                        if sink is None:
                            sink = open_output(self.out_url)
                            vout = sink.container.add_stream("libx264", rate=self.fps)
                            vout.width, vout.height, vout.pix_fmt = CANVAS_W, CANVAS_H, "yuv420p"
                            vout.time_base = Fraction(1, self.fps)
                            vout.options = {"preset": "veryfast", "tune": "zerolatency", "crf": "23", "g": str(self.fps), "bf": "0", "threads": "2"}  # fmt: skip
                            aout = sink.container.add_stream("aac", rate=RATE, layout="mono")
                            aout.bit_rate = 96_000
                            self.t_first_emit = now
                            self.sink_faults.start(now)
                        self._emit_frame(f, sink, vout, now)
                        emitted = True
                    while aout is not None:
                        with self.lock:
                            c = self.aq[0] if self.aq else None
                            due = c is not None and self.origin + c.pts + self.delay <= now
                            if due:
                                self.aq.popleft()
                        if not due or c is None:
                            break
                        self._emit_audio(c, sink, aout)
                if not emitted:
                    time.sleep(0.002)
        finally:
            self.stop.set()
            if sink is not None:
                try:
                    for pkt in vout.encode(None):
                        sink.container.mux(pkt)
                except Exception:  # noqa: BLE001
                    pass
                sink.close()
            if self._timing:
                self._timing.close()
        return 0

    def _state(self, f: _Frame, now: float) -> str:
        with self.lock:
            held = f.held or any(self.holds.values())
            fenced = self.fence_seq is not None and f.seq <= self.fence_seq
            if not held and self.fence_seq is not None and f.seq > self.fence_seq:
                self.fence_seq = None
        if held:
            return "HOLD"
        if fenced:
            return "HOLD"
        if self.t_first_emit is not None and now - self.t_first_emit < self.start_protect_s:
            return "PROTECT"
        if not self.broken and not self._covered(f):
            return "PROTECT"
        return "LIVE"

    def _emit_frame(self, f: _Frame, sink: Any, vout: Any, now: float) -> None:
        state = self._state(f, now)
        self.counts[state] += 1
        if state == "LIVE":
            img = self._live_image(f)
        else:
            img = self.slate
        if self.sink_faults.check("sink", None, now=now) == "stall":
            return  # egress stalled: nothing leaves
        vf = av.VideoFrame.from_ndarray(img, format="bgr24").reformat(format="yuv420p")
        vf.pts, vf.time_base = f.seq, Fraction(1, self.fps)
        for pkt in vout.encode(vf):
            sink.container.mux(pkt)
        t1 = time.monotonic()
        self._log("engine_delay", f.seq, f.pts, f.arrival, t1)
        self._log("emit_lag", f.seq, f.pts, (self.origin or 0) + f.pts + self.delay, max(t1, (self.origin or 0) + f.pts + self.delay))  # fmt: skip

    def _live_image(self, f: _Frame) -> np.ndarray:
        """LIVE output: the frame with the card painted over (unless the fail-safe is off and nothing covers it)."""
        if self.broken and self.faults.check("lane", "accurate", now=f.arrival - HANG_COVERAGE_S) == "hang":
            return f.bgr  # broken build: no coverage, no slate, raw pixels
        return self._paint(f)

    def _emit_audio(self, c: _Chunk, sink: Any, aout: Any) -> None:
        with self.lock:
            silent = c.held or any(self.holds.values())
        pcm = np.zeros_like(c.pcm) if silent else c.pcm
        af = av.AudioFrame.from_ndarray(pcm.reshape(1, -1), format="fltp", layout="mono")
        af.sample_rate = RATE
        af.pts, af.time_base = int(round(c.pts * RATE)), Fraction(1, RATE)
        for pkt in aout.encode(af):
            sink.container.mux(pkt)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--in-url", required=True)
    p.add_argument("--out-url", required=True)
    p.add_argument("--script", required=True)
    p.add_argument("--delay", type=float, default=4.0)
    p.add_argument("--timing-log", type=Path, default=None)
    p.add_argument(
        "--bus", default=None, help="ipc endpoint to bind the hold subscriber on (the bus's -in side)"
    )
    p.add_argument("--broken", action="store_true", help="fail-safe disabled (the gate must fail)")
    args = p.parse_args(argv)
    eng = RefEngine(args.in_url, args.out_url, args.script, args.delay, broken=args.broken,
                    timing_path=args.timing_log, bus_endpoint=args.bus)  # fmt: skip
    signal.signal(signal.SIGTERM, lambda *_: eng.stop.set())
    signal.signal(signal.SIGINT, lambda *_: eng.stop.set())
    return eng.run()


if __name__ == "__main__":
    sys.exit(main())
