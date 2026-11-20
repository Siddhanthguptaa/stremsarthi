"""Fault-injection protocol for the fail-safe tests (docs/specs/eval.md 5.9 gate 1 step 3).

Standard library only, so the engine, vision and relay owners can import it (or copy it) without pulling in
the rest of ``eval``. Nothing here touches any other module: a component that wants to be testable reads
``SS_FAULT`` itself and honours the hooks documented below.

Environment protocol
--------------------
``SS_FAULT``      one or more specs separated by ``;``. Each spec is::

                      lane:<fast|accurate|slow>:<hang|crash>@t=<time>[,for=<time>]
                      sink:<stall|crash>@t=<time>[,for=<time>]

                  ``<time>`` is ``20``, ``20s``, ``1.5s`` or ``500ms`` (seconds by default). Examples::

                      SS_FAULT=lane:accurate:hang@t=20s
                      SS_FAULT=lane:fast:crash@t=15s,for=5s;sink:stall@t=40s,for=3s

``SS_FAULT_LOG``  optional path of a JSON-lines file. When a spec becomes active (and again when a spec with
                  ``for=`` ends) the component appends ONE line, flushed at once::

                      {"event":"fault_active","spec":"lane:accurate:hang@t=20s","t_monotonic":123.456,"proc":"vision"}
                      {"event":"fault_cleared", ...}

                  ``t_monotonic`` is ``time.monotonic()`` (CLOCK_MONOTONIC, shared by every process on the host).
                  The eval harness measures the fail-safe against this record, so a component that does not
                  write it is reported as "hooks missing" (the test xfails or the gate exits 2).

Clock: ``t=0`` is the ``Frame.arrival`` (``time.monotonic()`` at decode) of the first frame the component
sees (engine: first decoded frame; vision: first frame handed to ``LaneHost.submit``; relay sink: first
``write``). The three clocks differ by milliseconds, which the tests tolerate.

Hooks by owner
--------------
* vision (``vision/lanes.py`` worker thread, per lane):
    ``lane:<name>:hang``  from ``t`` the worker blocks forever inside the run, before calling the detector.
                          No ``LaneResult`` is produced, none with ``ok=False`` either (a hung model). The
                          thread must stay a daemon so ``close()`` still returns.
    ``lane:<name>:crash`` from ``t`` every run of that lane raises ``RuntimeError("SS_FAULT lane crash")``
                          inside the worker, so each run yields ``LaneResult(ok=False)``.
* relay (the ``FrameSink``/egress, T1.2):
    ``sink:stall``        from ``t`` the egress writer stops draining (no bytes reach the tap or any
                          destination) and ``FrameSink.healthy`` turns False; with ``for=`` it resumes.
    ``sink:crash``        at ``t`` the encoder process is killed once (the supervisor restarts it).
* engine (``engine/main.py`` or ``session.py``): none of its own, but it must keep running and must fail
  closed (PROTECT or HOLD) when the above happen. The engine owner may also host the shared parsing by
  importing this file.

Reference implementation: ``FaultInjector`` below. ``FaultInjector.from_env(proc="vision")`` returns a no-op
object when ``SS_FAULT`` is unset, so the hooks cost nothing in production.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ENV_FAULT = "SS_FAULT"
ENV_FAULT_LOG = "SS_FAULT_LOG"

LANES = ("fast", "accurate", "slow")
LANE_MODES = ("hang", "crash")
SINK_MODES = ("stall", "crash")

_TIME_RE = re.compile(r"^(\d+(?:\.\d+)?)(ms|s)?$")


class FaultSpecError(ValueError):
    """A ``SS_FAULT`` value that does not follow the protocol."""


def parse_time(text: str) -> float:
    """``'20s'``, ``'20'``, ``'1.5s'``, ``'500ms'`` -> seconds."""
    m = _TIME_RE.match(text.strip())
    if not m:
        raise FaultSpecError(f"bad time {text!r} (use 20, 20s, 1.5s or 500ms)")
    value = float(m.group(1))
    return value / 1000.0 if m.group(2) == "ms" else value


@dataclass(frozen=True)
class FaultSpec:
    kind: str  # "lane" | "sink"
    mode: str  # lane: hang|crash; sink: stall|crash
    lane: str | None  # fast|accurate|slow for kind == "lane"
    at_s: float
    for_s: float | None = None  # None: until the end of the run
    raw: str = ""

    def active(self, t: float) -> bool:
        """True when ``t`` (seconds on the fault clock) is inside the fault window."""
        if t < self.at_s:
            return False
        return self.for_s is None or t < self.at_s + self.for_s

    @property
    def end_s(self) -> float | None:
        return None if self.for_s is None else self.at_s + self.for_s

    def canonical(self) -> str:
        head = f"lane:{self.lane}:{self.mode}" if self.kind == "lane" else f"sink:{self.mode}"
        tail = f"t={self.at_s:g}s" + (f",for={self.for_s:g}s" if self.for_s is not None else "")
        return f"{head}@{tail}"


def parse_spec(text: str) -> FaultSpec:
    raw = text.strip()
    head, sep, params = raw.partition("@")
    if not sep or not params:
        raise FaultSpecError(f"{raw!r}: missing '@t=<time>'")
    parts = head.split(":")
    kind = parts[0]
    lane: str | None = None
    if kind == "lane":
        if len(parts) != 3:
            raise FaultSpecError(f"{raw!r}: expected lane:<{'|'.join(LANES)}>:<{'|'.join(LANE_MODES)}>")
        lane, mode = parts[1], parts[2]
        if lane not in LANES:
            raise FaultSpecError(f"{raw!r}: unknown lane {lane!r}")
        if mode not in LANE_MODES:
            raise FaultSpecError(f"{raw!r}: unknown lane mode {mode!r}")
    elif kind == "sink":
        if len(parts) != 2:
            raise FaultSpecError(f"{raw!r}: expected sink:<{'|'.join(SINK_MODES)}>")
        mode = parts[1]
        if mode not in SINK_MODES:
            raise FaultSpecError(f"{raw!r}: unknown sink mode {mode!r}")
    else:
        raise FaultSpecError(f"{raw!r}: unknown target {kind!r} (lane or sink)")
    at: float | None = None
    dur: float | None = None
    for item in params.split(","):
        key, eq, val = item.partition("=")
        key = key.strip()
        if not eq:
            raise FaultSpecError(f"{raw!r}: parameter {item!r} is not key=value")
        if key == "t":
            at = parse_time(val)
        elif key == "for":
            dur = parse_time(val)
            if dur <= 0:
                raise FaultSpecError(f"{raw!r}: for= must be positive")
        else:
            raise FaultSpecError(f"{raw!r}: unknown parameter {key!r}")
    if at is None:
        raise FaultSpecError(f"{raw!r}: t= is required")
    return FaultSpec(kind, mode, lane, at, dur, raw)


def parse_faults(value: str | None) -> list[FaultSpec]:
    """Parse a full ``SS_FAULT`` value (empty or None: no faults)."""
    if not value or not value.strip():
        return []
    return [parse_spec(part) for part in value.split(";") if part.strip()]


def faults_from_env(env: Mapping[str, str] | None = None) -> list[FaultSpec]:
    return parse_faults((os.environ if env is None else env).get(ENV_FAULT))


def format_env(specs: list[FaultSpec] | list[str]) -> str:
    """Join specs (objects or strings) into an ``SS_FAULT`` value."""
    return ";".join(s if isinstance(s, str) else s.canonical() for s in specs)


class FaultInjector:
    """Reference hook. ``check`` is cheap and thread-safe; it logs each activation and clearing once."""

    def __init__(
        self,
        specs: list[FaultSpec],
        proc: str,
        log_path: str | None = None,
        clock=time.monotonic,
    ) -> None:
        self.specs = list(specs)
        self.proc = proc
        self.log_path = log_path
        self._clock = clock
        self._t0: float | None = None
        self._lock = threading.Lock()
        self._announced: set[tuple[str, str]] = set()

    @classmethod
    def from_env(cls, proc: str, env: Mapping[str, str] | None = None) -> FaultInjector:
        e = os.environ if env is None else env
        return cls(parse_faults(e.get(ENV_FAULT)), proc, e.get(ENV_FAULT_LOG) or None)

    @property
    def enabled(self) -> bool:
        return bool(self.specs)

    def start(self, first_frame_arrival: float | None = None) -> None:
        """Set ``t = 0``. Call with the first frame's ``arrival``; later calls are ignored."""
        with self._lock:
            if self._t0 is None:
                self._t0 = self._clock() if first_frame_arrival is None else first_frame_arrival

    def elapsed(self, now: float | None = None) -> float | None:
        if self._t0 is None:
            return None
        return (self._clock() if now is None else now) - self._t0

    def check(self, kind: str, lane: str | None = None, now: float | None = None) -> str | None:
        """Mode of the fault active now for ``kind`` (``lane`` or ``sink``) and ``lane``, else None."""
        if not self.specs:
            return None
        t = self.elapsed(now)
        if t is None:
            return None
        found: str | None = None
        for s in self.specs:
            if s.kind != kind or (kind == "lane" and s.lane != lane):
                continue
            if s.active(t):
                self._announce("fault_active", s)
                found = found or s.mode
            elif s.for_s is not None and t >= s.at_s + s.for_s:
                self._announce("fault_cleared", s)
        return found

    def _announce(self, event: str, spec: FaultSpec) -> None:
        key = (event, spec.canonical())
        with self._lock:
            if key in self._announced:
                return
            self._announced.add(key)
        if not self.log_path:
            return
        # the instant the fault is defined to start (or end) on the shared clock, not when a hook noticed it
        edge = (spec.at_s if event == "fault_active" else (spec.end_s or spec.at_s)) + (self._t0 or 0.0)
        line = json.dumps(
            {
                "event": event,
                "spec": spec.canonical(),
                "t_monotonic": round(edge, 6),
                "t_observed": round(self._clock(), 6),
                "proc": self.proc,
            },
            separators=(",", ":"),
        )
        try:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
        except OSError:
            pass


def read_fault_log(path: str | Path) -> list[dict]:
    """Activation records written by the hooked components (missing file: no records)."""
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("event") in ("fault_active", "fault_cleared"):
            out.append(obj)
    return out


def activation_time(records: list[dict], spec: FaultSpec | str) -> float | None:
    """Earliest ``fault_active`` ``t_monotonic`` for ``spec`` (any process), or None."""
    want = spec if isinstance(spec, str) else spec.canonical()
    times = [r["t_monotonic"] for r in records if r.get("event") == "fault_active" and r.get("spec") == want]
    return min(times) if times else None


def hooks_present(root: str | Path, dirs: tuple[str, ...] = ("engine", "vision", "relay")) -> dict[str, bool]:
    """Static check: does any ``.py`` file (tests excluded) in each module directory read ``SS_FAULT``?"""
    base = Path(root)
    found: dict[str, bool] = {}
    for d in dirs:
        hit = False
        for p in (base / d).rglob("*.py") if (base / d).is_dir() else ():
            if "tests" in p.parts:
                continue
            try:
                if ENV_FAULT in p.read_text(encoding="utf-8", errors="ignore"):
                    hit = True
                    break
            except OSError:
                continue
        found[d] = hit
    return found
