"""Read and write ``runs/<run_id>/timing.jsonl`` (docs/specs/eval.md 5.4).

Line types: ``header`` (first line), ``timing`` (one per StageTiming) and ``event``. Times are
``time.monotonic()`` seconds. A line never carries images, audio or text; the reader rejects any key
outside the schema so a stray payload cannot sneak into a run log.
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

import numpy as np

STAGES = (
    "ingest_decode",
    "lane_fast",
    "lane_accurate",
    "motion",
    "plan",
    "compose",
    "encode",
    "egress",
    "vad",
    "stt",
    "pii",
    "mute_apply",
    "chat_classify",
    "caption_translate",
    "music",
    "api_hold",
    # stages the engine itself logs (engine/session.py): sink_write is the write into the sink,
    # engine_delay runs from frame arrival to the end of its emission, emit_lag from deadline to emission
    "sink_write",
    "engine_delay",
    "emit_lag",
    # stages the relay publishes (docs/specs/relay.md): egress_write ends when the frame is written to the
    # egress socket, the end point of the D-1 delay
    "encode_submit",
    "egress_write",
)
_TIMING_KEYS = {"type", "stage", "seq", "pts", "t_start", "t_end", "proc"}
_EVENT_KEYS = {"type", "name", "t", "detail"}
_HEADER_KEYS = {"type", "run_id", "profile", "git_rev", "t0_wall", "hardware", "seed"}


class TimingFormatError(ValueError):
    pass


class TimingWriter:
    """Thread-safe JSONL writer. Use as a context manager."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f: TextIO = self.path.open("w", encoding="utf-8")
        self._lock = threading.Lock()

    def _write(self, obj: dict[str, Any]) -> None:
        with self._lock:
            self._f.write(json.dumps(obj, separators=(",", ":"), sort_keys=True) + "\n")

    def header(
        self,
        run_id: str,
        profile: str,
        git_rev: str,
        t0_wall: str,
        hardware: dict[str, Any] | None = None,
        seed: int | None = None,
    ) -> None:
        self._write(
            {
                "type": "header",
                "run_id": run_id,
                "profile": profile,
                "git_rev": git_rev,
                "t0_wall": t0_wall,
                "hardware": hardware or {},
                "seed": seed,
            }
        )

    def timing(
        self, stage: str, seq: int, pts: float | None, t_start: float, t_end: float, proc: str
    ) -> None:
        if stage not in STAGES:
            raise TimingFormatError(f"unknown stage {stage!r}")
        if t_end < t_start:
            raise TimingFormatError("t_end < t_start")
        self._write(
            {
                "type": "timing",
                "stage": stage,
                "seq": seq,
                "pts": pts,
                "t_start": t_start,
                "t_end": t_end,
                "proc": proc,
            }
        )

    def stage_timing(self, st: Any, proc: str) -> None:
        """Write a ``contracts.StageTiming`` (duck-typed so this module imports without contracts)."""
        self.timing(st.stage, st.seq, st.pts, st.t_start, st.t_end, proc)

    def event(self, name: str, t: float, detail: str | None = None) -> None:
        obj: dict[str, Any] = {"type": "event", "name": name, "t": t}
        if detail is not None:
            obj["detail"] = detail
        self._write(obj)

    def close(self) -> None:
        with self._lock:
            if not self._f.closed:
                self._f.close()

    def __enter__(self) -> TimingWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@dataclass
class TimingLog:
    header: dict[str, Any] = field(default_factory=dict)
    timings: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)


def read_timing(path: Path) -> TimingLog:
    log = TimingLog()
    with Path(path).open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TimingFormatError(f"line {n}: not JSON") from exc
            kind = obj.get("type")
            allowed = {"header": _HEADER_KEYS, "timing": _TIMING_KEYS, "event": _EVENT_KEYS}.get(kind)
            if allowed is None:
                raise TimingFormatError(f"line {n}: unknown type {kind!r}")
            extra = set(obj) - allowed
            if extra:
                raise TimingFormatError(f"line {n}: unexpected keys {sorted(extra)}")
            if kind == "header":
                log.header = obj
            elif kind == "timing":
                if obj["stage"] not in STAGES:
                    raise TimingFormatError(f"line {n}: unknown stage {obj['stage']!r}")
                log.timings.append(obj)
            else:
                log.events.append(obj)
    return log


def _stats_ms(values_s: list[float]) -> dict[str, float | int]:
    a = np.asarray(values_s, dtype=np.float64) * 1000.0
    return {
        "n": int(a.size),
        "p50_ms": round(float(np.percentile(a, 50)), 3),
        "p95_ms": round(float(np.percentile(a, 95)), 3),
        "max_ms": round(float(a.max()), 3),
    }


def stage_latencies(log: TimingLog) -> dict[str, dict[str, float | int]]:
    """Per-stage duration (t_end - t_start) p50/p95/max in milliseconds."""
    by: dict[str, list[float]] = defaultdict(list)
    for t in log.timings:
        by[t["stage"]].append(t["t_end"] - t["t_start"])
    return {stage: _stats_ms(v) for stage, v in sorted(by.items()) if v}


def end_to_end_delays(
    log: TimingLog, first_stage: str = "ingest_decode", last_stage: str = "egress"
) -> list[float]:
    """Per-seq delay in seconds from the start of `first_stage` to the end of `last_stage`."""
    starts: dict[int, float] = {}
    ends: dict[int, float] = {}
    for t in log.timings:
        if t["stage"] == first_stage:
            starts[t["seq"]] = t["t_start"]
        elif t["stage"] == last_stage:
            ends[t["seq"]] = t["t_end"]
    return [ends[s] - starts[s] for s in sorted(starts) if s in ends]
