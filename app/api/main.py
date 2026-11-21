"""App factory and uvicorn entry: ``python -m app.api.main [--lan]``."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import secrets
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from contracts import Config, load_config

from . import API_VERSION
from .allowlist import AllowlistStore
from .auth import TokenService
from .bus import BusClient, make_bus
from .chat_state import ChatState
from .config_store import ConfigStore
from .enrol_service import MAX_ENROL_FRAMES, AllowlistService
from .errors import install_error_handlers
from .events import SafetyEventStore
from .golive import GoLive
from .keystore import DestinationKeys
from .orchestrator import Orchestrator
from .ratelimit import SlidingWindowLimiter
from .recorder import SessionRecorder
from .risk import RiskState
from .routes import alerts as alerts_routes
from .routes import auth_routes, control, health
from .routes import chat as chat_routes
from .routes import config as config_routes
from .routes import destinations as destinations_routes
from .routes import enrol as enrol_routes
from .routes import events as events_routes
from .routes import mod as mod_routes
from .routes import pairing as pairing_routes
from .routes import report as report_routes
from .routes import risk as risk_routes
from .routes import session as session_routes
from .security import SecurityMiddleware
from .sessions import SessionManager
from .settings import ApiSettings
from .ws import EventHub
from .ws import router as ws_router


def create_app(
    settings: ApiSettings | None = None,
    bus: BusClient | None = None,
    config: Config | None = None,
) -> FastAPI:
    """Build the API. ``bus`` defaults to the backend named in the settings (ZeroMQ) and ``config``
    to ``contracts.load_config`` for the profile in ``STREAMSARTHI_PROFILE`` (default ``dev``)."""
    settings = settings or ApiSettings()
    tokens = TokenService(settings.secret(), room_id=secrets.token_urlsafe(9))
    bus = bus or make_bus(settings.bus_backend, settings.bus_sub_endpoint, settings.bus_pub_endpoint)
    config = config if config is not None else load_config()
    modq = config.chat.modqueue
    chat_state = ChatState(max_items=modq.max_items, ttl_s=modq.ttl_s)
    hub = EventHub(bus, settings, tokens, chat_state)

    enrol_cfg = config.enrol
    allowlist = AllowlistService(
        AllowlistStore(
            ttl_s=enrol_cfg.ttl_s,
            max_faces=enrol_cfg.max_faces,
            persist_path=settings.allowlist_path if enrol_cfg.persist else None,
            key=settings.allowlist_key.get_secret_value() if settings.allowlist_key else None,
        ),
        bus,
        grace_s=settings.allowlist_resync_grace_s,
        sweep_s=settings.allowlist_sweep_s,
    )
    events = SafetyEventStore()
    risk = RiskState()
    sessions = SessionManager(start_timeout_s=settings.session_start_timeout_s)
    recorder = SessionRecorder(Path(settings.sessions_dir).expanduser() if settings.sessions_dir else None)
    keys = DestinationKeys()
    orchestrator = Orchestrator(
        enabled=settings.session_spawn,
        engine_cmd=settings.engine_argv(),
        chat_cmd=settings.chat_argv(),
        stop_timeout_s=settings.session_stop_timeout_s,
        chat_env={
            "BUS_SUB_ENDPOINT": settings.bus_sub_endpoint,
            "BUS_PUB_ENDPOINT": settings.bus_pub_endpoint,
        },
        on_engine_exit=lambda _code: sessions.mark_engine_exit(),
    )

    def engine_settled() -> bool:
        status = hub.engine_status()
        return status is None or status.state in ("starting", "ready", "error")

    golive = GoLive(
        sessions=sessions,
        orchestrator=orchestrator,
        recorder=recorder,
        keys=keys,
        ingest_host=settings.ingest_host,
        ingest_rtmp_port=config.ingest_port_rtmp,
        ingest_srt_port=config.ingest_port_srt,
        ttl_s=settings.session_ttl_s,
        stop_timeout_s=settings.session_stop_timeout_s,
        engine_settled=engine_settled,
    )

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await bus.start()
        await hub.start()
        background = [
            asyncio.create_task(events.run(events.subscribe(bus)), name="safety-events"),
            asyncio.create_task(allowlist.run(allowlist.subscribe()), name="allowlist-sync"),
            asyncio.create_task(chat_state.run_sweeper(), name="chat-sweeper"),
            asyncio.create_task(risk.run(risk.subscribe(bus)), name="risk-state"),
            asyncio.create_task(recorder.run(recorder.subscribe(bus)), name="session-recorder"),
        ]
        await orchestrator.start_watching()
        try:
            yield
        finally:
            await golive.shutdown()  # children get SIGTERM and a grace period before the bus closes
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await hub.stop()
            await bus.stop()

    app = FastAPI(
        title="Stream Sarthi API",
        version=API_VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.tokens = tokens
    app.state.bus = bus
    app.state.hub = hub
    app.state.config_store = ConfigStore(config)
    app.state.sessions = sessions
    app.state.orchestrator = orchestrator
    app.state.golive = golive
    app.state.recorder = recorder
    app.state.risk = risk
    app.state.keys = keys
    app.state.pairing_builder = None  # set to inject a fake; otherwise relay.pairing with a local fallback
    app.state.allowlist = allowlist
    app.state.events = events
    app.state.enroller = None  # set to inject a FaceEnroller; otherwise loaded from enrol.impl
    app.state.max_enrol_frames = MAX_ENROL_FRAMES
    app.state.enrol_inflight = 0
    app.state.enrol_limiter = SlidingWindowLimiter(settings.enrol_rate_limit, settings.enrol_rate_window_s)
    app.state.mod_action_limiter = SlidingWindowLimiter(
        settings.mod_action_limit, settings.mod_action_window_s
    )
    app.state.hold_release_limiter = SlidingWindowLimiter(
        settings.hold_release_limit, settings.hold_release_window_s
    )

    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth_routes.router)
    app.include_router(control.router)
    app.include_router(config_routes.router)
    app.include_router(enrol_routes.router)
    app.include_router(events_routes.router)
    app.include_router(alerts_routes.router)
    app.include_router(chat_routes.router)
    app.include_router(mod_routes.router)
    app.include_router(session_routes.router)
    app.include_router(destinations_routes.router)
    app.include_router(risk_routes.router)
    app.include_router(pairing_routes.router)
    app.include_router(report_routes.router)
    app.include_router(ws_router)

    # Innermost first: CORS (exact origins, Authorization only), then the security layer.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )
    app.add_middleware(
        SecurityMiddleware,
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=settings.allowed_origins,
    )
    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(prog="python -m app.api.main")
    parser.add_argument("--lan", metavar="PRIVATE_IP", help="bind this private IP (token required)")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()

    overrides: dict[str, object] = {}
    if args.lan:
        overrides["lan_host"] = args.lan
    if args.port:
        overrides["port"] = args.port
    settings = ApiSettings(**overrides)  # type: ignore[arg-type]
    app = create_app(settings)

    # Console only, never written to a log file: the streamer opens this link once.
    token = app.state.tokens.issue("streamer")
    print(f"Stream Sarthi API on http://{settings.bind_host}:{settings.port}")  # noqa: T201
    print(f"Open the app: http://{settings.bind_host}:{settings.web_port}/#token={token}")  # noqa: T201
    uvicorn.run(app, host=settings.bind_host, port=settings.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
