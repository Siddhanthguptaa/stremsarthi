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
| Silero VAD | MIT | `audio/` | none |
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
