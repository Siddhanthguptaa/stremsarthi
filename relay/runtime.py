"""``relay.runtime:build``: what ``engine/main.py`` loads from ``Config.runtime.modules``.

Ingest (T0.3): MediaMTX is started and supervised and the PyAV reader is the ``FrameSource``.
Egress (T1.2): one killable encoder process and one independent muxer per destination (YouTube,
Twitch, the local ``shielded`` tap) behind a single-slot, non-blocking ``FrameSink``.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Callable
from typing import Any

from contracts.config import Config
from contracts.protocols import EventBus
from contracts.runtime import RelayEndpoints
from relay.destinations import (
    DestinationPublisher,
    DestinationSet,
    Opener,
    build_destinations,
    default_opener,
)
from relay.egress import Egress
from relay.encoder import Encoder, EncoderSettings, describe_command, select_encoder
from relay.ingest import NetworkInput
from relay.mediamtx import MediaMTXSupervisor
from relay.security import PATH_SHIELDED, Credentials, RelayAddress
from relay.sink import EgressFrameSink
from relay.tls_forwarder import TLSForwarder

log = logging.getLogger("relay.runtime")

# Not in Config yet (see the T0.3 report): read from the environment with safe defaults.
ENV_BIND_HOST = "INGEST_BIND_HOST"  # default 127.0.0.1; a LAN or Tailscale address for a phone
ENV_PORT_RTSP = "INGEST_PORT_RTSP"  # internal RTSP reader port, default 8554
ENV_PUBLISH_USER = "INGEST_PUBLISH_USER"  # optional fixed publisher login so OBS survives restarts
ENV_PUBLISH_PASSWORD = "INGEST_PUBLISH_PASSWORD"


class RelaySource(NetworkInput):
    """The ingest ``FrameSource`` that owns its MediaMTX supervisor and stops it on ``close()``."""

    supervisor: MediaMTXSupervisor

    def publish_url(self, protocol: str) -> str:
        """URL for OBS or a phone (``rtmp`` or ``srt``). Contains the login: never log it."""
        return self.supervisor.publish_url(protocol)


def _publisher_credentials(env: dict[str, str]) -> Credentials | None:
    user, password = env.get(ENV_PUBLISH_USER), env.get(ENV_PUBLISH_PASSWORD)
    if user and password:
        return Credentials(user, password)
    return None


def build_egress_sink(
    config: Config,
    bus: EventBus | None,
    *,
    tap_url: str | None,
    environ: dict[str, str] | None = None,
    session_id: str | None = None,
    cafile: str | None = None,
    opener: Opener = default_opener,
    encoder_factory: Callable[[EncoderSettings], Any] | None = None,
) -> EgressFrameSink:
    """Encoder, destinations and the sink. ``cafile`` (tests only) is an extra trust anchor for the TLS peers."""
    codec, reason = select_encoder(config.relay.encoder, config.gpu.enabled)
    settings = EncoderSettings.from_config(config, codec)
    built = build_destinations(config, tap_url=tap_url, environ=environ if environ is not None else dict(os.environ))
    for code in built.skipped:
        log.warning("destination skipped: %s", code)  # fixed codes such as youtube:no_key, never a value
    publishers: list[DestinationPublisher] = []
    destinations: DestinationSet | None = None
    holder: dict[str, Egress] = {}
    try:
        for spec in built.specs:
            forwarder = TLSForwarder(spec.host, spec.port, cafile=cafile).start() if spec.tls else None
            publishers.append(DestinationPublisher(spec, fps=settings.fps, forwarder=forwarder, opener=opener))
        destinations = DestinationSet(
            publishers,
            bus=bus,
            session_id=session_id or uuid.uuid4().hex,
            hold_after_s=config.relay.ingest_hold_after_s,
        )

        def on_event(code: str) -> None:
            if "egress" in holder:
                holder["egress"].encoder_event(code)

        kwargs: dict[str, Any] = {"on_event": on_event}
        if encoder_factory is not None:
            kwargs["factory"] = encoder_factory
        encoder = Encoder(settings, **kwargs)
        egress = Egress(encoder, destinations)
        holder["egress"] = egress
        encoder.start()
        log.info(
            "egress: encoder=%s (%s) destinations=%s",
            encoder.codec, reason, [p.name for p in publishers],
        )  # fmt: skip
        log.info("egress command: %s", describe_command(encoder.settings, [p.name for p in publishers]))
        destinations.start()
        return EgressFrameSink(egress, canvas=(int(config.canvas[0]), int(config.canvas[1])))
    except BaseException:
        if destinations is not None:
            destinations.close()
        else:
            for publisher in publishers:
                publisher.close()
        if "egress" in holder:
            holder["egress"].encoder.close()
        raise


def build(config: Config, bus: EventBus, *, environ: dict[str, str] | None = None) -> RelayEndpoints:
    env = dict(os.environ if environ is None else environ)
    address = RelayAddress(
        host=env.get(ENV_BIND_HOST, "127.0.0.1"),
        rtmp_port=config.ingest_port_rtmp,
        srt_port=config.ingest_port_srt,
        rtsp_port=int(env.get(ENV_PORT_RTSP, "8554")),
        api_port=config.relay.mediamtx_api_port,
    )
    supervisor = MediaMTXSupervisor(
        address, phone=_publisher_credentials(env), srt_latency_ms=config.relay.srt_latency_ms
    ).start()
    session_id = uuid.uuid4().hex
    try:
        source: Any = RelaySource(
            supervisor.reader_url(),
            fps=float(config.fps),
            bus=bus,
            session_id=session_id,
            hold_after_s=config.relay.ingest_hold_after_s,
        )
    except BaseException:
        supervisor.stop()
        raise
    source.supervisor = supervisor
    source.add_close_hook(supervisor.stop)
    try:
        sink = build_egress_sink(
            config,
            bus,
            tap_url=address.rtmp(PATH_SHIELDED, supervisor.internal),
            environ=env,
            session_id=session_id,
        )
    except BaseException:
        source.close()
        raise
    return RelayEndpoints(source=source, sink=sink)
