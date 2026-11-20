# Spec: `relay/`

Status: approved plan, section 7. Contracts: `contracts/`. Budgets: `docs/PLAN.md` section 2.5.

## 1. Purpose and owner

The relay moves bytes in and out of the engine. It supervises MediaMTX (ingest of SRT and RTMP from OBS or a phone), decodes to the canonical 1280x720 canvas, encodes the engine's output once, and forwards it to YouTube, Twitch and a loopback tap over verified TLS. It never decides what is private.

Owner agent: `pipeline-engineer` (instance restricted to `relay/`).

## 2. Directories owned and file layout

Edit only `relay/` and `scripts/run_*`. Tests in `relay/tests/`.

| File | Role | Origin |
|---|---|---|
| `relay/mediamtx.py` | spawn and supervise MediaMTX v1.21.4, stdin config, log classifier | adapt `reference/irl-shield/relay/mediamtx.py` |
| `relay/ingest.py` | `NetworkInput`: PyAV reader, reconnect generations, drop-oldest queue, `FrameSource` impl | adapt `relay/stream.py` (input half) |
| `relay/encoder.py` | spawned encoder process, killable, one encode feeds N muxers | adapt `relay/stream.py` (encoder half) |
| `relay/egress.py` | single-slot non-blocking writer | reuse `relay/egress.py` |
| `relay/tls_forwarder.py` | verified TLS loopback forwarder, one per destination | reuse `relay/tls_forwarder.py` |
| `relay/destinations.py` | YouTube, Twitch and tap destination definitions | new |
| `relay/security.py` | runtime-only credentials, bind rules | adapt `relay/security.py` |
| `relay/pairing.py` | Moblin `moblin://` import payload (pure function; QR rendering is `app/api`) | adapt `shield/dashboard/pairing.py` (logic only) |
| `relay/runtime.py` | `build(config, bus)` returning a `FrameSource` and `FrameSink` | rewrite |
| `relay/config/mediamtx.yml.tmpl` | path `camera` (publish), path `shielded` (tap) | new |
| `scripts/run_engine` | launches the full local stack | new |

