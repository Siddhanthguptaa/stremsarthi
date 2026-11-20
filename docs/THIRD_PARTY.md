# Third-party code, models and licences

Rules (CLAUDE.md): copy code from `reference/` into the module directories, never edit `reference/`, keep upstream LICENSE/NOTICE files next to copied code, and record every reused file here with its source path, licence and what changed. Never copy code from Offleak (AGPL-3.0).

Status: **no code has been copied yet.** The "Reused files" table lists the planned reuse from `docs/PLAN.md` section 1 so builders and the reviewer know what to expect. A builder changes a row's status to `copied` in its report and the lead updates this file after the wave.

## Licence notices to keep

| Upstream | Licence | Files to keep with copied code |
|---|---|---|
| PrivaStream (commit `fe27fe743dfdcd655589cb609000216fab4c2635`) | Apache-2.0 | `reference/privastream/LICENSE` (copy to `THIRD_PARTY_LICENSES/privastream/LICENSE`). No NOTICE file exists upstream. Its `setup.py` says "MIT"; the LICENSE file is Apache-2.0, so treat it as Apache-2.0. Add a per-file header "Adapted from Saximn/privastream, Apache-2.0" |
| IRL Shield (commit `e7afb2c51df98614c8c4966827c47d658acf6ad8`) | MIT | `reference/irl-shield/LICENSE` and `models/licenses/` (RapidOCR and PaddleOCR Apache-2.0 texts, model licences, dependency licences) copied to `THIRD_PARTY_LICENSES/irl-shield/`. Atkinson Hyperlegible font keeps its `OFL.txt` |
| Offleak | AGPL-3.0 | **Never copied.** Ideas only |

## Reused files (planned)

| Source path (under `reference/`) | Licence | Target | Planned change | Status |
|---|---|---|---|---|
| `irl-shield/shield/engine.py` | MIT | `engine/deadline.py` | N lanes, per-lane max age, mute spans, retroactive hold | planned |
| `irl-shield/shield/control.py` | MIT | `engine/control.py` | none beyond imports | planned |
| `irl-shield/shield/motion.py` | MIT | `engine/motion.py` | none beyond imports | planned |
| `irl-shield/shield/render.py` | MIT | `engine/compositor.py` | add blur and pixelate | planned |
| `irl-shield/shield/presentation/*` (+ OFL font) | MIT, OFL-1.1 | `engine/slate.py` | Hindi/English copy, panic slate | planned |
| `irl-shield/shield/lanes.py` | MIT | `vision/lanes.py` | configurable rate and stride | planned |
| `irl-shield/relay/live.py` | MIT | `engine/session.py` | add mute-span application | planned |
| `irl-shield/relay/stream.py` | MIT | `relay/ingest.py`, `relay/encoder.py` | lift 2-destination cap, remove archive writes, configurable fps | planned |
| `irl-shield/relay/egress.py` | MIT | `relay/egress.py` | none | planned |
| `irl-shield/relay/tls_forwarder.py` | MIT | `relay/tls_forwarder.py` | one instance per destination | planned |
| `irl-shield/relay/mediamtx.py`, `security.py` | MIT | `relay/mediamtx.py`, `relay/security.py` | replace `/proc` audit, drop Twitch/Tailscale coupling, MediaMTX 1.21.4 | planned |
| `irl-shield/relay/synthetic.py`, `fixture.py` | MIT | `eval/sender/` | optical stamp and beep sender | planned |
| `irl-shield/bench/onnx_detectors.py`, `static_model.py`, `detector_options.py` | MIT (bundled models Apache-2.0) | `vision/ort.py`, `vision/ort_static.py`, `vision/options.py` | move out of bench, multi-script rec | planned |
| `irl-shield/models/prepare.py`, `manifest.json` | MIT | `models/prepare.py`, `models/manifest.json` | add YuNet, SFace, QR, OTP and plate models | planned |
| `irl-shield/scripts/bounded.py`, `m2-tools` | MIT | `scripts/bounded.py`, `scripts/fetch_tools.sh` | MediaMTX hash-pinned fetch | planned |
| `irl-shield/shield/dashboard/*` (patterns), `pairing.py` | MIT | `app/api/` | FastAPI port; Moblin pairing QR | planned |
| `irl-shield/shield/twitch/auth.py`, `http.py`, `storage.py`, `events.py` | MIT | `chat/twitch/` | new scopes, optional connection-loss hold | planned |
| `privastream/src/models/detection/face_blur/enroll_face.py`, `face_detector.py` (ideas) | Apache-2.0 | `vision/detectors/faces/` | rewrite on YuNet+SFace, per-track voting | planned |
| `privastream/src/models/detection/pii_blur/pii_detector.py` (`Hysteresis`) | Apache-2.0 | `vision/tracking/hysteresis.py` | boxes to tracks | planned |
| `privastream/src/models/detection/blur_utils.py` | Apache-2.0 | `engine/blur.py` | crop polygon mask to the ROI | planned |
| `privastream/src/models/audio/audio_redaction_server_faster_whisper.py`, `training/src/whisper_processor.py` | Apache-2.0 | `audio/stt/local.py` | padding, real char-to-word mapping, no Flask | planned |
| `privastream/src/models/audio/training/src/pii_detector.py` (deterministic part) | Apache-2.0 | `audio/pii/rules.py` | drop fake time estimate | planned |
| `privastream/src/web/backend/auth.py` | Apache-2.0 | `app/api/auth.py` | none | planned |
| `privastream/src/web/backend/video_api_utils.py` | Apache-2.0 | `engine/backpressure.py` | none | planned |
| `privastream/src/web/frontend/components/ui/*`, `lib/utils.ts`, `components.json`, `tailwind.config.js`, `globals.css` | Apache-2.0 (shadcn components MIT) | `app/web/` | none | planned |
| `privastream/src/web/frontend/components/enrollment/*`, `app/enrollment/page.tsx` | Apache-2.0 | `app/web/` | fewer frames per POST, FastAPI endpoint | planned |
| `privastream/src/web/frontend/public/pcm-downsampler-worklet.js` | Apache-2.0 | `app/web/public/` | none | planned |
| `privastream/benchmarks/ml_latency_harness.py` | Apache-2.0 | `eval/microbench.py` | none | planned |
| `privastream/src/models/audio/check_gpu.py` | Apache-2.0 | `scripts/doctor.py` | extend | planned |

