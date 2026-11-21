"""Manual driver for ``scripts/netem_4g.sh`` (not collected by pytest: the name has no ``test_`` prefix).

Starts MediaMTX on one SRT port, publishes a stamped 720p30 stream over SRT with the given latency
and an AES passphrase, reads it with ``NetworkInput`` and prints one JSON summary line: frames,
frame gaps, transport holds, and ingest latency (publisher stamp to reader arrival, p50 and p95).
Run it through ``scripts/netem_4g.sh run`` so the impairment is on the same SRT port.
"""

from __future__ import annotations

import argparse
import json
import time

from contracts.enums import Topic
from relay.ingest import NetworkInput
from relay.mediamtx import MediaMTXSupervisor, verified_binary
from relay.security import RelayAddress
from relay.srt import generate_passphrase
from relay.tests.support_media import Collector, StampedPublisher, TimedBus, free_port, read_stamp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--srt-port", type=int, required=True)
    parser.add_argument("--latency-ms", type=int, default=400)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--hold-after", type=float, default=1.0)
    args = parser.parse_args()
    import socket

    address = RelayAddress(
        "127.0.0.1",
        rtmp_port=free_port(),
        srt_port=args.srt_port,
        rtsp_port=free_port(),
        api_port=free_port(socket.SOCK_STREAM),
    )
    supervisor = MediaMTXSupervisor(
        address,
        binary=verified_binary(),
        srt_latency_ms=args.latency_ms,
        srt_passphrase=generate_passphrase(),
    ).start()
    bus = TimedBus()
    source = NetworkInput(supervisor.reader_url(), bus=bus, hold_after_s=args.hold_after)
    collector = Collector(source, keep_images=0, probe=read_stamp)
    publisher = StampedPublisher(supervisor.publish_url("srt"), "srt")
    t0 = time.monotonic()
    try:
        time.sleep(args.seconds)
    finally:
        stats = source.stats()
        publisher.stop()
        collector.stop()
        supervisor.stop()
    frames = collector.frames
    lat = sorted(
        (((int(f.arrival * 1000) & 0xFFFFFFFF) - f.probe) & 0xFFFFFFFF) / 1000.0 for f in frames[60:]
    )
    holds = [(t, h.active) for t, h in bus.times_of(Topic.HOLD)]
    pts = [f.pts for f in frames if f.generation == 1]
    gaps = [b - a for a, b in zip(pts, pts[1:], strict=False)]
    summary = {
        "srt_latency_ms": args.latency_ms,
        "seconds": round(time.monotonic() - t0, 1),
        "frames": len(frames),
        "expected_frames": int(args.seconds * 30),
        "generations": stats["generation"],
        "max_pts_gap_s": round(max(gaps), 3) if gaps else None,
        "holds_asserted": sum(1 for _, active in holds if active),
        "ingest_latency_p50_s": round(lat[len(lat) // 2], 3) if lat else None,
        "ingest_latency_p95_s": round(lat[int(len(lat) * 0.95)], 3) if lat else None,
        "srt_loss_log_events": supervisor.classifier.counts.get("srt-loss", 0),
    }
    print("NETEM_SUMMARY " + json.dumps(summary))


if __name__ == "__main__":
    main()
