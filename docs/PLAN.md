# Stream Sarthi: lead architect plan

Date: 2026-11-20. Status: **APPROVED** (user replied "Proceed" on 2026-11-20, no changes requested). This is the source of truth for the build; changes go through `docs/DECISIONS.md`. Progress lives in `docs/STATUS.md`; per-module specs in `docs/specs/`.
Sources: `CLAUDE.md`, `STREAM_SARTHI.md`, `reference/REUSE_MAP.md`, plus three exploration reports (IRL Shield, PrivaStream, dependencies and hardware). Claims I could not verify are marked **UNVERIFIED**. Estimates are marked *est.*

## 0. Context and what the research changed

Stream Sarthi sits between OBS or a phone and YouTube/Twitch, and masks private data in video, audio and chat inside a 3–5 s delay. The repo has the brief, rules, six builder agent definitions and trimmed copies of PrivaStream (Apache-2.0) and IRL Shield (MIT).

Findings that change the plan:

| # | Finding | Consequence |
|---|---|---|
| 1 | IRL Shield's deadline engine, hold state machine, lanes, motion projection and TLS forwarder are clean and fail closed | Reuse them as the engine core (adapt, not rewrite) |
| 2 | IRL Shield **delays audio but never mutes it**, even during hold. PrivaStream's mute has **no lookahead** (a number split across two 3 s chunks is half muted) | The audio mute path is new work: MuteSpans on one PTS clock, with padding and a delay-window deadline |
| 3 | PrivaStream **mouth blur does not exist** (plumbing only). Its face allowlist is **broken** (enrol with buffalo_s, match with buffalo_l). Its plate detector silently never loads. No weights ship | Rewrite faces on YuNet+SFace with per-track voting. Mouth ROI comes from YuNet's 5 landmarks. Plates are text-first |
| 4 | **Sarvam streaming STT returns finals only, no timestamps** | Mute granularity is the VAD segment (≤1.5 s) unless a timestamp path is confirmed in a Phase 0 spike |
| 5 | **Groq Whisper is limited to 20 requests/min** | It cannot be the primary STT for short chunks. Sarvam is primary, Groq is secondary with ≥3 s chunks, faster-whisper is the local fallback |
| 6 | PyTorch cu128 wheels stop at **2.11**. `onnxruntime-gpu` 1.26 has CUDA 12.8 wheels, and Blackwell works through JIT (**UNVERIFIED** on the RTX 5050) | Pin torch 2.11 and ORT-GPU 1.26. Torch becomes optional (extras only). Test the 5050 path early |
| 7 | Dev laptop is an i5-13450HX (6P+4E) with a **6 GB** RTX 3050. NVENC, libsrt and x264 all work in WSL2 | The 3.5 GB cap has headroom. x264 at 720p30 costs about 0.3–1.5 cores |
| 8 | IRL Shield failed its own held-out privacy gate (84 % natural, 64 % synthetic hidden). Its CPU privacy was never graded | Don't promise recall. Use mask-all-text for signboards, card-level quads, tracking and retroactive hold, and publish honest eval numbers |
| 9 | IRL Shield's `--archive` and `seal` store the full encoded stream | Delete them (violates store-nothing) |
| 10 | YouTube `liveChatMessages.list` costs 5 units per call on a 10k/day quota. A ban costs 200 units | Poll with a quota governor, prefer deletes over bans, and treat gRPC `streamList` as an upgrade |

## 1. Reuse inventory

Verdicts: **REUSE** = copy with licence header and minimal edits. **ADAPT** = copy then change. **REWRITE** = take ideas only. **SKIP** = leave out. Every copied file is logged in `docs/THIRD_PARTY.md` with its upstream path, licence and what changed. Upstream LICENSE/NOTICE files stay next to copied code.

### From IRL Shield (MIT)

| Source | Target | Verdict | Reason |
|---|---|---|---|
| `shield/engine.py` | `engine/deadline.py` | ADAPT | Best piece. Generalise from fixed fast/accurate lanes to N lanes with per-lane max age. Add mute spans and retroactive hold |
| `shield/control.py` | `engine/control.py` | REUSE | Per-source hold dict with HOLD > PROTECT > LIVE priority |
| `shield/types.py` | `contracts/` (ideas) | ADAPT | Fold into our contracts, add category and track id |
| `shield/lanes.py` | `vision/lanes.py` | ADAPT | Newest-frame-wins worker threads. Make stride/rate configurable per lane |
| `shield/motion.py` | `engine/motion.py` | REUSE | Deadline-gated global-motion projection. Local tracking is added separately |
| `shield/render.py` | `engine/compositor.py` | ADAPT | Quad rasteriser and PROTECT fallback. Add blur and pixelate |
| `shield/presentation/renderer.py`, `tally.py`, font (OFL) | `engine/slate.py` | ADAPT | Slate cache and "frames older than capture high-water mark stay slated" fence. Hindi/English copy. Keep the OFL file |
| `shield/control` wiring in `shield/app/appearance.py` | none | REWRITE | Reads private `_holds`. Replace with a public API |
| `shield/app/live.py` | none | **SKIP** | The `naie` broadcaster lock |
| `shield/app/session.py`, `shield/cli.py` | `scripts/run_engine`, `engine/main.py` | REWRITE | Tailscale, `/dev/tty` prompts and Twitch/demo coupling |
| `shield/skins/*` | none | SKIP | Viewer-vote skins are out of scope |
| `shield/dashboard/server.py`, `snapshot.py` | `app/api/` (patterns) | ADAPT | Bearer-token, Host/Origin check, CSP and allowlisted snapshot patterns. We use FastAPI |
| `shield/dashboard/pairing.py` | `app/api/pairing.py` | ADAPT | Moblin `moblin://` import QR for phone ingest. Relax the Tailscale-only rule |
| `shield/twitch/auth.py`, `http.py`, `storage.py` | `chat/twitch/` | ADAPT | Device-code flow, `Secret` type, keyring. Scopes change (see Decisions) |
| `shield/twitch/events.py` | `chat/twitch/events.py` | ADAPT | Dedup and generation fencing. Make the connection-loss hold optional |
| `shield/twitch/transport.py` | `chat/twitch/transport.py` | REWRITE | Monkeypatches pyTwitchAPI private hooks (fragile) |
| `shield/twitch/keys.py` | none | SKIP | Use a stream key from `.env` |
| `relay/live.py` (`RelaySession`) | `engine/session.py` | ADAPT | A/V-synced PCM ring, HOLD re-check before emit. Add mute-span application |
| `relay/stream.py` | `relay/ingest.py`, `relay/encoder.py` | ADAPT | Input reconnect/generation logic, killable spawned encoder, 5 s reply deadline. Lift the 2-destination cap. Remove archive writes. Make the 30 fps assumption config-driven |
| `relay/egress.py` | `relay/egress.py` | REUSE | Single-slot non-blocking writer |
| `relay/tls_forwarder.py` | `relay/tls_forwarder.py` | REUSE | Verified TLS. One instance per destination |
| `relay/mediamtx.py` | `relay/mediamtx.py` | ADAPT | Stdin config and log classifier. Replace the `/proc` socket audit. Bump to v1.21.4 with a new SHA |
| `relay/security.py` | `relay/security.py` | ADAPT | Runtime-only credentials. Drop the Tailscale-only bind rule and `TwitchDestination` |
| `relay/runtime.py` | `relay/runtime.py` | REWRITE | Imports `bench/`, Twitch-coupled, fixed 720p30 |
| `relay/archive.py`, `seal.py`, `bridge.py`, `media.py`, `run.py` | none | **SKIP** | Archive stores video. The rest is the legacy Mac path |
| `relay/synthetic.py`, `fixture.py` | `eval/sender/` | ADAPT | Optical-stamp and beep A/V latency measurement |
| `bench/onnx_detectors.py` | `vision/ort.py`, `vision/text/` | ADAPT | ORT session builder (provider check, `disable_fallback`, arena limit) and RapidOCR wrapper |
| `bench/static_model.py` | `vision/ort_static.py` | REUSE | Fixed-shape ONNX derivation |
| `bench/detector_options.py` | `vision/options.py` | ADAPT | Fold into Config |
| `bench/metrics.py`, `scoring.py`, `independence.py`, `provenance.py` | `eval/` | ADAPT | Interval-leak scoring idea. Replace the Latin-only judges |
| `bench/pipeline.py`, `scheduled.py`, `vision.py`, `reference_engine.py` | none | SKIP | Apple Vision and legacy |
| `models/prepare.py`, `manifest.json` | `models/prepare.py`, `models/manifest.json` | REUSE + extend | Hash-pinned downloader. Add YuNet, SFace, QR, OTP and plate models |
| `scripts/bounded.py` | `scripts/bounded.py` | REUSE | Process-group timeout wrapper |
| `scripts/m2-tools` | `scripts/fetch_tools.sh` | ADAPT | Hash-pinned MediaMTX fetch |
| `scripts/walk-linux`, `host-lock`, `setup*`, `tools/demo_renderer` | none | SKIP | Dev-host, `naie` and perf-lease specific |
| `tests/test_deadline_engine`, `test_control`, `test_lanes`, `test_motion*`, `test_render`, `m2/test_tls_forwarder`, `integration/test_writer_semantics`, `m3` mock-Twitch pattern | module `tests/` dirs | ADAPT | Port with the code they test |
| `requirements*.txt` (all) | none | **REWRITE** | Pascal-era CUDA 12.4 pins. Replaced by our `requirements/` |

### From PrivaStream (Apache-2.0)