Deliberately **not** reused: IRL Shield `relay/archive.py`, `seal.py` (store video), `shield/app/live.py` (broadcaster lock), skins, legacy Mac relay, Pascal-era requirements; PrivaStream plate code, `pii_clf.joblib` (pickle: never load), mediasoup stack, Flask video API, training pipeline.

## Dependencies and models: licence flags

| Item | Licence | Where used | Flag |
|---|---|---|---|
| Ultralytics YOLO (+ any weights trained with it) | **AGPL-3.0** | optional plate adapter (extra only) | **replace before commercial release**; not on the default path |
| InsightFace buffalo_s / buffalo_l weights | **non-commercial research only** | optional face adapter (extra only) | **replace before commercial release**; code is MIT |
| PyAV Linux wheel (bundles FFmpeg and x264) | LGPL/GPL components | ingest/encode | note for any redistribution; we do not redistribute binaries in v1 |
| YuNet 2023mar, SFace 2021dec (OpenCV Zoo) | MIT, Apache-2.0 | `vision/detectors/faces/` | none |
| PP-OCRv5/v6 ONNX via RapidOCR | Apache-2.0 | `vision/detectors/text/` | none; SHA-pinned in `models/manifest.json` |
| EasyOCR | Apache-2.0 | optional Kannada/Bengali crops | pulls torch (extra only) |
| IRL Shield FLV muxer / loopback-to-TLS design (MIT) | MIT | `relay/muxer.py`, `relay/destinations.py`, `relay/tls_forwarder.py` | reworked: muxer now runs in a killable spawned worker over a socketpair; licence at `relay/LICENSE`. `relay/sinkfault.py` is new code |
| Silero VAD | MIT | `audio/` | none |
| PrivaStream `src/models/audio/training/src/pii_detector.py` (Luhn check, overlap-merge idea) | Apache-2.0 | `audio/pii/merge.py`, `audio/pii/rules.py` | adapted, not copied whole; no DeBERTa, no training; licence at `audio/pii/LICENSE-privastream-Apache-2.0` |
| faster-whisper / CTranslate2 | MIT | local STT fallback | none |
| YAMNet | Apache-2.0 | `guard/music/` | none |
| Chromaprint / pyacoustid | LGPL-2.1 / MIT | fingerprinting | dynamic use only |
| audfprint | MIT | optional fingerprint backend | none |
| IndicTrans2 dist-200M | MIT | demo captions | gated on Hugging Face (needs token) |
| supervision (ByteTrack) | MIT | `vision/tracking/` | none |
| zxing-cpp | Apache-2.0 | `vision/detectors/upi_qr/` | none |
| MediaMTX | MIT | ingest | hash-pinned download |
| Noto Sans (Devanagari, Kannada, Tamil, Telugu, Bengali) | OFL-1.1 | caption rendering | keep OFL texts |
| Atkinson Hyperlegible Next | OFL-1.1 | hold slate | keep `OFL.txt` |
| Sarvam API, Groq API | commercial APIs | audio, chat, captions | terms apply; audio chunks and chat text only, nothing stored |
| Dejavu | MIT | not used | needs a database, looks unmaintained |
| Offleak | AGPL-3.0 | not used | never copy |

