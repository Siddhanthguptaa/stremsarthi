# Reused code: what's here, why, and what's missing

This folder holds trimmed copies of two open-source projects. Only the code Stream Sarthi needs was copied, and every file keeps its original path inside its project folder, so imports still work. **Never edit these files.** Copy what you need into the Stream Sarthi module folders (`engine/`, `relay/`, `vision/`, `audio/`, `chat/`, `guard/`, `app/`, `eval/`), and record each reused file in `docs/THIRD_PARTY.md`.

| Project | Upstream | Commit copied | Licence | Files copied |
|---|---|---|---|---|
| PrivaStream (TikTok TechJam 2025, 1st place) | https://github.com/Saximn/privastream | `fe27fe743dfdcd655589cb609000216fab4c2635` (2026-06-20) | Apache-2.0 (see `privastream/LICENSE`). Its setup.py says "MIT" in its classifiers, but the LICENSE file is Apache-2.0, so treat it as Apache-2.0. | 150 |
| IRL Shield (Codex × TwitchCon 2026, 3rd of 501) | https://github.com/elannash/irl-shield | `e7afb2c51df98614c8c4966827c47d658acf6ad8` (2026-11-15) | MIT (see `irl-shield/LICENSE`). Bundled fonts and models keep their own licences (`irl-shield/models/licenses/`). | 229 |

The copy was taken from `Desktop\sathack26\privastream` and `Desktop\sathack26\irl-shield`. All copied Python files parse cleanly.

---

## privastream/: what each part is for

| Path | What it is | Feeds Stream Sarthi module |
|---|---|---|
| `src/models/detection/face_blur/` | Face detection with InsightFace `buffalo_l`, an allowlist based on face embeddings (`enroll_face.py`), and a stream runner | `vision/`: faces and allowlist |
| `src/models/detection/plate_blur/` | Ultralytics YOLO number plate detector (can export to .engine or .onnx) and a blur runner | `vision/`: number plates (retrain for Indian plates) |
| `src/models/detection/pii_blur/` | On-screen text PII: EasyOCR (English only) plus a scikit-learn classifier, `pii_clf.joblib` (included, 1.6 MB) | `vision/`: text PII (add Indian patterns and OCR for Indian scripts) |
| `src/models/detection/unified_detector.py`, `blur_utils.py`, `export_yolo.py`, tests and demos | Runs all detectors in one pass, blur helpers, FPS tests | `vision/` and the `engine/` compositor |
| `src/models/utils/` | Shared blur utilities | `engine/` |
| `src/models/audio/audio_redaction_server_faster_whisper.py` | A Flask service: faster-whisper (`base`) speech-to-text, then `dslim/bert-base-NER` (English) to find PII and redact audio | `audio/`: replace the English NER with Hinglish and Indian-language PII detection |
| `src/models/audio/training/` | DeBERTa PII training pipeline (BiLSTM, multi-dropout, distillation), `whisper_processor.py`, `livestream_pipeline.py`, configs | `audio/`: retrain the PII model on Hinglish data |
| `src/models/audio/` (other files) | Benchmark, GPU check, GPU monitor, faster-whisper install helper | `audio/` and `eval/` |
| `src/core/`, `src/configs/`, `src/cli/` | Model config, logging, exceptions, CLI | Patterns only |
| `src/web/backend/` | Flask backend: rooms, auth, video filter API | `app/api/`: port to FastAPI |
| `src/web/frontend/` | Next.js and Tailwind app with face enrolment UI (camera capture, face preview), host and viewer pages, socket and WebRTC clients | `app/web/`: reuse the enrolment UI and components |
| `src/web/mediasoup/` | WebRTC SFU and audio-redaction processors with a sliding window (`SLIDING_WINDOW_EXPLANATION.md`) | Reference for how to time audio mutes. Stream Sarthi uses an RTMP/SRT relay, not WebRTC. |
| `benchmarks/ml_latency_harness.py` | Latency harness for the models | `eval/` |
| `docs/` | ARCHITECTURE, API, ML_PRODUCTION_PLAN, IMPROVEMENTS, DEVELOPMENT | Background reading |
| `tests/`, `src/tests/` | Backend and model tests | Reference |

**Not copied, and why:**
- `.git`, `.github`, Dockerfiles and `docker-compose.yml`: the Docker base image is `python:3.10-slim` with no CUDA.
- `.next/` build cache, `tsconfig.tsbuildinfo`, `pnpm-lock.yaml`: build output and a duplicate lock file.
- `src/web/backend/video.log`: a 19 MB runtime log.
- `src/models/audio/input.wav` and `output_redacted.wav`: recordings of a real voice.
- `src/models/detection/face_blur/whitelist/creator_embedding.json`: **the original creator's face embedding (biometric data).** Enrol your own faces with `enroll_face.py`.
- Windows `.bat` installers, CHANGELOG, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, `src/tools/migrate_to_shared_models.py`.

---

## irl-shield/: what each part is for