| Source | Target | Verdict | Reason |
|---|---|---|---|
| `face_blur/face_detector.py` | `vision/detectors/faces/` | REWRITE (ideas kept) | Keep CLAHE low-light, dilation, panic mode, TTA-union. Replace buffalo_l (non-commercial, slow) with YuNet+SFace. Fix the shared vote buffer (privacy leak) with per-track votes |
| `face_blur/enroll_face.py` | `vision/detectors/faces/enroll.py` | ADAPT | Average + L2-normalise, multiple people, same model as matching |
| `pii_blur/pii_detector.py` `Hysteresis` | `vision/tracking/hysteresis.py` | ADAPT | K_confirm/K_hold stabiliser. Rework from IoU on boxes to tracks |
| `pii_blur` OCR pipeline, decider, `pii_hybrid.py`, `pii_clf.joblib`, `run_live.py` | none | REWRITE / **SKIP** | English-only, Singapore address rules. The joblib is a pickle (code-execution risk): never load it |
| `plate_blur/*`, `export_yolo.py` | none | SKIP | AGPL, weights absent, constructor bug |
| `unified_detector.py` | `vision/host.py` (idea) | REWRITE | Keep the registry + result-dict idea only |
| `detection/blur_utils.py` | `engine/blur.py` | ADAPT | Gaussian, pixelate and fill. Crop polygon masks to the ROI |
| `utils/blur_utils.py` | none | SKIP | Full-frame blur per polygon |
| `audio_redaction_server_faster_whisper.py` | `audio/stt/local.py` | ADAPT | faster-whisper call (`vad_filter`, `word_timestamps`, int8), interval merge. Redo char-to-word mapping and add padding |
| `training/src/whisper_processor.py` | `audio/stt/local.py` | ADAPT | Backend shim and absolute-offset pattern |
| `training/src/pii_detector.py` (deterministic part) | `audio/pii/rules.py` | ADAPT | Luhn and keyword-value regex, overlap merge. Drop the fake linear time estimate |
| `training/src/pipeline_types.py` | `contracts/` (ideas) | ADAPT | Shape for MuteSpan/Detection |
| `training/*`, DeBERTa configs, `piidd_postprocessing.py` | none | SKIP | Kaggle essay training. No training in v1 |
| `mediasoup/*`, `SLIDING_WINDOW_EXPLANATION.md` | none | SKIP | Design reference only (anti-pattern: no lookahead, wall-clock sync) |
| `backend/auth.py` | `app/api/auth.py` | REUSE | Dependency-free HMAC tokens, tested |
| `backend/app.py` `RoomManager` | `app/api/sessions.py` | ADAPT | TTL session manager for FastAPI |
| `backend/video_api_utils.py` | `engine/backpressure.py` | REUSE | `RequestTracker`, stale-drop, `clamp_region` |
| `backend/video_filter_api.py` | `app/api/enrol.py` | ADAPT (enrol and auth decorator only) | HTTP-per-frame hot path and global detector state are not used |
| `frontend/components/ui/*`, `lib/utils.ts`, `components.json`, `tailwind.config.js`, `globals.css` | `app/web/` | REUSE | shadcn on Tailwind 3 |
| `frontend/components/enrollment/*`, `app/enrollment/page.tsx` | `app/web/` | ADAPT | Camera capture and allowlist card. Fewer frames per POST |
| `frontend/lib/socket.ts`, `mediasoup-client.ts`, `webrtc.ts`, host/viewer pages | none | REWRITE / SKIP | Listener leaks, dead events, WebRTC viewer model |
| `frontend/public/pcm-downsampler-worklet.js` | `app/web/public/` | REUSE | Correct anti-aliased 16 kHz PCM |
| `benchmarks/ml_latency_harness.py` | `eval/microbench.py` | REUSE | p50/p95/p99 JSON timer |
| `models/audio/check_gpu.py` | `scripts/doctor.py` | ADAPT | Environment doctor |
| `docs/ML_PRODUCTION_PLAN.md` | `docs/` (read) | ADAPT | "Degrade toward more privacy" policy and release gates |

**Weights and licences to flag in `docs/THIRD_PARTY.md`:** Ultralytics (AGPL) is not load-bearing and is optional behind an extra. InsightFace buffalo packs (non-commercial) are an optional adapter only. Both are marked "replace before commercial release". Offleak: never copied.

## 2. Architecture

### 2.1 Processes and threads

```
 OBS/phone ──SRT/RTMP──► [P1 MediaMTX] ──loopback RTSP──┐
                                                         ▼
 ┌───────────────────────── [P2 engine process] ─────────────────────────────────┐
 │ T-ingest (PyAV decode, resize to 1280x720 canvas, tag PTS)                    │
 │    ├─► frame ring (D+1 s)         ├─► audio ring 48 kHz (sample-indexed)      │
 │    ├─► T-lane-fast  (≥8 Hz: faces, QR, game-id regions, mouth landmarks)      │
 │    ├─► T-lane-acc   (3–5 Hz: OCR det+rec, cards, plates, signs, screens)      │
 │    ├─► T-motion (global-motion path, deadline-gated)  + tracker               │
 │    └─► 16 kHz tap ─► [asyncio thread: audio/guard/captions]                   │
 │           VAD→segmenter→STT race→PII rules/LLM→MuteSpan; music; profanity     │
 │  T-tick (deadline engine: per frame at arrival+D) ─► compositor ─► egress slot│
 │  ZeroMQ broker thread (XSUB/XPUB) hosts the event bus                         │
 └──────────────────────────────┬────────────────────────────────────────────────┘
                                ▼
        [P3 encoder process (spawned, killable)] ──► TLS forwarder(s)
                ├─► rtmps://a.rtmps.youtube.com:443/live2/<key>
                ├─► Twitch RTMPS
                └─► MediaMTX `shielded` path (loopback tap, used by eval gates)

 [P4 chat process]  YouTube/Twitch connectors → classifier cascade → verdicts/actions
 [P5 api process]   FastAPI + WebSockets (fan-out, config, hold, mod queue, report)
 [web]              Next.js streamer app + moderator console (browser)
 [win helper]       optional Windows-side hotkey script → POST /hold
```

- Python threads work for vision because ORT, OpenCV and PyAV release the GIL (IRL Shield ran this way at about 378 % CPU). The detector host sits behind one interface, so if the GIL bites we can move lanes to a process pool with shared memory without changing detectors (Risk R1).
- Fault isolation: chat and api crashes never stop the stream. The engine owns hold, so a hotkey still works if api is down. An encoder crash triggers restart with backoff (0.5→8 s) and a transport HOLD slate.

### 2.2 Data flow

**Video.**
1. Decode to BGR, letterbox to the canonical 1280×720 canvas (non-16:9 input is padded, not stretched), stamp `Frame(seq, pts, arrival)`.
2. Frame goes to the ring. Lanes take the newest frame at their own rate (FAST stride 3 = 10 Hz in `dev`, stride 2 in `demo`; ACCURATE about 4 Hz). A slow lane drops work, never source frames.
3. Detectors emit `Detection`. The tracker (ByteTrack via `supervision`, plus hysteresis) assigns `track_id`. The mask planner turns detections into `MaskRequest` after applying the category toggles, padding and the allowlist.
4. At `arrival + D` the deadline engine selects the requests it can trust for that frame (see 2.3), paints masks, and hands the frame to the egress slot.

**Audio.**
1. Engine downsamples the ring to 16 kHz mono int16 `AudioChunk`s (20 ms frames) and feeds the audio thread.
2. Silero VAD opens a segment on speech and closes it at a pause or at **1.5 s** (hard cap), with **0.4 s** overlap. Segments carry source PTS.
3. STT race: Sarvam (primary) and the local faster-whisper path run in parallel. The first valid result wins. Groq Whisper (≥3 s chunks, ≤18 requests/min) is the secondary API.
4. A transcript window (last 8 s of words with PTS) lets the PII rules see numbers split across segments. Rules: digit words (Hindi, English, regional), Devanagari and other-script digits, "double/triple", spoken UPI ("name at oksbi"), phone-number structure, address cues. Ambiguous cases go to Groq `gpt-oss-20b` (JSON mode, timeout 0.7 s, token bucket under 30 RPM).
5. Output `MuteSpan(pts_start, pts_end)` plus a `MaskRequest(kind=MOUTH_OF_SPEAKER)` for the same span. Spans are padded by 0.2 s each side and tail-extended if the segment ends on a number word.
6. Audio fail-safe: if a voiced segment has no verdict by its deadline, emit `MuteSpan(granularity="failsafe")` for it. Music and non-speech audio pass through.

**Chat** (separate process, no frame path). Connector → `ChatMessage` → classifier cascade → `ChatVerdict` → bus → api (WebSocket to streamer overlay, mod queue) and platform actions (delete/timeout where the API allows).

### 2.3 Delay buffer, A/V sync, fail-safe, hold