Demo song audio and any face images used for evaluation stay out of git (`.gitignore`); only fingerprints and generated SPECIMEN data are committed.

## Reuse log (actual, from builder reports)

Copied code keeps its upstream licence file beside it. "Changed" is what the builder reported.

| Source (under `reference/`) | Licence | Target | Changed | Task |
|---|---|---|---|---|
| `irl-shield/shield/engine.py` | MIT | `engine/deadline.py` | N lanes, `LaneResult`, `HoldSource` holds, retro hold, fence, reason codes, PTS-reset restart | T0.4 |
| `irl-shield/shield/control.py` | MIT | `engine/control.py` | `HoldSource` keys, public `snapshot()`, retro windows, bounded ledger | T0.4 |
| `irl-shield/shield/motion.py` | MIT | `engine/motion.py` | reformatted, `reset()`, `IdentityMotion` | T0.4 |
| `privastream/.../video_api_utils.py` | Apache-2.0 | `engine/backpressure.py` | kept `now_ms`, `is_stale_request`, `RequestTracker`, `clamp_region`; added `try_start` | T0.4 |
| `irl-shield/shield/render.py` | MIT | `engine/compositor.py`, `engine/blur.py` | BLUR/PIXELATE/SOLID, ROI crops, feather, collapse rule | T1.1 |
| `privastream/.../detection/blur_utils.py` | Apache-2.0 | `engine/blur.py` | polygon mask cropped to ROI | T1.1 |
| `irl-shield/shield/presentation/renderer.py` + Atkinson font (OFL) | MIT, OFL-1.1 | `engine/slate.py`, `engine/assets/` | English and Hindi slates, raqm shaping | T1.1 |
| `irl-shield/relay/live.py` | MIT | `engine/session.py` | audio ring, mute book, bus wiring | T1.1 |
| Noto Sans Devanagari (google/fonts, fetched 2026-11-20) | OFL-1.1 | `engine/assets/` | none; SHA-256 in `engine/assets/NOTICE.md` | T1.1 |
| `irl-shield/shield/lanes.py` | MIT | `vision/lanes.py` | plugin detectors, stride or rate, `LaneResult` | T0.5 |
| `irl-shield/bench/onnx_detectors.py` (`_session`) | MIT | `vision/ort.py` | CoreML removed, arena limit from config, preload_dlls | T0.5 |
| `irl-shield/bench/static_model.py`, `detector_options.py` | MIT | `vision/ort_static.py`, `vision/options.py` | type hints, `Lane` | T0.5 |
| `irl-shield/models/prepare.py`, `manifest.json` | MIT | `models/prepare.py`, `models/manifest.json` | rewritten with profiles, https only, retries, traversal guard; IRL entries kept | T0.5 |
| `irl-shield/bench/onnx_detectors.py` (`PPOCRDetector`, `PPOCRRecognizer`) | MIT | `vision/ocr/det.py`, `vision/ocr/latin.py` | session from factory, static shape, scores | T1.3 |
| `privastream/.../pii_blur/pii_detector.py` (`Hysteresis` idea) | Apache-2.0 | `vision/planner.py` (`IouHoldTracker`) | idea only | T1.3 |
| `privastream/.../audio_redaction_server_faster_whisper.py`, `training/src/whisper_processor.py` | Apache-2.0 | `audio/stt/local.py` | no Flask/BERT/librosa; word times shifted by PTS | T0.6 |
| Silero VAD v6.2.3 ONNX (downloaded, SHA-256 pinned) | MIT | `models/weights/audio/` | none | T0.6 |
| `privastream/benchmarks/ml_latency_harness.py` | Apache-2.0 | `eval/microbench.py` | type hints, header | T0.2 |
| `privastream/src/web/backend/auth.py` | Apache-2.0 | `app/api/auth.py` | secret passed in, `TokenService` with roles, fixed room for hotkey tokens | T0.7 / T1.6 |
| `privastream/src/web/frontend/components/ui/*`, `lib/utils.ts`, `components.json`, `postcss.config.js`, `public/pcm-downsampler-worklet.js` | Apache-2.0 | `app/web/` | header only | T0.7 |
| `privastream/.../next.config.js`, `tailwind.config.js`, `eslint.config.mjs`, `globals.css` | Apache-2.0 | `app/web/` | mediasoup and socket.io removed, brand tokens, security headers | T0.7 |
| `irl-shield/shield/twitch/auth.py`, `storage.py`, `events.py`, `http.py`, `Secret` | MIT | `chat/twitch/`, `chat/common/` | new scopes, `/dev/tty` prompt removed, shield/hold logic removed, routes as parameters | T1.5 |
| `irl-shield/shield/twitch/transport.py` | MIT | `chat/twitch/transport.py` | rewrite on aiohttp (ideas only) | T1.5 |
| ByteTrack algorithm (MIT) and `privastream` `Hysteresis` idea (Apache-2.0) | MIT, Apache-2.0 | `vision/tracking/` | reimplemented in numpy/scipy, no code copied | T2.3 |
| `privastream/src/web/frontend/components/enrollment/cameracapture.tsx`, `facepreview.tsx` | Apache-2.0 | `app/web/components/enrollment/camera-capture.tsx`, `face-card.tsx` | timed JPEG burst, guidance, denied state, no stored photos; avatar row without thumbnail | T2.5 |
| `irl-shield/relay/mediamtx.py` | MIT | `relay/mediamtx.py` | kept stdin config and log classifier; dropped /proc audit and archive; added supervisor, v1.21.4 pin | T0.3 |
| `irl-shield/relay/stream.py` (`NetworkInput`) | MIT | `relay/ingest.py` | kept reconnect generations and drop-oldest queue; added letterbox, PTS rebase, audio blocks, transport hold | T0.3 |
| `irl-shield/relay/security.py` | MIT | `relay/security.py` | dropped Tailscale-only rule and Twitch coupling | T0.3 |

Fonts, models and licence files added by builders: `engine/assets/` (Atkinson Hyperlegible Next, Noto Sans Devanagari, OFL texts), `models/licenses/` (IRL Shield MIT, PaddleOCR, RapidOCR, YuNet MIT, SFace Apache-2.0), `models/manifest.json` (SHA-256 pinned YuNet, SFace, PP-OCR v5/v6 detectors, en/devanagari/ta/te recognisers), `audio/stt/LICENSE-privastream-Apache-2.0`, `eval/LICENSES/`, `app/api/LICENSE.privastream`, `app/web/LICENSE.privastream`, `chat/twitch/LICENSE.irl-shield`, `vision/LICENSE.*`.
