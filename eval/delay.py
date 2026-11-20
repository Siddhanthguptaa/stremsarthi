"""Delay at the egress tap (decision D-1) and the maths around it.

The tap is a reader on MediaMTX's ``shielded`` path, i.e. the encoded stream the engine writes to its egress.
For every output frame whose optical stamp decodes, delay = ``t_read - t_capture`` on the shared monotonic
clock (sender, engine and tap run on one host). That is capture to the frame arriving at the tap; the egress
socket write is the end of the engine's own stage and the tap adds about one RTMP hop, reported separately
as the calibration (``eval.run loopback`` measures it with the engine absent).

Outputs: a ``StageTiming``-compatible JSONL (``stage="egress"``, ``t_start`` = capture time, ``t_end`` = tap
read time, ``proc="eval-tap"``, plus the usual header) and percentile / fps dictionaries for the report.
The engine's own timing log (``engine.timing_log_path``: ``{"stage","seq","pts","t_start","t_end","ms"}``) is read
leniently and aligned to the tap frames through the frame PTS.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .harness import metrics
from .harness.timing import STAGES, TimingLog, TimingWriter


@dataclass
class TapFrame:
    """One frame read at the tap. ``kind``: live (stamp readable), slate (unreadable stamp, slate-like) or
    unreadable (unreadable stamp and not a slate: a real problem)."""

    counter: int | None
    t_read: float  # monotonic seconds
    kind: str
    delay_s: float | None = None
    pts_s: float | None = None
    scene: str | None = None
    card_visible: bool = False
    leaked: bool | None = None  # None: no card in view (or not scored)
    similarity: float | None = None
    ocr: dict[str, Any] | None = None  # filled by the OCR worker for sampled frames
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def capture_s(self) -> float | None:
        return None if self.delay_s is None else self.t_read - self.delay_s


# ---------------------------------------------------------------- delay and fps


def live_frames(frames: Iterable[TapFrame]) -> list[TapFrame]:
    return [f for f in frames if f.kind == "live" and f.delay_s is not None]


def delay_stats(frames: Sequence[TapFrame], warmup_s: float = 1.0) -> dict[str, Any]:
    """p50/p95/max of capture-to-tap delay over live frames read at least `warmup_s` after the first one."""
    live = live_frames(frames)
    if not live:
        return {"n": 0, "p50_s": None, "p95_s": None, "max_s": None, "warmup_s": warmup_s}
    first = min(f.t_read for f in live)
    kept = [f.delay_s for f in live if f.t_read - first >= warmup_s and f.delay_s is not None]
    out: dict[str, Any] = dict(metrics.delay_percentiles(kept))
    out["warmup_s"] = warmup_s
    return out


def fps_stats(frames: Sequence[TapFrame]) -> dict[str, Any]:
    """Decoded frames per second at the tap between the first frame and the last stamp-bearing one.

    Frames after the last readable stamp are the end-of-stream slates (the sender stopped), not throughput.
    """
    if len(frames) < 2:
        return {"fps": None, "frames": len(frames), "window_s": 0.0}
    stamped = [f for f in frames if f.kind == "live"]
    if len(stamped) < 2:
        return {"fps": None, "frames": len(frames), "window_s": 0.0}
    t0, t1 = frames[0].t_read, stamped[-1].t_read
    kept = [f for f in frames if f.t_read <= t1]
    window = t1 - t0
    return {
        "fps": round((len(kept) - 1) / window, 3) if window > 0 else None,
        "frames": len(kept),
        "window_s": round(window, 3),
    }


def gap_stats(frames: Sequence[TapFrame], over_s: float = 0.25) -> dict[str, Any]:
    """Largest gap between consecutive tap reads, and how many exceed `over_s` (a stall)."""
    ts = [f.t_read for f in frames]
    if len(ts) < 2:
        return {"max_gap_s": 0.0, "n_over": 0}
    gaps = np.diff(np.asarray(ts))
    return {"max_gap_s": round(float(gaps.max()), 4), "n_over": int((gaps > over_s).sum())}


# ---------------------------------------------------------------- timing JSONL


def write_tap_timing(
    path: Path,
    frames: Sequence[TapFrame],
    run_id: str,
    profile: str,
    git_rev: str,
    t0_wall: str,
    hardware: dict[str, Any] | None = None,
    seed: int | None = None,
    events: Sequence[tuple[str, float, str | None]] = (),
) -> int:
    """Write the tap frames as a StageTiming-compatible JSONL. Returns the number of timing lines."""
    n = 0
    with TimingWriter(path) as w:
        w.header(run_id, profile, git_rev, t0_wall, hardware, seed)
        for f in frames:
            if f.kind != "live" or f.counter is None or f.capture_s is None:
                continue
            w.timing("egress", f.counter, f.pts_s, f.capture_s, f.t_read, "eval-tap")
            n += 1
        for name, t, detail in events:
            w.event(name, t, detail)
    return n


# ---------------------------------------------------------------- the engine's own timing log


def read_engine_timing(path: Path) -> TimingLog:
    """Read ``engine.timing_log_path`` (no ``type`` key, a derived ``ms`` key). Unknown stages are skipped,
    a half-written last line is ignored."""
    log = TimingLog()
    p = Path(path)
    if not p.exists():
        return log
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "header":
            log.header = obj
            continue
        if obj.get("type") == "event":
            log.events.append(obj)
            continue
        stage = obj.get("stage")
        if stage not in STAGES or "seq" not in obj or "t_start" not in obj or "t_end" not in obj:
            continue
        log.timings.append(
            {
                "type": "timing",
                "stage": stage,
                "seq": int(obj["seq"]),
                "pts": obj.get("pts"),
                "t_start": float(obj["t_start"]),
                "t_end": float(obj["t_end"]),
                "proc": obj.get("proc", "engine"),
            }
        )
    return log


@dataclass(frozen=True)
class EngineFrame:
    seq: int
    arrival: float  # engine decode time (monotonic)
    emitted: float  # end of the engine's emission of the frame (monotonic)


def engine_frames(log: TimingLog) -> dict[int, EngineFrame]:
    """Per engine seq: arrival and emission, from the ``engine_delay`` stage (arrival to end of emission)."""
    return {
        t["seq"]: EngineFrame(t["seq"], t["t_start"], t["t_end"])
        for t in log.timings
        if t["stage"] == "engine_delay"
    }


def encoder_pts_map(log: TimingLog) -> dict[int, float]:
    """Encoder sequence number -> engine frame PTS, from the relay's ``egress_write`` lines.

    The relay encoder numbers the frames it writes from 0 (that is the tap's stream PTS x fps) and logs the
    engine PTS of each one, so this is the bridge from a tap frame to the engine's own ``seq``."""
    return {
        t["seq"]: float(t["pts"])
        for t in log.timings
        if t["stage"] == "egress_write" and t.get("pts") is not None
    }


def align_to_engine(
    frames: Sequence[TapFrame],
    engine: dict[int, EngineFrame],
    fps: float = 30.0,
    enc_map: dict[int, float] | None = None,
) -> dict[str, Any]:
    """Attach ``extra['engine_arrival'/'engine_emitted']`` to tap frames.

    With ``enc_map`` (real relay) the tap PTS gives the encoder seq, which maps to the engine PTS and so to the
    engine seq. Without it (the reference engine) the tap PTS is the engine PTS: ``seq = round(pts * fps)``.
    The mapping is checked: for live frames ``arrival - capture`` is the ingest latency and must be small and
    steady. Returns ``{"matched", "ingest_s_median", "ingest_s_std", "valid"}``.
    """
    ingest: list[float] = []
    matched = 0
    for f in frames:
        if f.pts_s is None:
            continue
        key = round(f.pts_s * fps)
        if enc_map:
            pts_e = enc_map.get(key)
            if pts_e is None:
                continue
            key = round(pts_e * fps)
        ef = engine.get(key)
        if ef is None:
            continue
        matched += 1
        f.extra["engine_arrival"] = ef.arrival
        f.extra["engine_emitted"] = ef.emitted
        if f.kind == "live" and f.capture_s is not None:
            ingest.append(ef.arrival - f.capture_s)
    if not ingest:
        return {"matched": matched, "ingest_s_median": None, "ingest_s_std": None, "valid": False}
    arr = np.asarray(ingest)
    med, std = float(np.median(arr)), float(arr.std())
    valid = matched >= 30 and -0.05 <= med <= 1.5 and std <= 0.10
    return {
        "matched": matched,
        "ingest_s_median": round(med, 4),
        "ingest_s_std": round(std, 4),
        "valid": valid,
    }


def egress_delays(
    frames: Sequence[TapFrame], log: TimingLog, fps: float = 30.0, warmup_s: float = 1.0
) -> list[float]:
    """Capture to the frame being written to the egress socket (D-1), from the relay's ``egress_write``
    timing (``t_end`` per engine seq) and the tap frame's capture time. Empty when the stage is not logged."""
    ends = {t["seq"]: t["t_end"] for t in log.timings if t["stage"] == "egress_write"}
    if not ends:
        return []
    live = live_frames(frames)
    if not live:
        return []
    first = min(f.t_read for f in live)
    out: list[float] = []
    for f in live:
        if f.pts_s is None or f.capture_s is None or f.t_read - first < warmup_s:
            continue
        end = ends.get(round(f.pts_s * fps))
        if end is not None:
            out.append(end - f.capture_s)
    return out