MediaMTX binary comes from `scripts/fetch_tools.sh` (lead, hash-pinned v1.21.4, bumped from IRL Shield's 1.21.3 with a new SHA-256). The relay refuses to start if the hash differs.

## 3. Inputs

- OBS or phone: SRT on `INGEST_PORT_SRT` (default 8890) and RTMP on `INGEST_PORT_RTMP` (1935). Phone app: Moblin (iPhone) or Larix (Android).
- From the engine (`FrameSink`): composited BGR frame, 1600 samples of 48 kHz mono audio, PTS.
- `Config.destinations` and secrets from the environment (`YOUTUBE_STREAM_KEY`, `TWITCH_STREAM_KEY`).

## 4. Outputs

- To the engine (`FrameSource`): `Frame(seq, pts, arrival, image)` at 1280x720 BGR and a 48 kHz mono int16 audio stream tagged by absolute sample index.
- Egress: RTMPS to `a.rtmps.youtube.com:443/live2/<key>`, Twitch RTMPS, and the loopback MediaMTX path `shielded` (tap for the eval gates).
- Bus: `HoldCommand(source=AUTO_TRANSPORT)` on ingest drop, `HoldCommand(source=AUTO_EGRESS)` on egress reconnect; `SafetyEvent(modality=SYSTEM, category=HOLD)`; `StageTiming` for `ingest_decode`, `encode_submit`, `egress_write`.

## 5. Behaviour

### 5.1 MediaMTX supervision
1. Write config over stdin (no config file with keys on disk). Recording off. Paths: `camera` (publisher auth by a runtime-generated username and password), `shielded` (read-only loopback).
2. Start and watch the process; classify its log lines (publisher connected, SRT loss, auth failure). Restart with backoff 0.5, 1, 2, 4, 8 s. Replace IRL Shield's `/proc/<pid>/net` socket audit with a check through MediaMTX's API (`/v3/paths/list`).
3. SRT: `srtMinLatency` default 120 ms on LAN, 300-500 ms for 4G (`relay.srt_latency_ms`).

### 5.2 Ingest (`ingest.py`)
1. PyAV reads `rtsp://127.0.0.1:8554/camera` over TCP in a thread (`m2-input`). Output queue `Queue(maxsize=8)`; when full, drop the oldest and count it.
2. Decode to BGR, then letterbox to 1280x720 preserving aspect (portrait or 4:3 input is padded, not stretched). Resample audio to s16 mono 48 kHz.
3. PTS: container PTS rebased to 0 at the first frame of a connection generation. A reconnect increments the generation; the engine restarts its clock and starts in `PROTECT`. No hard-coded 30 fps: read the stream's rate; the constant 1600 samples per frame becomes `48000 / fps`.
4. On ingest drop for more than 1.0 s publish `HoldCommand(AUTO_TRANSPORT, active=True)` and release on the first good frame after reconnect.

### 5.3 Encoder (`encoder.py`)
1. Spawned process (`multiprocessing` spawn), frames over a socketpair, one frame in flight, 5 s reply deadline, backoff 0.5 to 8 s, killable by the parent.
2. Default x264: `libx264`, preset `veryfast`, 720p30, about 4.5 Mbps (CBR-ish, `maxrate 4.5M`, `bufsize 9M`), keyframe every 2 s (`gop = 2 * fps`), no B-frames, `yuv420p`. Audio AAC 128 kbps, 48 kHz, stereo (mono upmixed by duplication).
3. NVENC when `ffmpeg -encoders` lists `h264_nvenc` and `gpu.enabled`: `p4` preset, CBR, same GOP. Verified present on the dev laptop under WSL2 (about 280 fps at 720p30). Fall back to x264 on any NVENC init failure.
4. One encode feeds N muxers (YouTube, Twitch, tap). The IRL Shield cap of 2 destinations is removed; a destination that fails is isolated and retried without touching the others.

### 5.4 Egress and TLS (`egress.py`, `tls_forwarder.py`, `destinations.py`)
1. The encoder muxes FLV to loopback; one `tls_forwarder` per destination connects out with certificate verification against the system store and a pinned hostname (`a.rtmps.youtube.com`, Twitch ingest host). Max 2 connections per forwarder.
2. `EgressWriter` keeps one replaceable pending result so a slow destination never grows memory.
3. YouTube URL `rtmps://a.rtmps.youtube.com:443/live2/<STREAM_KEY>`; the key is read from the environment at connect time and never logged or written to disk. Remove IRL Shield's Twitch-only host, the `?bandwidthtest=true` default, and the `naie` broadcaster lock (it lives in `shield/app/live.py`; do not copy that file).
4. A destination reconnect asserts `HoldCommand(AUTO_EGRESS)` only if it was the last healthy destination.

### 5.5 Moblin pairing (`pairing.py`, T6.4)
Builds the `moblin://?{json}` import payload (ingest URL, stream id, credentials) as a pure function. QR rendering is `app/api`'s job. The Tailscale-only address rule is relaxed to: loopback, LAN private ranges, Tailscale `100.64.0.0/10`.

### 5.6 What is deleted
`relay/archive.py`, `seal.py`, `bridge.py`, `media.py`, `run.py` are not copied. No `--archive` option exists anywhere. Not even a debug build may write encoded media to disk.

## 6. Reuse

| Source | Change |
|---|---|
| `reference/irl-shield/relay/mediamtx.py` | keep stdin config and log classifier; drop `/proc` audit; bump version and SHA; config template for our paths |
| `relay/stream.py` | keep `NetworkInput` reconnect and generation logic, killable encoder; lift the 2-destination cap; derive fps and sample count; remove `MuxSink`/archive; add x264 veryfast option |
| `relay/egress.py`, `relay/tls_forwarder.py` | use as is (generic) |
| `relay/security.py` | drop the Tailscale-only bind rule and `TwitchDestination` coupling; keep runtime credentials |
| `relay/live.py` | its A/V ring moves to `engine/session.py`; do not duplicate it here |
| `relay/runtime.py` | rewrite; it imports `bench/` and is Twitch-coupled |
| `relay/synthetic.py`, `fixture.py` | go to `eval/sender/` (not the relay) |
Keep the upstream MIT LICENSE next to copied files; log them in `docs/THIRD_PARTY.md`.

## 7. Edge cases

SRT jitter and loss; MediaMTX crash; publisher reconnect with a new generation and PTS reset; destination rejects the key (YouTube not live yet: retry every 5 s, surface a `SafetyEvent(SYSTEM)` code); YouTube ingest stalls; encoder crash or 5 s reply timeout; NVENC not available at runtime; phone rotated to portrait; audio-only or video-only drops; very low input fps; ports already in use (fail with a clear message); credentials accidentally present in the log line stream (the log classifier must redact URLs containing keys).

## 8. Test cases (CPU only unless noted)

1. MediaMTX supervisor: refuses a binary with the wrong SHA-256; restarts after a kill with the backoff sequence; stdin config contains no key material on disk.
2. Ingest: feed a synthetic RTMP stream (from `eval/sender`) for 60 s; zero dropped frames, PTS monotonic, 1280x720 output.
3. Letterbox: a 1080x1920 portrait input arrives as 1280x720 with aspect preserved.
4. Reconnect: kill the publisher; engine receives a new generation and `HoldCommand(AUTO_TRANSPORT)` within 1.5 s; releases on the first frame.
5. Encoder: a killed encoder is replaced within backoff; a frame in flight never blocks the engine more than 5 s.
6. Egress: against a mock RTMPS sink (self-signed CA injected into the trust store), frames arrive as FLV with a keyframe every 2 s; a bad certificate is rejected.
7. N destinations: one failing destination does not interrupt the others.
8. Secrets: `grep` of captured logs finds no stream key; `repr` of destination objects hides keys.
9. No-archive test: running a session for 30 s creates no files outside `runs/<id>/timing.jsonl` (assert directory listing).
10. SRT impairment (Phase 7, GPU/net privileges needed, manual): `tc netem` 4G profile (loss 2 %, jitter 50 ms); no hang, slate during drops, recovery within 5 s.
11. **Acceptance T0.3:** 60 s loopback with 0 drops (gate G0).
12. **Acceptance T1.2:** engine output reaches the loopback tap and a mock YouTube sink; keyframe interval 2 s; audio AAC 48 kHz.
13. **Acceptance T6.4:** Moblin payload round-trips; ingest drop shows the transport slate and recovers.

## 9. Config keys used

`fps`, `canvas`, `delay_seconds` (read only), `gpu.enabled`, `destinations[]` (`kind`, `url_host`, `enabled`), env `YOUTUBE_STREAM_KEY`, `TWITCH_STREAM_KEY`, `INGEST_PORT_SRT`, `INGEST_PORT_RTMP`. Added to `Config` (D-25): `relay.srt_latency_ms` (120 LAN, 300-500 4G), `relay.video_bitrate_kbps` (4500), `relay.audio_bitrate_kbps` (128), `relay.keyframe_seconds` (2), `relay.encoder` (`auto|x264|nvenc`), `relay.ingest_hold_after_s` (1.0), `relay.mediamtx_api_port`.

## 10. Latency target

Ingest plus MediaMTX at most 0.25 s on a LAN (up to 0.5 s over 4G SRT); decode plus resize at most 0.05 s; encode plus mux plus egress write at most 0.2 s.

## 11. VRAM and CPU target

VRAM: 0 on x264; NVENC session about 0.15 GB. CPU: MediaMTX 0.1 core, decode 0.3, x264 veryfast 720p30 about 1.0-1.5 core (0.26 measured on synthetic content, so budget conservatively).

## 12. Tasks

| Task | Wave | Depends | Scope |
|---|---|---|---|
| T0.3 | 0.B | T0.1, contracts | MediaMTX supervisor, loopback reader, TLS forwarder port, ingest tests |
| T1.2 | 1 | T0.3 | encoder process, AAC, egress writers, YouTube and Twitch destinations, N destinations, backoff, archive removal |
| T6.4 | 6 | G5 | SRT tuning for 4G, ingest-drop hold, reconnect, Moblin payload |
| T7.3 | 7.A | G6 | profiling, soak, `netem` run (shared with engine) |

## 13. Out of scope

Deciding what to mask, audio analysis, the compositor, HTTP APIs, QR image rendering, any recording or archive, adaptive output bitrate (not in v1; ingest adaptation is via SRT latency only), vertical (9:16) output formats, CDN or cloud relays.