- **Clock.** Container PTS rebased to 0 at session start. A frame's release deadline is `t0 + (pts − pts0) + D`, where `t0` is the engine arrival time of the first frame (IRL Shield's rule). Audio is indexed by absolute sample number from the same PTS, so both streams share one clock. Egress emits each frame with its 33.3 ms audio slice taken at that PTS, after mutes are applied.
- **D** (`delay_seconds`) defaults to 4.0 (`.env.example` has `STREAM_DELAY_SECONDS=4`). Config validates 3.0–5.0. Because ingest and encode add about 0.5 s, a D above about 4.3 can break the 5 s end-to-end gate, so the gate reports it.
- **Sizing.** Ring = `ceil(fps × (D + 1)) + 8` frames (about 160 frames, about 440 MB at 720p BGR). It is RAM only. Nothing is archived or written to disk.
- **Video fail-safe** (IRL Shield rule, kept). At the deadline a frame is `LIVE` only if every active lane has a connected result with `completed_at ≤ deadline` inside its max age (FAST ≤ 6 frames, ACCURATE ≤ 12 frames, `[i−6, i+30]` window), and motion projection of those results to frame `i` completed in time. Otherwise the frame goes out **PROTECT**: a slate. A missing input frame is filled with the last pixels but forced protected. A frame that arrives after its deadline is discarded. The engine starts in PROTECT.
- **Hold** is `HOLD > PROTECT > LIVE`, keyed by source (`hotkey`, `api`, `chat`, `app`, `auto-transport`, `auto-egress`). Releasing one source never clears another. Hold is re-checked after `poll`, in the egress writer and at the encoder hand-off. It renders a Hindi/English slate and **mutes audio** (IRL Shield did not).
- **Retroactive hold** (new). Because frames sit in the buffer for D seconds, pressing hold covers the last `retro_seconds` (default 3.0, clamped to `D − 0.5`) of frames and audio that have not yet left. A streamer who notices a leak on their own monitor can still stop it.
- **Per-category fail-safe** in Config: `mask_when_unsure` (default on for cards, QR, faces), threshold bias toward masking.

### 2.4 Where each model runs

| Component | Model | Default device | Rate | Notes |
|---|---|---|---|---|
| Face detect | YuNet 2023mar (OpenCV `FaceDetectorYN`) | CPU | FAST | 5 landmarks give the mouth ROI. MIT |
| Face match | SFace 2021dec | CPU | on new tracks | Apache-2.0 |
| Text detect | PP-OCRv6 tiny det (v5 mobile det as alternate), ORT, static shape | CPU in `dev` (GPU optional), GPU in `demo` | ACCURATE | Apache-2.0, SHA-pinned |
| Text recognise | RapidOCR ONNX rec: en/latin, devanagari, ta, te | CPU | on crops | No Kannada or Bengali rec in PP-OCR |
| QR | OpenCV `QRCodeDetectorAruco`/WeChatQRCode + zxing-cpp | CPU | FAST | Decode, then check `upi://` or `pay?pa=` |
| Plates | OCR-first (text det + plate-format match + vehicle gate); optional YOLO plate weights (AGPL, extra) | CPU | ACCURATE | |
| Tracking | ByteTrack (`supervision`) + hysteresis + global motion | CPU | every frame | under 5 ms |
| VAD | Silero VAD (ONNX) | CPU | stream | under 1 ms per 30 ms chunk |
| STT | Sarvam `saaras:v4` codemix (API), Groq `whisper-large-v3-turbo` (API), faster-whisper tiny/base int8 (CPU) / small (GPU, `demo`) | API / CPU | per segment | Local Hindi quality is poor, so it's a spotting fallback |
| Spoken-PII decisions, chat classification | Groq `gpt-oss-20b` | API | on demand | |
| Captions | Sarvam translate (Mayura 11 langs, sarvam-translate 22 langs); IndicTrans2 dist-200M local in `demo` | API / GPU | per sentence | HF model is gated |
| Music | YAMNet (ONNX/tflite) + Chromaprint/audfprint | CPU | 1 Hz window | Dejavu needs a database and looks unmaintained |
| Encode | x264 veryfast (CPU default), NVENC if present | CPU / NVENC | 30 fps | NVENC works in WSL2 (280 fps on 720p30 test) |

### 2.5 Budgets

**Latency (p95, `dev` profile, D = 4.0 s).**

| Stage | Budget | Notes |
|---|---|---|
| SRT/RTMP ingest + MediaMTX | 0.25 s (LAN), up to 0.5 s (4G, `srtMinLatency` 300–500 ms) | |
| Decode + resize | 0.05 s | |
| Delay buffer | **D = 4.0 s** | |
| Compositor per frame | 0.03 s | blur/pixelate over masks |
| Encode + mux + egress write | 0.2 s | |
| **Engine delay, capture to egress socket** | **≈ 4.5–4.8 s (gate ≤ 5.0)** | YouTube's own viewer latency comes on top (see Decision D-1) |
| Fast lane (faces, QR) | ≤ 25 ms per frame | result ≤ 0.1 s after arrival |
| Accurate lane (OCR det+rec, cards, plates) | ≤ 450 ms CPU, ≤ 120 ms GPU | deadline: `completed_at ≤ arrival + D − 0.5 s` |
| Audio: segment close | ≤ 1.5 s from speech start | |
| Audio: STT round trip | ≤ 1.0 s (Sarvam, **UNVERIFIED**, measure in Phase 0), 0.7 s (Groq, est.), 0.4 s (local base, est.) | |
| Audio: PII rules / LLM ambiguous | 0.02 s / 0.7 s timeout | |
| **Audio total** | **≤ 3.3 s** against `D − 0.3 = 3.7 s` | 0.4 s slack, so the fallback race matters |
| Mute-span deadline | `completed_at ≤ span_start_arrival + D − 0.1` | else failsafe mute |
| Caption translate | ≤ 0.8 s | captions render on the delayed timeline, so they line up with audio |
| Chat classify | ≤ 300 ms (rules ≤ 5 ms) | YouTube poll adds about 5 s before a message is seen |
| Hold | ≤ 1 frame after it reaches the engine, ≤ 100 ms hotkey to engine | |

**CPU (i5-13450HX, 6P+4E, 16 threads; est., confirm in Gate 1).**

| Component | Cores |
|---|---|
| MediaMTX | 0.1 |
| Decode (PyAV) | 0.3 |
| Vision lanes (faces, QR, OCR det+rec, cards, tracking, motion) | 1.0–1.5 |
| Compositor | 0.3 |
| x264 veryfast 720p30 | 1.0–1.5 (0.26 measured on synthetic content) |
| Audio (VAD, resample, async glue) | 0.2 (0.6 while the local STT fallback is active) |
| Python control, bus, API, chat | 1.0 |
| **Total** | **≈ 4–5 cores** of 16 threads, with headroom |

**GPU memory (est., one process, fixed ORT `gpu_mem_limit`; verify with `nvidia-smi`).**

| Item | `dev` (cap 3.5 GB) | `demo` (cap 7 GB) |
|---|---|---|
| CUDA context | 0.4 | 0.5 |
| OCR det+rec (ORT, capped arena) | 0.4 | 0.6 |
| YuNet + SFace | CPU | 0.1 |
| Plate/vehicle model | CPU | 0.3 |
| NVENC session | 0.15 | 0.15 |
| faster-whisper (local fallback) | CPU | 0.4 (small int8_fp16 about 0.6) |
| IndicTrans2 200M fp16 | off | 0.9 |
| Headroom for the Windows desktop | 1.0 | 2.0 |
| **Total** | **≈ 2.0** | **≈ 5.0** |

RAM: frame ring about 440 MB, plus models and buffers, under 4 GB total.

## 3. Decisions

Each is logged in `docs/DECISIONS.md` as D-n.