| Path | What it is | Feeds Stream Sarthi module |
|---|---|---|
| `relay/mediamtx.py`, `bridge.py`, `stream.py`, `media.py`, `runtime.py`, `live.py`, `run.py` | Ingest through MediaMTX (RTMP from the phone app, e.g. Moblin over Tailscale), stream handling, the relay runtime | `relay/` |
| `relay/egress.py`, `tls_forwarder.py`, `security.py`, `seal.py`, `archive.py` | Output to Twitch over TLS with certificate checks, security, run sealing and archiving | `relay/`: add YouTube output |
| `relay/synthetic.py`, `fixture.py` | Synthetic inputs for tests | `eval/` |
| `shield/engine.py`, `lanes.py`, `motion.py`, `masking.py`, `render.py`, `types.py` | The processing engine: GPU buffer and delay, detector lanes (fast 8 Hz and accurate 4 Hz), motion tracking, masking, rendering | `engine/` |
| `shield/control.py` | Hold and control state | `engine/`: hold |
| `shield/presentation/` | Renders the privacy slate shown during hold, with its font (OFL licence) | `engine/`: hold screen |
| `shield/skins/` | Mask styles (bar, patch) and viewer voting on them in chat | `engine/` (optional) |
| `shield/dashboard/` | Phone dashboard server with pairing, snapshot and a Hold button | `app/` (merge into the streamer app) |
| `shield/twitch/` | Twitch OAuth, EventSub events, transport, storage; chat commands `!shield hold` and `!skin` | `chat/`: Twitch connector and commands |
| `shield/app/`, `shield/cli.py` | Live session orchestration and the CLI (`run`, `login --run`) | `engine/` and `scripts/` |
| `bench/onnx_detectors.py`, `detector_options.py`, `static_model.py`, `vision.py`, `pipeline.py`, `reference_engine.py`, `metrics.py`, `scoring.py`, `independence.py`, `provenance.py`, `scheduled.py` | ONNX text detectors (PP-OCRv5 mobile through RapidOCR models), detector options, metrics and scoring. The live engine imports these. | `vision/` and `eval/` |
| `models/prepare.py`, `manifest.json`, `licenses/` | Downloads pinned PP-OCRv5 ONNX models and checks their SHA-256; licence records | Model setup for `vision/` |
| `tools/demo_renderer/` | Sends a synthetic street scene as a rehearsal or demo source | `eval/`: demo source |
| `scripts/walk-linux` | The main attended run on Linux (see `docs/RUN.md`) | `scripts/` |
| `scripts/setup`, `setup-check`, `m2-tools`, `m2-setup-linux`, `host-lock`, `bounded.py` | Environment setup; downloads the pinned MediaMTX v1.21.3; host lock | `scripts/` |
| `tests/` | 70 tests: relay, engine, dashboard, Twitch, skins, live defaults | Reference tests |
| `docs/` | RUN (how to run it), ARCHITECTURE, DASHBOARD, MASK-RUNTIME, LIVE-EGRESS, LIVE-GUARD, LIVE-SESSION-RUNBOOK, SKINS, WALK-LINUX, FUTURE-WORK, Q-FRESH-HELDOUT-RESULT, `research/STACK-OPTIONS.md` (68 KB stack research), `images/generated-before-after.png` | Background reading |
| `bench/DETECTORS.md`, `bench/PORTABILITY.md` | Detector comparison; portability and licence notes | Background reading |
| `fixtures/fonts/`, `fixtures/demo-renderer/fictional-street.png` | Fonts and the demo scene used by the renderer | `eval/` |

**Not copied, and why:**
- `.git`.
- `AGENTS.md`: instructions written for the original authors' Codex agent. They would conflict with our `CLAUDE.md`.
- `.infisical.json`: configuration for their secrets manager.
- `Sources/VisionBridge/`: a macOS-only Swift bridge to Apple Vision.
- About 60 experiment harnesses and result files in `bench/`.
- `docs/model/`: TLA+ proofs and evidence.
- Held-out fixture manifests: metadata of third-party videos.
- 18 tests that depend on bench code that wasn't copied.

---

## Known issues to handle during the build

1. **Twitch-only output, locked to one account.** IRL Shield publishes only to Twitch, and its live publishing is restricted to the original broadcaster account (`naie`). Register your own Twitch app, remove that restriction, and add YouTube RTMPS output.
2. **CUDA version pin.** IRL Shield pins `onnxruntime-gpu==1.20.2` with CUDA 12.4 and cuDNN 9.3, chosen for an older Pascal GPU. That works on the development laptop's RTX 3050 (Ampere), but the demo laptop's RTX 5050 (Blackwell, sm_120) needs CUDA 12.8 or newer. Use one onnxruntime-gpu build for CUDA 12.8+ on both laptops, and test the GPU path on both early.
3. **PyTorch version.** PrivaStream asks for `torch>=1.12` without pinning. Install the cu128 build first, since it supports both the RTX 3050 (sm_86) and the RTX 5050 (sm_120) (`pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128`), then the rest of its requirements.
4. **Missing model weights.** PrivaStream's config points to `face_blur/face_best.pt` and `plate_blur/best.pt`, which aren't in the repository: its docs say they download automatically, but the URL is a placeholder. Check the project's GitHub releases or its Docker image (`ghcr.io/saximn/privastream`). Otherwise, train a plate detector, which you need for Indian plates anyway. InsightFace `buffalo_l` downloads itself on first use.
5. **Licences that need replacing before any commercial use.** PrivaStream uses Ultralytics (AGPL-3.0) and InsightFace, whose weights are for non-commercial use only. They're fine for the hackathon. For a product, replace them with permissively licensed options such as RT-DETR or YuNet/SFace.
6. **English only.** PrivaStream's OCR is English-only EasyOCR, and its audio NER is English (`dslim/bert-base-NER`). Add OCR for Indian scripts and Hinglish PII detection.
7. **Python version.** IRL Shield uses Python 3.11; PrivaStream's Docker image used 3.10. Standardise on 3.11.
8. **Two different stacks.** PrivaStream runs on Flask with a mediasoup WebRTC server. Stream Sarthi uses an RTMP/SRT relay (from IRL Shield) and a FastAPI backend.
9. **Small text.** IRL Shield's README says its detectors miss some text, mostly small or distant text. That's why hold matters, and why Stream Sarthi adds tracking and card-level masking.
10. **No audio redaction in IRL Shield.** Audio comes from PrivaStream's pipeline. Offleak, the other TwitchCon winner, also doesn't redact audio, and its code is AGPL, so never copy from it.
