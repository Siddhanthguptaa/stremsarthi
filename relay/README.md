# relay

Moves bytes in and out of the engine. It never decides what is private. Ingest (T0.3) and egress (T1.2).
Spec: `docs/specs/relay.md`.

| File | Role |
|---|---|
| `mediamtx.py` | Hash-pinned MediaMTX v1.21.4 supervisor: stdin config, log classifier (fixed codes), restart backoff, API readiness |
| `ingest.py` | `NetworkInput` (`FrameSource`): PyAV reader, reconnect generations, 1280x720 letterbox, PTS rebased to 0, 48 kHz mono audio, `HoldCommand(AUTO_TRANSPORT)` |
| `encoder.py` | Killable spawned encoder (shared-memory frame, one in flight, 5 s reply deadline, backoff 0.5 to 8 s). libx264 veryfast or h264_nvenc, 720p30, 4500 kbps, GOP = 2 s, no B-frames, AAC 128 kbps 48 kHz stereo. One encode, N muxers. Audio is placed on the video PTS clock |
| `egress.py` | `EgressWriter` (single replaceable slot, engine tick never blocks) and `Egress` (encode, then fan out; `encode` timing) |
| `destinations.py` | YouTube, Twitch, `mediamtx_tap`: per-destination thread, queue, FLV muxer, backoff, `egress_write` timing, `AUTO_EGRESS` hold only when ALL are down |
| `tls_forwarder.py` | One verified-TLS loopback forwarder per destination (system CAs, pinned host; `cafile` for tests) |
| `security.py`, `sink.py`, `runtime.py` | Bind rules and credentials; `EgressFrameSink` and `NullFrameSink`; `build(config, bus) -> RelayEndpoints` |

Keys: `YOUTUBE_STREAM_KEY` / `TWITCH_STREAM_KEY` in `.env` (`SecretStr`, never logged; logs carry fixed codes
and class names only). A destination without a key is skipped. Destinations: `Config.destinations`
(`url_host` may be `host:port`). Nothing is written to disk. NVENC is used on `relay.encoder: auto` only when
`gpu.enabled` and a 1-frame test encode passes; any NVENC start failure falls back to x264.

Run from the repo root through the wrapper (see `docs/AGENT_BRIEF.md`):

```
MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /home/siddhanth/stremsarthi/scripts/ss.sh \
  python -m pytest relay/tests -q -m "not slow and not gpu"     # unit + integration, about 1 min
  python -m pytest relay/tests -q -m slow                        # the 60 s soak (reports encode CPU)
  flock /tmp/streamsarthi-gpu.lock env STREAMSARTHI_GPU_LOCK_HELD=1 python -m pytest relay/tests -q -m gpu
```

Integration tests need `.tmp/tools/mediamtx-v1.21.4/mediamtx` and `ffmpeg`/`ffprobe`. They use free ports,
a test CA made in the test (local RTMPS sink) and synthetic frames; no real YouTube is contacted.

Licence: parts are adapted from IRL Shield (MIT); see `LICENSE` here and `docs/THIRD_PARTY.md`.