| ID | Decision | Reason |
|---|---|---|
| D-1 | **"End-to-end delay" gate = capture (optical stamp) to the frame being written to the egress socket, p95 ≤ 5.0 s.** We also record viewer-side latency on YouTube as an informational number | YouTube adds roughly 3–5 s of its own on top of anything we do, so a viewer-side 5 s is unreachable at D=4. **Needs your confirmation** |
| D-2 | **Event transport:** an in-process typed bus inside the engine for the hot path (frames never serialised), and **ZeroMQ PUB/SUB** (broker thread in the engine, `ipc://`) for cross-process events and commands. JSON wire format for contracts, with an `InProcessBus` fake for tests | Engine is the always-on process, so a hotkey and hold work if api dies. pyzmq is small and mature. No Redis or NATS to install |
| D-3 | **STT:** Sarvam `saaras:v4` codemix over WebSocket is primary (₹0.49/min about ₹29/h, 22 languages, REST 60/min, 20 concurrent WebSockets). Groq `whisper-large-v3-turbo` is secondary ($0.04/h but 20 RPM, so ≥3 s chunks). faster-whisper base int8 on CPU is the local fallback. All behind one `SpeechToText` protocol, raced per segment | Sarvam covers Hinglish and 5 target languages. Groq's limit blocks it as primary. Local is for outages and offline mode |
| D-4 | **Mute granularity:** segment-level (≤1.5 s, 0.2 s padding) by default, because Sarvam WebSocket has no timestamps. Word-level when a timestamped result arrives (Groq or local words). Phase 0 spike checks Sarvam REST `with_timestamps` (**UNVERIFIED**) | Honest about collateral muting. Evidence decides |
| D-5 | **Spoken-PII logic:** rules first (digit words in Hindi/English/regional, Devanagari digits, "double/triple", spoken UPI, phone structure, address cues), Groq `gpt-oss-20b` only for ambiguous cases with a 0.7 s timeout. If it times out, an ambiguous-and-voiced segment is muted | Deterministic fast path. LLM cost stays small. Fail toward privacy |
| D-6 | **Chat classification:** cascade of rules/keyword lists (Hinglish in Latin letters, Devanagari, misspellings), scam/UPI link patterns, doxxing (streamer private details from config plus phone/address patterns), then Groq `gpt-oss-20b` in batches of about 20 within 30 RPM. MuRIL fine-tune is a later option | Rules answer in under 5 ms. LLM handles the rest |
| D-7 | **Captions:** Sarvam translate (Mayura for code-mixed, 11 languages; sarvam-translate for the other 22), ₹2 per 1,000 characters. IndicTrans2 dist-200M local in `demo` only (HF gated, needs a token). Burned into the video on the delayed timeline, with Noto Indic fonts (OFL) and **libraqm** for correct Indic shaping | Captions are ready before display because of the delay |
| D-8 | **Profiles:** `config/profiles/{dev,demo}.yaml` over a shared base, merged by pydantic-settings, with env overrides. `dev`: stride 3, GPU optional with a 3.5 GB cap, no local IndicTrans2. `demo`: stride 2, GPU on, 7 GB cap, local fallback STT on GPU, local captions. Same code, config only | CLAUDE.md rule |
| D-9 | **Indian-script OCR plan.** Signboards are **detection-only mask-all-text** (IRL Shield's approach), script-agnostic and covering Kannada and Bengali. Recognition runs only on crops for structured PII: Aadhaar, PAN, plates, phone, PIN, UPI ID, email, game IDs. Recognisers: en/latin, devanagari, ta, te via RapidOCR. Kannada/Bengali recognition is an optional EasyOCR extra on crops, not on the real-time path. Devanagari digits are normalised | PP-OCRv5 has no Kannada or Bengali rec. Masking all sign text needs no recognition |
| D-10 | **Detector framework:** our own `Detector` Protocol with plugin autodiscovery (`vision/detectors/<id>/__init__.py` exports `DETECTOR`), FAST/ACCURATE/SLOW lanes, ORT session helper, `vision_mem_mb` declared per detector. New detectors need no edits to shared files | Lets 5 detector tasks run in parallel with no file overlap |
| D-11 | **Ultralytics / InsightFace:** neither is load-bearing. Default faces are YuNet+SFace. Plates are OCR-first. YOLO plate weights and InsightFace buffalo_s exist only as optional adapters behind extras, flagged "replace before commercial release" | Avoids AGPL and non-commercial weights on the default path. PrivaStream ships no weights anyway |
| D-12 | **Python stack:** Python 3.11 via `uv`. `onnxruntime-gpu==1.26.0` (never alongside plain `onnxruntime`). Torch 2.11 cu128 only as an optional extra (IndicTrans2, EasyOCR). `opencv-contrib-python-headless` only. numpy ≤2.4, av 16.0.1. Full list in section 7 (`requirements/`) | Fixes the three-OpenCV conflict, Python 3.12-only wheels, and the Pascal pins |
| D-13 | **YouTube chat:** poll `liveChatMessages.list` honouring `pollingIntervalMillis`, with a quota governor (10k/day: reads 5, delete 20, ban 200). Prefer hide-on-our-side and delete over bans. gRPC `streamList` is an upgrade task (**UNVERIFIED** that a clean Python client exists) | Quota arithmetic |
| D-14 | **Twitch:** EventSub WebSocket `channel.chat.message` via twitchAPI 4.5.0 (without monkeypatching private hooks), Helix delete (`moderator:manage:chat_messages`) and timeout (`moderator:manage:banned_users`) | Scopes differ from IRL Shield's |
| D-15 | **Encoding and outputs:** x264 veryfast by default, NVENC when `h264_nvenc` is present. 720p30 at about 4.5 Mbps, keyframe every 2 s (GOP 60), AAC 128 kbps 48 kHz stereo (YouTube recommends 44.1/48 kHz stereo). One encode feeds N destinations. YouTube via `rtmps://a.rtmps.youtube.com:443/live2/<key>` through the TLS forwarder | YouTube ingest guidance |
| D-16 | **Hotkey:** a Windows-side helper `scripts/hold_hotkey_win.py` calls the engine's local hold endpoint. Plus an in-app key and a mobile button | A WSL process cannot see global Windows hotkeys |
| D-17 | **Privacy:** no archive, no seal, no image or raw-PII logging. Logs hold category, time, confidence and box. Flagged chat messages are never logged raw. Tokens are RAM-only (optional OS keyring) | CLAUDE.md data rules |

## 4. Contracts (`contracts/`, lead-owned, written first)

Python 3.11, `dataclasses(frozen=True, slots=True)` for hot-path types and pydantic v2 for Config and wire models. Every type has `to_wire()` / `from_wire()` (JSON). All pixel coordinates are in the canonical 1280×720 canvas. All times are seconds on the **source PTS clock** unless named `wall_*` or `completed_at` (monotonic seconds).

```python
# contracts/enums.py
class Category(StrEnum):
    # video
    FACE="face"; AADHAAR="aadhaar"; PAN="pan"; UPI_QR="upi_qr"; PLATE="plate"
    SIGNBOARD="signboard"; PIN_CODE="pin_code"; PHONE_NUMBER="phone_number"
    UPI_ID="upi_id"; EMAIL="email"; OTP_SCREEN="otp_screen"
    GAME_ID="game_id"; ROOM_CREDENTIAL="room_credential"; RISKY_TEXT="risky_text"
    # audio
    SPOKEN_PHONE="spoken_phone"; SPOKEN_UPI="spoken_upi"; SPOKEN_ADDRESS="spoken_address"
    SPOKEN_OTHER_PII="spoken_other_pii"; SPOKEN_UNVERIFIED="spoken_unverified"  # failsafe
    # chat
    CHAT_ABUSE="chat_abuse"; CHAT_DOXXING="chat_doxxing"; CHAT_SCAM_LINK="chat_scam_link"
    CHAT_FAKE_UPI="chat_fake_upi"
    # guard
    MUSIC="music"; COPYRIGHT_MATCH="copyright_match"; PROFANITY="profanity"
    # system
    HOLD="hold"; FAILSAFE="failsafe"

class Modality(StrEnum): VIDEO="video"; AUDIO="audio"; CHAT="chat"; GUARD="guard"; SYSTEM="system"
class MaskStyle(StrEnum): BLUR="blur"; PIXELATE="pixelate"; SOLID="solid"
class MaskKind(StrEnum): REGION="region"; FULL_FRAME="full_frame"; MOUTH_OF_SPEAKER="mouth_of_speaker"
class Lane(StrEnum): FAST="fast"; ACCURATE="accurate"; SLOW="slow"
class Device(StrEnum): CPU="cpu"; GPU_OPTIONAL="gpu_optional"; GPU_ONLY="gpu_only"
class ChatAction(StrEnum): ALLOW="allow"; HIDE_FROM_STREAMER="hide"; HOLD_FOR_MOD="hold_for_mod"; DELETE="delete"; TIMEOUT="timeout"
class HoldSource(StrEnum): HOTKEY="hotkey"; API="api"; CHAT="chat"; APP="app"; AUTO_TRANSPORT="auto_transport"; AUTO_EGRESS="auto_egress"
```

```python
# contracts/geometry.py
Point = tuple[float, float]
@dataclass(frozen=True, slots=True)
class Quad:                       # 4 corners, clockwise, canonical pixels
    pts: tuple[Point, Point, Point, Point]
    @staticmethod
    def from_xyxy(x1,y1,x2,y2) -> "Quad": ...
    def bbox(self) -> tuple[int,int,int,int]: ...
    def padded(self, px: float) -> "Quad": ...
    def iou(self, other: "Quad") -> float: ...
```

```python
# contracts/media.py
@dataclass(frozen=True, slots=True)
class Frame:
    seq: int                      # monotonic per session
    pts: float                    # source time, rebased to 0
    arrival: float                # time.monotonic() at decode
    image: np.ndarray             # (720,1280,3) uint8 BGR, read-only; never retained past a call

@dataclass(frozen=True, slots=True)
class AudioChunk:
    seq: int
    pts_start: float
    sample_rate: int              # 16000 for analysis taps
    pcm: np.ndarray               # int16 mono, 20 ms multiples
    arrival: float
```

```python
# contracts/detection.py
@dataclass(frozen=True, slots=True)
class Detection:
    detector: str                          # plugin id
    category: Category
    quad: Quad
    confidence: float                      # 0..1
    frame_seq: int
    pts: float
    completed_at: float                    # monotonic when inference finished
    track_id: int | None = None
    allow: bool = False                    # allowlisted face: must NOT be masked
    aux_quads: Mapping[str, Quad] = {}     # e.g. {"mouth": Quad} from YuNet landmarks
    extra: Mapping[str, float|int|bool|str] = {}   # codes and scores only: never raw text or images

@dataclass(frozen=True, slots=True)
class MaskRequest:
    request_id: str
    source: str                            # detector id, "audio", "hold"
    category: Category
    kind: MaskKind
    quads: tuple[Quad, ...]                # REGION only; anchored at frame_pts
    frame_pts: float                       # PTS the quads were computed on (motion projects from here)
    pts_start: float; pts_end: float       # frames in this range get the mask
    style: MaskStyle
    confidence: float
    reason: str                            # machine code, e.g. "verhoeff_ok"
    completed_at: float
    track_id: int | None = None

@dataclass(frozen=True, slots=True)
class MuteSpan:
    span_id: str
    pts_start: float; pts_end: float       # already padded
    category: Category
    granularity: Literal["word","segment","failsafe"]
    confidence: float
    reason: str                            # code, no transcript text
    completed_at: float
    mode: Literal["silence","bleep"] = "silence"
```

```python
# contracts/chat.py
@dataclass(frozen=True, slots=True)
class ChatMessage:
    platform: Literal["youtube","twitch"]
    message_id: str; channel_id: str
    author_id: str; author_display: str
    text: str                              # transient: never logged if flagged
    received_at: float                     # wall time
    is_broadcaster: bool; is_moderator: bool

@dataclass(frozen=True, slots=True)
class ChatVerdict:
    platform: str; message_id: str
    action: ChatAction
    categories: tuple[Category, ...]
    confidence: float
    reason: str                            # code
    stage: Literal["rules","llm","model"]
    latency_ms: float
    timeout_seconds: int | None = None
    platform_result: Literal["pending","done","unsupported","failed"] = "pending"
```

```python
# contracts/events.py
@dataclass(frozen=True, slots=True)
class SafetyEvent:                          # the only thing the safety report is built from
    event_id: str; session_id: str
    wall_ts: str                            # UTC ISO-8601
    pts: float | None
    modality: Modality
    category: Category
    action: Literal["masked","muted","hidden","held","deleted","timed_out","warned","failsafe"]
    confidence: float
    bbox: Quad | None                       # pixels, video only
    duration_ms: int | None
    source: str; detail_code: str           # never text, images or raw PII

@dataclass(frozen=True, slots=True)
class RiskSignal:
    wall_ts: str; pts: float | None
    source: Literal["music","fingerprint","profanity","risky_text","slur"]
    category: Category
    score: float                            # 0..1
    ttl_s: float                            # signal decays after this
    label: str                              # code, e.g. "fp:demo_03"

@dataclass(frozen=True, slots=True)
class HoldCommand:  source: HoldSource; active: bool; actor: str   # actor is a hash, not a name

@dataclass(frozen=True, slots=True)
class CaptionEvent: pts_start: float; pts_end: float; lang: str; text: str; is_final: bool

@dataclass(frozen=True, slots=True)
class StageTiming:  stage: str; seq: int; pts: float | None; t_start: float; t_end: float  # eval reads these
```

`Config` (pydantic v2 + pydantic-settings; secrets are `SecretStr` and never in `repr`/logs):

```python
class CategoryConfig(BaseModel):  enabled: bool=True; style: MaskStyle=BLUR; sensitivity: float=0.5; mask_when_unsure: bool=True; padding_px: int=12
class DetectConfig(BaseModel):    stride: int=3; fast_hz: float=10; accurate_hz: float=4; accurate_width: int=960
class GpuConfig(BaseModel):       enabled: bool=False; mem_limit_mb: int=3500; ort_provider: Literal["cpu","cuda"]="cpu"
class AudioConfig(BaseModel):     segment_max_s: float=1.5; overlap_s: float=0.4; pad_s: float=0.2; stt_order: list[str]; llm_timeout_s: float=0.7; failsafe_mute_voiced: bool=True; languages: list[str]
class HoldConfig(BaseModel):      retro_seconds: float=3.0; slate_text_hi: str; slate_text_en: str
class Destination(BaseModel):     kind: Literal["youtube","twitch","mediamtx_tap"]; url_host: str; enabled: bool
class Config(BaseSettings):
    profile: Literal["dev","demo"]="dev"
    delay_seconds: float = Field(4.0, ge=3.0, le=5.0)
    fps: int=30; canvas: tuple[int,int]=(1280,720)
    detect: DetectConfig; gpu: GpuConfig
    categories: dict[Category, CategoryConfig]
    audio: AudioConfig; hold: HoldConfig
    chat: ChatConfig; guard: GuardConfig; captions: CaptionsConfig
    destinations: list[Destination]
    # secrets from .env: YOUTUBE_STREAM_KEY, TWITCH_STREAM_KEY, GROQ_API_KEY, SARVAM_API_KEY,
    # YOUTUBE_OAUTH_CLIENT_ID/SECRET, TWITCH_CLIENT_ID/SECRET
```

**Detector plugin interface**

```python
class Detector(Protocol):
    id: ClassVar[str]
    lane: ClassVar[Lane]
    categories: ClassVar[frozenset[Category]]
    device: ClassVar[Device]
    vram_mb: ClassVar[int]                     # declared estimate
    def configure(self, cfg: DetectorConfig, res: Resources) -> None: ...   # res: ORT session factory, allowlist, clock
    def warmup(self) -> None: ...
    def detect(self, frame: Frame, ctx: DetectContext) -> Sequence[Detection]: ...  # single-threaded per lane; do not retain frame
    def close(self) -> None: ...
# discovery: vision/detectors/<id>/__init__.py exports DETECTOR: type[Detector]
class SpeechToText(Protocol):  async def transcribe(self, pcm16k: np.ndarray, pts_start: float, lang_hint: str|None) -> Transcript: ...  # Transcript.words may carry (start,end) or be segment-level
class PiiSpanDetector(Protocol): async def detect(self, window: TranscriptWindow) -> Sequence[PiiSpan]: ...
class ChatClassifier(Protocol):  async def classify(self, batch: Sequence[ChatMessage]) -> Sequence[ChatVerdict]: ...
class EventBus(Protocol):        def publish(self, topic: Topic, event: object) -> None; def subscribe(self, topics: Sequence[Topic]) -> AsyncIterator[tuple[Topic, object]]
```

`contracts/fakes/` holds test doubles: `FakeFrameSource` (synthetic frames with a known SPECIMEN card), `FakeDetector`, `FakeSTT`, `FakeChatClassifier`, `InProcessBus`. Every module tests against these, which is what lets the waves run in parallel. Contract tests (`contracts/tests/`) cover round-trip wire encoding, frozen-ness, enum stability, and a check that no contract field can carry free text apart from `ChatMessage.text` and `CaptionEvent.text`.

### 4.1 Implemented contracts (authoritative)

The code in `contracts/` is authoritative. It follows the sketch above with these differences, all logged in `docs/DECISIONS.md` (D-18 to D-30); `docs/specs/contracts.md` describes the final layout:

- `Frame.image` and `AudioChunk.pcm` are typed `Any`, so `contracts` imports without numpy. They are hot-path only and raise `TypeError` if serialised.
- Added `ConfigChange` (runtime toggles), `Topic` (bus topics), `TOPIC_TYPES` and `WIRE_TYPES` registries, `Transcript`, `TranscriptWord`, `TranscriptWindow`, `PiiSpan`, `AllowlistHandle`, `DetectorConfig`, `Resources` and `DetectContext`.
- `contracts/privacy.py` classifies every `str` field as free text, machine code or opaque id. Codes and ids are validated at construction (no spaces, bounded length). `test_no_free_text.py` fails when a new `str` field is unclassified. `ChatMessage` and `CaptionEvent` never print their text in `repr`.
- Every data contract validates its own invariants (confidence within 0..1, `pts_end >= pts_start`, REGION masks need quads, TIMEOUT needs `timeout_seconds`, and so on).
- Wire codec is strict (`contracts/wire.py`): unknown fields, unknown enum values and bad `Literal` values raise `ValueError`.
- `Config` loads `env > .env > config/profiles/<profile>.yaml > defaults`. The retroactive-hold window is **clamped** to `delay_seconds - 0.5` (`Config.effective_retro_seconds`), not rejected. `Config.delay_warning()` flags delays above 4.3 s.
- `contracts/fakes/` has `InProcessBus`, `FakeDetector`, `FakeFrameSource`, `FakeSTT`, `FakeChatClassifier`.

## 5. Phases and gates

Every gate is a runnable script that exits non-zero on failure and writes a report to `eval/reports/<date>-<phase>.md`. GPU checks run one at a time under `flock /tmp/streamsarthi-gpu.lock`. Numeric targets are **initial**, to be re-based on the Phase 0 baseline and recorded in `docs/DECISIONS.md` if changed.

| Phase | Goal | Gate script | Pass criteria |
|---|---|---|---|
| **0** Environment, contracts, harness skeleton | Everything installs, contracts compile, loopback ingest works, harness measures | `scripts/gate_phase0.sh` | `scripts/doctor.py`: Python 3.11, `torch.cuda.get_arch_list()` has `sm_86` (dev) and `sm_120` (demo) when the optional torch extra is installed, ORT CUDA provider loads, ffmpeg has `libsrt`/`libx264`/`h264_nvenc`, MediaMTX hash OK, libraqm present. Contract tests green. `ruff` clean. `eval/run.py --selftest` OK. Synthetic sender → MediaMTX → reader → null sink for 60 s with 0 drops. Spike report: Sarvam REST timestamps, STT p50/p95 latency |
| **1** End-to-end pipe + one detector | ingest → buffer → compositor → YouTube, with a SPECIMEN Aadhaar blurred live | `scripts/gate_phase1.sh [--live-youtube]` | 120 s run, `--profile dev`, CPU only and with GPU: **delay p95 ≤ 5.0 s** (optical stamp, capture to egress tap), output ≥ 29 fps, SPECIMEN card unreadable (no Verhoeff-valid OCR read of the output after 1 s warm-up, recall ≥ 98 % of card-visible frames), kill-detector test → slate within 1 s of the deadline miss, **0 unmasked frames in the fail-safe test**, hold test (slate within 1 frame, retro window works). `--live-youtube`: a 3 min unlisted YouTube run, viewer-side latency recorded |
| **2** Indian video layer | All video categories, tracking | `scripts/gate_phase2.sh` | Frame-level recall on the eval set (initial targets): faces 95 % (non-allowlisted blurred) with allowlisted faces visible ≥ 95 %, Aadhaar/PAN 95 %, UPI QR 98 %, signboard text 85 %, phone/PIN/UPI ID/email 90 %, OTP screens 85 %, game IDs 90 %, plates 85 %. Mask flicker (on→off→on within 1 s on a static target) ≤ 2 %. With all detectors on: fps ≥ 29, delay unchanged, CPU ≤ 5 cores |
| **3** Hinglish audio | Spoken PII, mute, mouth blur | `scripts/gate_phase3.sh` | Mute coverage ≥ 90 % of PII words (≥ 95 % of digit sequences) inside the delay window. Over-mute ≤ 15 % of non-PII speech seconds. Audio pipeline p95 ≤ 3.3 s. **API-down run**: voiced speech with no verdict is 100 % muted (fail-safe) and local fallback covers ≥ 70 % of PII. Mouth blur on ≥ 95 % of muted-span frames where an allowlisted face is visible |
| **4** Chat shield | Abuse, scams, doxxing, queue, actions | `scripts/gate_phase4.sh` | On the eval chat set: abuse recall ≥ 85 % / precision ≥ 90 %, scam link recall ≥ 95 %, fake UPI ≥ 98 %, doxxing ≥ 85 %, false positives on clean chat ≤ 3 %. Classify p95 ≤ 300 ms. Mock YouTube and Twitch servers: connect, classify, hide, delete and timeout round trip. `!hold` accepted only from broadcaster/mod |
| **5** Strike guard, captions | Risk meter, music, profanity, captions | `scripts/gate_phase5.sh` | Music detect recall ≥ 90 % with false positives ≤ 5 % (music vs speech clips). Fingerprint match ≥ 90 % on noisy 5 s snippets of the demo library. Profanity (Hinglish) recall ≥ 85 %. Risk meter reacts within 2 s. Captions render in all 5 scripts (raqm shaping test) and arrive before display |
| **6** Product | Streamer app, moderator console, safety report | `scripts/gate_phase6.sh` | Web build, lint and typecheck pass. API tests pass. Playwright flows (setup, enrol, toggles, hold, mod queue, report, mobile viewport) pass. Safety report schema test: no images or raw text. A scripted walkthrough measures time from a fresh clone to a stream going live |
| **7** Hardening | Red team, numbers, demo | `scripts/gate_final.sh` | Red-team miss rate per category recorded and published (video: angle, glare, occlusion, motion; audio: fast numbers, accents, noise; chat: misspellings, mixed script). 30 min soak: 0 unmasked frames, memory growth ≤ 10 %. SRT with `tc netem` 4G loss/jitter profile: no hang, slate during drops. Cost per streaming hour computed. `docs/DEMO.md` script rehearsed. Manual item: 3 full runs on the RTX 5050 |

## 6. Task graph

Owners are the agents in `.claude/agents/`. "Lead" tasks are repo-wide files (`pyproject.toml`, `requirements/`, `config/`, `scripts/setup*`, `scripts/doctor.py`, `scripts/fetch_tools.sh`, `contracts/`, `docs/`) that no builder is allowed to touch. Two instances of the same agent type may run in one wave when they own different subdirectories. **No wave shares a file**: tests live in each module's own `tests/`, and detector and subsystem code is split into subpackages, one per task. Each task's spec is its module section in `docs/specs/<module>.md`. If a task needs a change outside its directories, it reports back and the lead creates a follow-up task.

**Step 0 (first execution step after approval, lead):** write `docs/PLAN.md`, `docs/specs/*.md`, `contracts/`, `docs/STATUS.md` (all tasks pending), `docs/THIRD_PARTY.md`, `docs/DECISIONS.md`. Then commit, stop and tell you it's ready.

### Phase 0

| ID | Wave | Owner | Dirs | Depends | Acceptance test |
|---|---|---|---|---|---|
| T0.1 | 0.A | lead | `pyproject.toml`, `requirements/`, `config/`, `scripts/setup_wsl.sh`, `scripts/doctor.py`, `scripts/fetch_tools.sh`, `models/` | Step 0 | `doctor.py` passes on dev. Installs need your OK (see section 9) |
| T0.2 | 0.B | eval-engineer | `eval/` (harness, sender, generators v0), `scripts/gate_phase0.sh` | T0.1 | `eval/run.py --selftest`, sender produces an optical-stamped SRT/RTMP stream |
| T0.3 | 0.B | pipeline-engineer | `relay/` | T0.1, contracts | MediaMTX supervisor, hash-pinned config, loopback reader; ingest tests; 60 s loopback with 0 drops |
| T0.4 | 0.B | pipeline-engineer | `engine/` | contracts | Deadline-engine port, hold control, fail-safe, ported unit tests on synthetic frames (CPU) |
| T0.5 | 0.B | vision-engineer | `vision/` core (SDK, registry, lanes, ORT helper) | contracts | Autodiscovery test with `FakeDetector`. `models/prepare.py` fetches and verifies SHAs. Latency probe |
| T0.6 | 0.B | audio-engineer | `audio/` (VAD, segmenter, STT adapters, spike) | contracts | Spike report: Sarvam timestamps, STT latency p50/p95, Groq limits. `FakeSTT` wiring test. Mock if keys are missing |
| T0.7 | 0.B | product-engineer | `app/api/` skeleton, `app/web/` scaffold | contracts | FastAPI + ZeroMQ bus client, health and WebSocket echo. Next.js 16 / Tailwind 3 builds |

### Phase 1

| ID | Wave | Owner | Dirs | Depends | Acceptance test |
|---|---|---|---|---|---|
| T1.1 | 1 | pipeline-engineer | `engine/` | T0.3, T0.4, T0.5 | Compositor (blur/pixelate/solid, slate), motion projection, retro hold, bus wiring, `StageTiming` logs. Unit tests plus a synthetic end-to-end |
| T1.2 | 1 | pipeline-engineer | `relay/` | T0.3 | Encoder process (x264/NVENC), AAC, egress writers, YouTube and Twitch destinations via TLS forwarder, N destinations, backoff, archive removed. Mock RTMPS sink test |
| T1.3 | 1 | vision-engineer | `vision/detectors/cards/`, `vision/host.py` | T0.5 | Aadhaar (Verhoeff) and PAN detector on the OCR lane, card quad expansion, MaskPlanner. SPECIMEN set recall ≥ 95 % |
| T1.4 | 1 | eval-engineer | `eval/`, `scripts/gate_phase1.sh` | T0.2 | Delay measurement, card-visibility checker, kill-detector and hold tests, soak driver |
| T1.5 | 1 | chat-engineer | `chat/youtube/`, `chat/twitch/` (spike) | T0.1 | OAuth flows, quota governor, mock servers. Early de-risk of chat |
| T1.6 | 1 | product-engineer | `app/api/`, `app/web/` | T0.7 | `/hold`, `/status`, config get/set, WebSocket fan-out, a minimal page with a hold button |

**Gate G1** (`gate_phase1.sh`). The lead integrates, runs the gate, and sends failures back to the owners. Failing twice triggers a re-plan.

### Phase 2

| ID | Wave | Owner | Dirs | Depends | Acceptance test |
|---|---|---|---|---|---|
| T2.1 | 2.A | eval-engineer | `eval/data_gen/video/` | G1 | Generators with augmentation (tilt, glare, blur, occlusion, distance): cards, QR, plates, signboards (Devanagari/Kannada/Tamil/Telugu/Bengali), OTP screens, BGMI/Free Fire overlays. All stamped SPECIMEN, faces from consenting teammates or CC0 sets |
| T2.2 | 2.A | pipeline-engineer | `engine/` | G1 | N lanes with per-lane max age, per-category mask style, per-category fail-safe |
| T2.3 | 2.A | vision-engineer | `vision/tracking/` | G1 | ByteTrack wrapper, hysteresis (adapted), MaskPlanner finalised. Flicker test |
| T2.4 | 2.A | vision-engineer | `vision/detectors/text/` | G1 | Text lane: detect, script routing, patterns (phone, PIN, UPI ID, email, PAN), signboard mask-all |
| T2.5 | 2.A | product-engineer | `app/api/`, `app/web/` | G1 | Category toggles and sensitivity UI and API, face enrolment UI and API, `SafetyEvent` store |
| T2.6 | 2.B | vision-engineer | `vision/detectors/faces/` | T2.3 | YuNet+SFace, allowlist, per-track voting, mouth quad. Allowlisted faces visible ≥ 95 % |
| T2.7 | 2.B | vision-engineer | `vision/detectors/upi_qr/` | T2.3 | QR decode, `upi://` or `pay?pa=` check. Recall ≥ 98 % |
| T2.8 | 2.B | vision-engineer | `vision/detectors/plates/` | T2.4 | OCR-first Indian plate formats, BH series |
| T2.9 | 2.B | vision-engineer | `vision/detectors/screens/` | T2.4 | OTP/notification screen detector, BGMI/Free Fire game-ID and room-credential templates |
| T2.10 | 2.B | eval-engineer | `eval/`, `scripts/gate_phase2.sh` | T2.1 | Per-category precision/recall reports, fps, CPU and delay regression |

### Phase 3

| ID | Wave | Owner | Dirs | Depends | Acceptance test |
|---|---|---|---|---|---|
| T3.1 | 3 | eval-engineer | `eval/data_gen/audio/`, `scripts/gate_phase3.sh` | G2 | TTS-composed Hinglish/Hindi/regional clips (Sarvam TTS, espeak-ng fallback) with numbers, UPI IDs and addresses, **ground-truth timestamps by construction**, noise mixes |
| T3.2 | 3 | audio-engineer | `audio/orchestrator/` | T0.6 | Segmenter, STT race, transcript window, MuteSpan emission, voiced-unverified fail-safe, bus publish |
| T3.3 | 3 | audio-engineer | `audio/pii/` | T0.6 | Number words in Hindi/English/regional, Devanagari digits, "double/triple", spoken UPI, address cues, Groq fallback with token bucket |
| T3.4 | 3 | pipeline-engineer | `engine/` | T2.2 | Sample-accurate mute with 10 ms fade, bleep mode, `MOUTH_OF_SPEAKER` resolution from the face `aux_quads["mouth"]`, retro audio hold, audio fail-safe |
| T3.5 | 3 | product-engineer | `app/api/`, `app/web/` | T2.5 | Live alert feed from `SafetyEvent`, audio toggles, languages setting |

### Phase 4

| ID | Wave | Owner | Dirs | Depends | Acceptance test |
|---|---|---|---|---|---|
| T4.1 | 4 | eval-engineer | `eval/data_gen/chat/`, `eval/mock_platforms/`, `scripts/gate_phase4.sh` | G3 | Hinglish abuse (Latin and Devanagari), scam links, fake UPI, doxxing, clean chat. Mock YouTube/Twitch servers |
| T4.2 | 4 | chat-engineer | `chat/classify/` | contracts | Cascade: rules, scam, dox, Groq batch. p95 ≤ 300 ms |
| T4.3 | 4 | chat-engineer | `chat/youtube/` | T1.5 | `list` polling with governor, delete, timeout |
| T4.4 | 4 | chat-engineer | `chat/twitch/`, `chat/commands/` | T1.5 | EventSub, Helix delete/timeout, `!hold`/`!unhold` role checks |
| T4.5 | 4 | chat-engineer | `chat/pipeline/`, `chat/modqueue/` | T4.2 | Orchestrator, verdict store (no raw flagged text logged), overlay feed |
| T4.6 | 4 | product-engineer | `app/api/`, `app/web/` | T4.5 | Moderator console and streamer overlay that hides flagged messages |

### Phase 5

| ID | Wave | Owner | Dirs | Depends | Acceptance test |
|---|---|---|---|---|---|
| T5.1 | 5 | audio-engineer | `guard/music/` | G3 | YAMNet gate, Chromaprint/audfprint fingerprint against the demo library, library builder script |
| T5.2 | 5 | audio-engineer | `guard/risk/`, `guard/profanity/` | G3 | Hinglish profanity on the transcript, `risky_text` from Detection stream, risk aggregator with decay |
| T5.3 | 5 | audio-engineer | `audio/captions/` | T3.2 | Sarvam translate and local IndicTrans2, `CaptionEvent` timing |
| T5.4 | 5 | pipeline-engineer | `engine/` | T3.4 | Caption renderer (Pillow + raqm, Noto fonts), toggle, placement |
| T5.5 | 5 | eval-engineer | `eval/data_gen/guard/`, `scripts/gate_phase5.sh` | G3 | Music/speech clips, demo song library (fingerprints only, audio stays out of git), profanity set |

### Phase 6

| ID | Wave | Owner | Dirs | Depends | Acceptance test |
|---|---|---|---|---|---|
| T6.1 | 6 | product-engineer | `app/api/` | G5 | Go-live orchestration (validate keys, test destination), safety report generation, risk stream |
| T6.2 | 6 | product-engineer | `app/web/` (streamer routes and shared layout) | G5 | Go-live setup, enrolment polish, toggles, hold, risk meter, alerts, captions toggle. Mobile first |
| T6.3 | 6 | product-engineer | `app/web/` (mod and report routes) | T6.2 layout | Moderator console polish, report timeline. Hindi labels |
| T6.4 | 6 | pipeline-engineer | `relay/` | G5 | SRT tuning for 4G, ingest-drop hold behaviour, reconnect, Moblin pairing QR |
| T6.5 | 6 | eval-engineer | `eval/e2e/`, `scripts/gate_phase6.sh` | T6.1–3 | Playwright flows, report schema test |

### Phase 7 (hardening)

| ID | Wave | Owner | Dirs | Depends | Acceptance test |
|---|---|---|---|---|---|
| T7.1 | 7.A | eval-engineer | `eval/redteam/video/` | G6 | Angle, glare, occlusion, motion suites with miss rates |
| T7.2 | 7.A | eval-engineer | `eval/redteam/audio_chat/` | G6 | Fast numbers, accents, noise, misspellings, mixed scripts |
| T7.3 | 7.A | pipeline-engineer | `engine/`, `relay/` | G6 | Profiling, regression benchmarks, 30 min soak fixes, `netem` 4G run |
| T7.4 | 7.A | lead | `docs/DEMO.md`, `docs/COST.md` | G6 | Demo script and cost per streaming hour |
| T7.5 | 7.B | vision / audio / chat engineers | their own dirs | T7.1–T7.2 | Fix the misses found. Targets re-run |
| T7.6 | 7.B | eval-engineer | `scripts/gate_final.sh`, `eval/reports/` | T7.5 | Final numbers into `docs/STATUS.md` |

After every wave: reviewer agent, full CPU test suite, the phase gate, then one commit "`<phase>.<wave>: <summary>`" and a `docs/STATUS.md` update (only the lead commits).

## 7. Specs (one file per module under `docs/specs/`)

Compact versions below. The files will carry the same fields with full test lists.

### `contracts` (and config)
- **Inputs/outputs:** none at runtime. It defines every type above.
- **Behaviour:** frozen types, stable enums, wire round-trip, `Config` loads `config/profiles/<profile>.yaml` plus env.
- **Edge cases:** unknown enum value on the wire fails loudly. `delay_seconds` outside 3.0–5.0 is rejected. Secrets never appear in `repr`.
- **Tests:** round-trip, immutability, enum snapshot, "no free-text field" lint, profile merge.
- **Latency/VRAM:** n/a.

### `engine/`
- **Inputs:** `Frame` stream, `AudioChunk` ring, `MaskRequest`, `MuteSpan`, `CaptionEvent`, `HoldCommand`, `Config`.
- **Outputs:** composited frames plus processed audio to the encoder; `SafetyEvent` (masked, muted, held, failsafe); `StageTiming`.
- **Behaviour:** the deadline engine, fail-safe, hold with retro window, mask painting (blur/pixelate/solid, polygon crop), motion projection, mute with 10 ms fades, mouth masking, caption burn-in, slate.
- **Edge cases:** input gap, late frame, fps change (30 assumed but not hard-coded), non-16:9 letterbox, detector that crashes (lane marked stale so PROTECT), clock jump, simultaneous holds from several sources, hold released while buffered frames are queued (fence).
- **Tests:** ported IRL Shield deadline/control/lanes/motion/render tests, synthetic frames with a known card, A/V sync test with beep and stamp, retro-hold test, fail-safe test.
- **Latency:** ≤ 30 ms compositor per frame. **VRAM:** none (CPU compositor).

### `relay/`
- **Inputs:** SRT (default 8890) and RTMP (1935) from OBS or phone via MediaMTX. **Outputs:** decoded frames and audio to the engine, encoded streams to YouTube, Twitch and the loopback tap.
- **Behaviour:** MediaMTX supervisor (hash-pinned v1.21.4), PyAV reader with reconnect generations, killable spawned encoder, TLS forwarder per destination, N destinations from one encode, backoff, transport HOLD during ingest drop.
- **Edge cases:** SRT jitter and loss, ingest restart, destination rejects the key, YouTube not live yet, encoder crash, phone rotates to portrait.
- **Tests:** mock RTMPS sink, killed-encoder recovery, 60 s loopback with 0 drops, `netem` loss (Phase 7).
- **Latency:** ingest ≤ 0.25 s LAN, encode+egress ≤ 0.2 s. **VRAM:** NVENC ≈ 0.15 GB, or 0 on x264.

### `vision/`
- **Inputs:** `Frame`s per lane, `Config`, the enrolled allowlist. **Outputs:** `Detection` and `MaskRequest` (via the MaskPlanner), plus the declared `vram_mb`.
- **Behaviour (per detector subpackage):**

| Subpackage | What it does | Notes |
|---|---|---|
| `faces` | YuNet → SFace match against allowlist, per-track voting, `aux_quads["mouth"]` | bias toward blurring; enrolled faces `allow=True` |
| `cards` | OCR find 12-digit, Verhoeff, PAN regex, expand to the card quad (contour search, else a padded region) | mask the whole card, never only the digits |
| `upi_qr` | decode QR, mask if `upi://` or `pay?pa=` | also mask undecodable QR-like codes when `mask_when_unsure` |
| `plates` | OCR-first: plate regex incl. BH series, vehicle gate | optional YOLO weights behind an extra |
| `text` | PIN, phone, UPI ID, email, PAN patterns on OCR text, Devanagari digits, signboard mask-all | detection-only for signs |
| `screens` | OTP and notification screen detector, BGMI/Free Fire ID and room-credential templates | fixed regions per game |
| `tracking` | ByteTrack + hysteresis + MaskPlanner | per-track state, K_confirm/K_hold |

- **Edge cases:** tilted, blurred or distant cards, glare, partly covered cards, many faces, tiny text, rapid motion, non-Latin digits, a lane that runs late (planner marks stale so PROTECT).
- **Tests:** CPU-only unit tests per detector on SPECIMEN data from `eval/`, plus plugin-autodiscovery and lane tests.
- **Latency:** FAST ≤ 25 ms, ACCURATE ≤ 450 ms CPU. **VRAM:** `dev` ≤ 0.8 GB for vision, `demo` ≤ 2.0 GB.

### `audio/`
- **Inputs:** 16 kHz `AudioChunk`s. **Outputs:** `MuteSpan`, `MaskRequest(MOUTH_OF_SPEAKER)`, `CaptionEvent`, `SafetyEvent`, `StageTiming`.
- **Behaviour:** Silero VAD, 1.5 s segmenter with 0.4 s overlap, STT race, transcript window, PII rules, Groq fallback, voiced-unverified fail-safe, caption translation.
- **Edge cases:** number split across a segment boundary, "double/triple", mixed Hindi/English numbers, API timeout or 429, API outage (local only), language switch, crosstalk, music under speech.
- **Tests:** TTS clips with ground-truth timestamps, rules table tests, injected API failures, rate-limit behaviour.
- **Latency:** see the 3.3 s audio budget. **VRAM:** 0 in `dev`, ≤ 1.3 GB in `demo` (local STT + IndicTrans2).

### `guard/`
- **Inputs:** audio tap, transcript stream, `Detection` stream. **Outputs:** `RiskSignal`, `SafetyEvent`.
- **Behaviour:** music detection, fingerprint match against the demo library, Hinglish profanity, risky on-screen text, a risk aggregator with decay and warning thresholds.
- **Edge cases:** music under speech, short snippets, library songs with different tempo, a profanity list false-positive on benign words.
- **Tests:** music/speech clips, fingerprint with noise, profanity set. We never claim Content ID parity.
- **Latency:** score updates ≤ 2 s. **VRAM:** 0 (CPU).

### `chat/`
- **Inputs:** YouTube and Twitch chat. **Outputs:** `ChatVerdict`, `HoldCommand` (from `!hold`), `SafetyEvent`, mod-queue items.
- **Behaviour:** connectors, cascade classifier, actions (hide on our side, delete, timeout), quota governor, role-checked commands, Groq batching.
- **Edge cases:** 429 and quota exhaustion, OAuth expiry, duplicate messages, edit/delete races, emoji or mixed-script text, a command from a non-mod, platform that lacks a given action.
- **Tests:** synthetic chat from `eval/`, mock platforms, latency probe, "no raw flagged message in logs" test.
- **Latency:** ≤ 300 ms p95. **VRAM:** 0.

### `app/api/` and `app/web/`
- **API inputs:** browser/mobile requests, bus events. **Outputs:** WebSocket stream (`SafetyEvent`, `RiskSignal`, `ChatVerdict`, `CaptionEvent`), config changes, `HoldCommand`, safety report.
- **Behaviour:** FastAPI + WebSockets, HMAC session tokens (PrivaStream `auth.py`), Host/Origin checks and a strict CSP (IRL Shield patterns), enrolment, mod actions, report generation.
- **Web:** Next.js 16, Tailwind 3, shadcn components. Screens: go-live setup, enrolment, category toggles, hold button, risk meter, alert feed, captions toggle, moderator console, report timeline. Mobile first. English UI with Hindi labels.
- **Edge cases:** engine down, WebSocket reconnect, two clients pressing hold, camera permission denied, small screens.
- **Tests:** API tests, `next build`, lint and typecheck, Playwright, report schema (no images, no raw text).
- **Privacy:** the UI never shows or stores raw PII or images.

### `eval/`
- **Outputs:** SPECIMEN data (marked SPECIMEN, Verhoeff-valid fake numbers), the synthetic sender, accuracy and latency harness, red-team suites, mock platforms, reports in `eval/reports/<date>-<phase>.md`.
- **Behaviour:** reads `StageTiming` JSONL, computes per-category precision/recall, mute coverage, delay (optical stamp), fps, VRAM, chat latency. Wraps GPU runs in `flock`.
- **Tests:** harness self-test, seed-reproducible generators.
- **Data rules:** no real personal data. Faces only from consenting teammates (gitignored) or CC0 sets. Demo song audio stays out of git (fingerprints only).

### `scripts/` and ops (lead-owned except `gate_*` and `run_*`)
`setup_wsl.sh`, `doctor.py`, `fetch_tools.sh`, `hold_hotkey_win.py`, `run_engine` (pipeline), `gate_*` (eval). Requirements split into `requirements/base.txt`, `gpu.txt`, `extras-torch.txt`, `extras-indic-ocr.txt`, with the pin list from the research (onnxruntime-gpu 1.26.0, torch 2.11.0 cu128 as optional, opencv-contrib-python-headless 4.13, numpy 2.2.6, rapidocr 3.10.0, av 16.0.1, silero-vad 6.2.3, faster-whisper 1.2.1, sarvamai, groq, twitchAPI 4.5.0, fastapi, uvicorn, pyzmq, supervision, zxing-cpp, pyacoustid, pydantic 2).

## 8. Top 10 risks and fallbacks

| # | Risk | Fallback |
|---|---|---|
| R1 | Python GIL or CPU load breaks 720p30 with all lanes on | Lanes already drop work, never frames. Reduce accurate-lane width, raise stride, run lanes in a process pool with shared memory (the host interface already allows it), enable GPU OCR |
| R2 | Audio can't make the delay window (STT too slow, no word timestamps) | Race Sarvam against local, segment-level mutes, mute voiced-unverified (fail-safe), raise D toward 4.3 s, shrink segment cap to 1.0 s |
| R3 | API outage or rate limit (Sarvam, Groq 20 RPM, 30 RPM LLM) | Token buckets, local faster-whisper spotting, rules only, batch chat, cache |
| R4 | ORT or CUDA on the RTX 5050 (JIT for sm_120, cuDNN mismatch, slow first run) | CPU path is the default and works anywhere. Test on the 5050 in Phase 0 and by wave 2. Pin ORT 1.26, avoid 1.23.x, keep the JIT cache. Demo can run CPU-only |
| R5 | Missed small, distant or tilted text and cards (IRL Shield failed its own held-out gate) | Mask-all-text on signs, card-level quads, tracking, retroactive hold, `mask_when_unsure`, honest published recall |
| R6 | Face allowlist leaks (a stranger unmasked) or masks the streamer | Per-track voting (not shared), bias toward blurring, multi-angle enrolment, threshold tuning on eval |
| R7 | YouTube chat quota, OAuth consent or no gRPC client | Polling governor, hide on our side, read-only fallback, mock servers prove the logic before live |
| R8 | Phone SRT over WSL2 or 4G is unstable (mirrored networking, firewall, jitter) | Mirrored mode plus a Hyper-V firewall rule, Tailscale, bigger SRT latency, OBS desktop mode as the backup, `netem` tests |
| R9 | Plates: no permissive weights, OCR-only misses | OCR-first plus vehicle gate, optional AGPL YOLO if weights exist, Colab fine-tune later |
| R10 | Indic OCR coverage gaps (no Kannada or Bengali recogniser) and Indic caption rendering | Mask-all-text on signs, optional EasyOCR on crops, libraqm and Noto fonts, shaping test in Gate 5 |

Also tracked: licence hygiene (AGPL and non-commercial items marked), hackathon rules on pre-built code and AI disclosure (yours to confirm), YouTube ingest rejecting the stream, and "live demo fails" (recorded backup plus local OBS demo).

## 9. What I need from you

**Accounts and keys (go in `.env`, which I never read or print):**
- YouTube: live streaming enabled on the channel (first activation can take up to 24 h), a stream key, and a Google Cloud OAuth client (Desktop app type, YouTube Data API v3 on, consent screen with your account as a test user).
- Twitch: developer app (client ID, secret if needed) and a stream key.
- `GROQ_API_KEY` and `SARVAM_API_KEY` (Sarvam gives ₹100 credit to start).
- A Hugging Face account and token with the IndicTrans2 model terms accepted (demo captions only).
- A second YouTube or Twitch account or device to post test chat.

**Machine setup (needs your admin rights, I will not do these myself):**
- `%UserProfile%\.wslconfig` with `[wsl2] networkingMode=mirrored`, then `wsl --shutdown`.
- Windows Hyper-V firewall rule for inbound UDP 8890 (SRT) and TCP 1935 (RTMP): `New-NetFirewallHyperVRule … -Protocol UDP/TCP`.
- `sudo apt install libraqm0` (Indic caption shaping) and Python 3.11 via `uv`.
- Approval for Phase 0 installs: venv and pip packages, MediaMTX download (hash-pinned), model downloads, `pyzmq`. I will not install anything until you say go.
- Windows-side Python for the hotkey helper.
- Tailscale on the laptop and phone if you stream over 4G.

**Phone:** Moblin (iPhone) or Larix Broadcaster (Android). I generate a pairing QR.

**Demo hardware:** the RTX 5050 laptop at least a week before the demo, with the same WSL2 setup, and 3 full rehearsals.

**Content only you can supply:**
- 5 short Bollywood clips for the demo fingerprint library (stay local, never committed).
- Consenting teammates for face enrolment and eval photos.
- 2–3 real, sourced incident reports for the problem slide (never invented).
- Whether the hackathon allows pre-built code and requires AI-tool disclosure.

## 10. Decisions confirmed by approval

The user approved the plan as written ("Proceed", 2026-11-20). That settles the three open items as follows (logged as D-1, D-23 and D-24 in `docs/DECISIONS.md`):

1. **Delay definition (D-1):** capture to egress socket, p95 <= 5.0 s. Viewer-side YouTube latency is recorded but not gated.
2. **Retroactive hold:** kept, default 3.0 s, clamped to `delay_seconds - 0.5`.
3. **Installs:** Phase 0 installs (venv, pip packages, MediaMTX download, model downloads, `pyzmq`) are covered by this approval but happen only when task T0.1 runs. System-level changes (`.wslconfig`, Hyper-V firewall rule, `apt`) need the user's admin rights and are never done by an agent.

## Verification (how we will know it works)

- Phase 0: `scripts/gate_phase0.sh`. Phase 1: `scripts/gate_phase1.sh --live-youtube`. Each phase gate as in section 5. The final run is `scripts/gate_final.sh`.
- At each wave: reviewer agent, `pytest -q` on CPU, the phase gate, then one commit and a `docs/STATUS.md` update with the numbers.
- Final proof: the eval report in `eval/reports/`, the red-team miss rates, the measured delay, and three full runs on the RTX 5050.
