"""Engine entry point: load the modules named in ``Config.runtime.modules`` and run the session.

``python -m engine.main --profile dev`` (see ``scripts/run_engine.sh``). New code. The engine never
imports ``relay``, ``vision``, ``audio``, ``guard`` or ``chat``: each ``"module:build"`` path is
resolved with ``importlib`` and called as ``build(config, bus)`` (docs/specs/contracts.md section 7):

* ``relay``  -> ``contracts.RelayEndpoints(source, sink)``
* ``vision`` -> a ``contracts.LaneHost``
* ``audio``  -> a ``contracts.AudioHost``

Every configured module is required: a missing vision host would mean no lane ever covers a frame
(the engine would stay in PROTECT) and a missing audio host would mean speech is never checked.
``--skip NAME`` drops one deliberately (logged as a warning) for development before that module
exists. SIGINT and SIGTERM stop the session cleanly: threads end, the source is closed, open safety
events are flushed, the hosts, the sink and the ZeroMQ broker are closed, and the exit code is 0.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contracts import CONTRACT_VERSION, AudioHost, Config, LaneHost, RelayEndpoints, load_config

from .bus import DEFAULT_XPUB_ENDPOINT, DEFAULT_XSUB_ENDPOINT, BrokerError, LocalBus, ZmqBroker
from .session import Session

log = logging.getLogger("streamsarthi.engine")

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_MODULES = ("relay", "vision", "audio")


class ModuleLoadError(RuntimeError):
    """A module named in ``runtime.modules`` could not be imported, built or does not fit its protocol."""


def load_builder(path: str) -> Callable[..., Any]:
    """Resolve ``"package.module:function"``. Raises ``ModuleLoadError`` with the dotted path only."""
    module_name, sep, attr = path.partition(":")
    if not sep or not module_name or not attr:
        raise ModuleLoadError(f"{path!r} is not a 'module:build' path")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ModuleLoadError(f"cannot import {module_name!r} ({type(exc).__name__})") from exc
    builder = getattr(module, attr, None)
    if not callable(builder):
        raise ModuleLoadError(f"{module_name!r} has no callable {attr!r}")
    return builder  # type: ignore[no-any-return]


@dataclass(slots=True)
class Assembly:
    config: Config
    bus: LocalBus
    session: Session
    endpoints: RelayEndpoints
    lane_host: LaneHost | None
    audio_host: AudioHost | None
    broker: ZmqBroker | None
    closers: list[Callable[[], None]] = field(default_factory=list)

    def start(self) -> None:
        if self.lane_host is not None:
            self.lane_host.start()
        if self.audio_host is not None:
            self.audio_host.start()
        self.session.start()

    def shutdown(self) -> None:
        """Stop in dependency order; every step is attempted even if an earlier one fails."""
        steps: list[Callable[[], None]] = [self.session.stop]
        if self.audio_host is not None:
            steps.append(self.audio_host.close)
        if self.lane_host is not None:
            steps.append(self.lane_host.close)
        steps += [self.endpoints.sink.close, self.endpoints.source.close]
        if self.broker is not None:
            steps.append(self.broker.stop)
        steps += self.closers
        for step in steps:
            try:
                step()
            except Exception as exc:
                log.warning("shutdown step failed: %s", type(exc).__name__)


def _build(config: Config, bus: LocalBus, name: str) -> Any:
    path = config.runtime.modules.get(name)
    if not path:
        raise ModuleLoadError(f"runtime.modules has no entry for {name!r}")
    try:
        return load_builder(path)(config, bus)
    except ModuleLoadError:
        raise
    except Exception as exc:
        raise ModuleLoadError(f"{name}: build failed ({type(exc).__name__})") from exc


def assemble(
    config: Config,
    *,
    skip: Sequence[str] = (),
    broker: bool = True,
    xpub_endpoint: str = DEFAULT_XPUB_ENDPOINT,
    xsub_endpoint: str = DEFAULT_XSUB_ENDPOINT,
) -> Assembly:
    """Build every module and wire source -> engine -> sink. Raises ``ModuleLoadError``/``BrokerError``."""
    unknown = set(skip) - set(REQUIRED_MODULES)
    if unknown:
        raise ModuleLoadError(f"cannot skip unknown modules: {sorted(unknown)}")
    for name in skip:
        log.warning("module %r skipped by request: %s", name, _SKIP_WARNING[name])
    bus = LocalBus()
    zmq_broker: ZmqBroker | None = None
    built: list[Any] = []
    try:
        if broker:
            zmq_broker = ZmqBroker(bus, xpub_endpoint=xpub_endpoint, xsub_endpoint=xsub_endpoint)
            zmq_broker.start()
        lane_host = None if "vision" in skip else _build(config, bus, "vision")
        if lane_host is not None:
            built.append(lane_host)
            if not isinstance(lane_host, LaneHost):
                raise ModuleLoadError("vision: build() did not return a LaneHost")
        audio_host = None if "audio" in skip else _build(config, bus, "audio")
        if audio_host is not None:
            built.append(audio_host)
            if not isinstance(audio_host, AudioHost):
                raise ModuleLoadError("audio: build() did not return an AudioHost")
        endpoints = _build(config, bus, "relay")  # last: it opens network listeners
        if not isinstance(endpoints, RelayEndpoints):
            raise ModuleLoadError("relay: build() did not return RelayEndpoints")
        session = Session(
            config, endpoints.source, endpoints.sink, lane_host=lane_host, audio_host=audio_host, bus=bus
        )
    except BaseException:
        for obj in reversed(built):
            try:
                obj.close()
            except Exception as exc:
                log.warning("cleanup failed: %s", type(exc).__name__)
        if zmq_broker is not None:
            zmq_broker.stop()
        raise
    return Assembly(config, bus, session, endpoints, lane_host, audio_host, zmq_broker)


_SKIP_WARNING = {
    "vision": "no detector will ever cover a frame, so every frame is PROTECT",
    "audio": "spoken PII is NOT checked or muted",
    "relay": "there is no ingest or egress",
}


def run(assembly: Assembly, stop: threading.Event, *, poll_s: float = 0.5) -> int:
    """Start, wait for ``stop`` (or the session finishing), shut down. Returns the exit code."""
    try:
        assembly.start()
        log.info("engine ready (contract %s, delay %.1f s)", CONTRACT_VERSION, assembly.config.delay_seconds)
        while not stop.is_set() and not assembly.session.finished.is_set():
            stop.wait(poll_s)
    finally:
        log.info("engine stopping")
        assembly.shutdown()
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m engine.main", description="Stream Sarthi engine")
    parser.add_argument(
        "--profile", choices=("dev", "demo"), default=None, help="config profile (default dev)"
    )
    parser.add_argument("--skip", action="append", default=[], choices=REQUIRED_MODULES, metavar="MODULE")
    parser.add_argument(
        "--module", action="append", default=[], metavar="NAME=module:build", help="override a module path"
    )
    parser.add_argument("--no-broker", action="store_true", help="do not start the ZeroMQ bridge")
    parser.add_argument("--xpub", default=DEFAULT_XPUB_ENDPOINT, help="subscribers connect here")
    parser.add_argument("--xsub", default=DEFAULT_XSUB_ENDPOINT, help="publishers connect here")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def _limit_opencv_threads(config: Config | None = None) -> int | None:
    """Cap OpenCV's worker pool for the whole process (D-39): ``engine.cv_threads`` (default 2).

    OpenCV's default pool (one thread per core) spins while idle: on the dev laptop it turned a 0.7
    core compositor and session into 1.9 cores (T1.1 measurements). ``STREAMSARTHI_CV_THREADS``
    overrides the config; ``engine.cv_threads: null`` leaves OpenCV's default. Returns the cap applied.
    """
    value = os.environ.get("STREAMSARTHI_CV_THREADS", "").strip()
    count: int | None = int(value) if value.isdigit() and int(value) >= 1 else None
    if count is None and config is not None:
        count = config.engine.cv_threads
    if count is not None:
        import cv2

        cv2.setNumThreads(count)
        log.info("OpenCV threads limited to %s", count)
    return count


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config(args.profile, config_dir=ROOT / "config" / "profiles", env_file=ROOT / ".env")
        overrides = dict(item.partition("=")[::2] for item in args.module)
        if overrides:
            config = config.model_copy(
                update={
                    "runtime": config.runtime.model_copy(
                        update={"modules": {**config.runtime.modules, **overrides}}
                    )
                }
            )
    except (ValueError, FileNotFoundError) as exc:
        log.error("configuration error: %s", type(exc).__name__)
        return 2
    _limit_opencv_threads(config)  # before any module (vision, relay) starts using OpenCV
    warning = config.delay_warning()
    if warning:
        log.warning(warning)
    try:
        assembly = assemble(
            config,
            skip=args.skip,
            broker=not args.no_broker,
            xpub_endpoint=args.xpub,
            xsub_endpoint=args.xsub,
        )
    except (ModuleLoadError, BrokerError) as exc:
        log.error("cannot start: %s", exc)
        return 2
    stop = threading.Event()

    def on_signal(signum: int, _frame: Any) -> None:
        log.info("signal %s received", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    return run(assembly, stop)


if __name__ == "__main__":
    sys.exit(main())
