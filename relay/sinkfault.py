"""Dormant ``SS_FAULT`` hook for the egress sink (protocol: ``eval/fault.py``; fail-safe gate tests only).

``sink:stall@t=..,for=..``  from ``t`` the sink stops draining (frames are discarded, nothing reaches the
                            tap or any destination) and ``healthy`` is False; with ``for=`` it resumes.
``sink:crash@t=..``         at ``t`` the encoder worker is killed once (the encoder supervisor restarts it).

``t = 0`` is the first ``write``. Each activation (and clearing of a ``for=`` window) appends ONE JSON line to
``SS_FAULT_LOG`` in the same format as ``eval.fault.FaultInjector``. This module does not import ``eval``.
``from_env`` returns ``None`` when ``SS_FAULT`` is unset or names no sink fault, so production pays one
``is not None`` test per call.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

ENV_FAULT = "SS_FAULT"
ENV_FAULT_LOG = "SS_FAULT_LOG"
_TIME = re.compile(r"^(\d+(?:\.\d+)?)(ms|s)?$")

log = logging.getLogger("relay.sinkfault")


@dataclass(frozen=True)
class SinkFaultSpec:
    mode: str  # stall | crash
    at_s: float
    for_s: float | None

    def canonical(self) -> str:
        tail = f"t={self.at_s:g}s" + (f",for={self.for_s:g}s" if self.for_s is not None else "")
        return f"sink:{self.mode}@{tail}"


def _seconds(text: str) -> float:
    match = _TIME.match(text.strip())
    if not match:
        raise ValueError("bad time")
    value = float(match.group(1))
    return value / 1000.0 if match.group(2) == "ms" else value


def parse_sink_faults(raw: str) -> list[SinkFaultSpec]:
    """Only ``sink:`` specs; others (lane faults) belong to vision and are skipped. A bad sink spec is ignored."""
    specs: list[SinkFaultSpec] = []
    for part in raw.split(";"):
        head, _, params = part.strip().partition("@")
        pieces = head.split(":")
        if len(pieces) != 2 or pieces[0] != "sink" or pieces[1] not in ("stall", "crash") or not params:
            continue
        try:
            values = dict(item.partition("=")[::2] for item in params.split(","))
            if "t" not in values:
                continue
            duration = _seconds(values["for"]) if "for" in values else None
            if duration is not None and duration <= 0:
                continue
            specs.append(SinkFaultSpec(pieces[1], _seconds(values["t"]), duration))
        except ValueError:
            continue
    return specs


class SinkFault:
    def __init__(
        self,
        specs: list[SinkFaultSpec],
        log_path: str | None,
        kill_encoder: Callable[[], None],
        clock: Callable[[], float] = time.monotonic,
    ):
        self._specs, self._log_path, self._kill, self._clock = specs, log_path, kill_encoder, clock
        self._t0: float | None = None
        self._announced: set[tuple[str, str]] = set()
        self._crashed: set[str] = set()
        self._lock = threading.Lock()

    @staticmethod
    def from_env(kill_encoder: Callable[[], None], env: Mapping[str, str] | None = None) -> SinkFault | None:
        source = os.environ if env is None else env
        raw = source.get(ENV_FAULT)
        specs = parse_sink_faults(raw) if raw else []
        return SinkFault(specs, source.get(ENV_FAULT_LOG) or None, kill_encoder) if specs else None

    def start(self) -> None:
        """Call on every write: the first one is ``t = 0``."""
        if self._t0 is None:
            self._t0 = self._clock()

    def stalled(self) -> bool:
        """True while a stall window is open; also fires the one-shot crash when its time has come."""
        if self._t0 is None:
            return False
        t = self._clock() - self._t0
        stalled = False
        with self._lock:
            for spec in self._specs:
                if t < spec.at_s:
                    continue
                if spec.mode == "crash":
                    if spec.canonical() not in self._crashed:
                        self._crashed.add(spec.canonical())
                        self._announce("fault_active", spec, spec.at_s)
                        try:
                            self._kill()
                        except Exception:  # noqa: BLE001
                            log.warning("sink fault: encoder kill failed")
                elif spec.for_s is None or t < spec.at_s + spec.for_s:
                    self._announce("fault_active", spec, spec.at_s)
                    stalled = True
                else:
                    self._announce("fault_cleared", spec, spec.at_s + spec.for_s)
        return stalled

    def _announce(self, event: str, spec: SinkFaultSpec, at: float) -> None:
        key = (event, spec.canonical())
        if key in self._announced:
            return
        self._announced.add(key)
        if not self._log_path:
            return
        line = json.dumps(
            {
                "event": event,
                "spec": spec.canonical(),
                "t_monotonic": round(at + (self._t0 or 0.0), 6),
                "t_observed": round(self._clock(), 6),
                "proc": "relay",
            },
            separators=(",", ":"),
        )
        try:
            with open(self._log_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
        except OSError:
            pass
