# Stream Sarthi

A real-time privacy and safety layer for Indian live streamers. It sits between the streamer (OBS on a desktop, or a phone streaming app) and YouTube or Twitch. It removes private information from video, audio and chat before viewers see it.

## Goal

A fully functional platform: every feature below works live, end to end, on the target hardware.

## Read before planning or building

- `STREAM_SARTHI.md`: the full project brief. It covers the idea, features, the Indian layer, what judges will ask, the architecture, hardware notes, build plans and risks.
- `reference/REUSE_MAP.md`: what was copied from PrivaStream and IRL Shield, what each part is for, what was left out, and known issues such as missing model weights and the onnxruntime CUDA pin.

## Features (full scope)

1. **Relay and ingest.** OBS (RTMP/SRT) and phone apps (SRT/RTMP) go into the engine, then out to YouTube and Twitch over RTMPS. A 3–5 second broadcast delay buffer keeps audio and video in sync. Fail-safe: any frame not yet checked goes out masked. Hold (panic) can be triggered from a hotkey, a chat command or the app.
2. **Video PII.**
   - Faces, with an allowlist of enrolled faces that stay visible
   - Aadhaar and PAN cards
   - UPI QR codes (decode, then check for `upi://`)
   - Indian number plates
   - Text in signboards in Indian scripts
   - PIN codes, phone numbers, UPI IDs and emails in on-screen text
   - Phone screens showing OTPs or notifications
   - BGMI and Free Fire player IDs, custom-room IDs and passwords
   - Tracking, so masks don't flicker
3. **Audio PII.**
   - Voice activity detection, then streaming speech-to-text through an API (with a local fallback) in Hinglish, Hindi, Kannada, Tamil, Telugu and Bengali
   - PII detection, including numbers spoken as Hindi words, UPI IDs and addresses
   - Mute or bleep inside the delay window, with mouth blur synced to the mute
4. **Chat shield.**
   - YouTube Live Chat and Twitch chat
   - Detects Hinglish and Indian-language abuse, doxxing, scam links and fake UPI payment links
   - Hides flagged messages from the streamer's overlay and sends them to a moderator queue
   - Takes platform actions (delete, timeout) where the platform's API allows
5. **Strike guard.**
   - Detects music playing, and matches it by fingerprint against a demo library
   - Detects profanity in speech and risky on-screen content
   - Shows a live risk meter with warnings
6. **Live captions** in Indian languages as an overlay the streamer can switch on or off. Use the Sarvam translation API, or IndicTrans2 locally in the `demo` profile.
7. **Streamer app** (web, works on mobile).
   - Go-live setup, face allowlist enrolment, an on/off toggle for each category, the hold button, the live risk meter and alerts
   - Moderator console
   - Safety report after each stream: a timeline of events with category, timestamp and confidence. It never contains images or raw PII.

## Repo layout

```
reference/privastream/   # READ-ONLY. Apache-2.0. Trimmed copy: detectors, audio PII, web app
reference/irl-shield/    # READ-ONLY. MIT. Trimmed copy: relay, engine, hold, dashboard, Twitch chat
reference/REUSE_MAP.md   # what was copied, why, and known issues
contracts/               # shared schemas and interfaces. Written first. Lead owns.
engine/                  # frame loop, delay buffer, A/V sync, mask compositor, encode
relay/                   # ingest (MediaMTX, SRT/RTMP), outputs (YouTube, Twitch)
vision/                  # all video detectors and tracking
audio/                   # VAD, ASR, audio PII, mute spans, captions
guard/                   # strike guard: music, fingerprint, profanity, risk meter
chat/                    # chat connectors, classifiers, actions, mod queue
app/api/                 # FastAPI backend + WebSockets
app/web/                 # Next.js + Tailwind (TypeScript) streamer app and mod console
eval/                    # synthetic data, test clips, accuracy and latency harness, red-team suites
scripts/                 # setup, run, gate checks
docs/                    # PLAN.md, STATUS.md, specs/, DECISIONS.md, THIRD_PARTY.md
```

## Rules

### Reuse and licences
- Copy code from `reference/` into the module directories. Never edit anything inside `reference/`.
- Keep the upstream LICENSE and NOTICE files next to copied code. Record every reused file in `docs/THIRD_PARTY.md`: source path, licence, and what changed.
- Never copy code from Offleak (AGPL-3.0).
- Ultralytics YOLO (AGPL-3.0) and InsightFace weights (non-commercial use only) are allowed for now. Mark them "replace before commercial release" in `docs/THIRD_PARTY.md`.

### Contracts and ownership
- Modules talk to each other only through `contracts/`. Only the lead changes a contract, and logs the reason in `docs/DECISIONS.md`.
- Each subagent edits only the directories listed in its definition. If it needs a change elsewhere, it reports that back instead of making the change.
- Only the lead commits.

### Machines and approach (API-first, video on the CPU first)
- **Development laptop:** Intel Core i5 (13th gen) with an NVIDIA RTX 3050 laptop GPU (4 GB or 6 GB; Ampere, sm_86).
- **Demo laptop:** RTX 5050 laptop GPU (8 GB; Blackwell, sm_120).
- Both run Windows 11 with WSL2 Ubuntu 24.04, Python 3.11 and Node 20+.
- **Build for the development laptop's limits.** Everything must work on the RTX 3050. The demo laptop may turn on extras only through a config profile (`dev` and `demo`), never through code changes.
- **Video stays on the machine.** Video detection runs locally because every frame must be checked and frames must never leave the machine.
  - Every video component needs a CPU path that runs in real time at its configured rate, plus a CPU-only test.
  - The GPU is optional acceleration.
- **Audio and chat go through APIs:**
  - speech-to-text: Sarvam, or Groq's Whisper
  - unclear spoken-PII decisions and chat classification: Groq
  - caption translation: Sarvam

  Every API call is async with a timeout and has a local fallback (rules; faster-whisper tiny or base on the CPU). API calls must never block the frame path. Only audio chunks and chat text are sent out, and we store nothing.
- **No model training in v1.** Use rules, pre-trained models and LLM APIs. Any later fine-tuning runs on Colab or Kaggle, never on these laptops.
- **One Python stack for both laptops.** PyTorch and onnxruntime-gpu builds for CUDA 12.8+ (cu128) support both sm_86 and sm_120.
  - Check with `python -c "import torch; print(torch.cuda.get_arch_list())"`; the list must include `sm_86` and `sm_120`.
  - Upgrade IRL Shield's pinned onnxruntime-gpu (built for CUDA 12.4) to a CUDA 12.8+ build.
- **GPU memory budget:** 3.5 GB in total in the `dev` profile (fits a 4 GB RTX 3050); up to 7 GB in the `demo` profile.
- **Models:** YuNet and SFace (or InsightFace buffalo_s) for faces, PP-OCR mobile (ONNX) for text, nano detectors at 640 px input. No large local models.
- **Video target:** 720p at 30 fps in. Detect every 3rd frame and let tracking fill the frames in between (the `demo` profile may detect every 2nd frame). End-to-end delay of 5 seconds or less, including API round trips.
- **Encoding:** x264 (veryfast preset) on the CPU by default; NVENC when it's available.
- **One GPU job at a time.** Wrap every GPU command: `flock /tmp/streamsarthi-gpu.lock <command>`. Unit tests run on the CPU with mocks.
- **This section wins.** The agent files in `.claude/agents/` were written for an earlier plan with an 8 GB GPU. Where they mention VRAM, NVENC, GPU decoding or local speech-to-text, follow this section.

### Data and privacy
- Video frames never leave the machine. Only audio chunks and chat text go to APIs.
- Never use real Aadhaar, PAN or bank cards, or anyone's real personal data. Generate synthetic data marked SPECIMEN in `eval/`.
- Never store images or raw PII at runtime. Event logs keep category, timestamp, confidence and bounding box only.
- Secrets live only in `.env` (see `.env.example`). Never print, log or commit them.

### Quality
- Every module has pytest unit tests that run on the CPU, contract tests, and an entry in the eval harness.
- A task is done when:
  - its tests pass
  - its acceptance test passes
  - it meets its latency and VRAM budget
  - the reviewer agent has approved it
  - its numbers are recorded in `docs/STATUS.md`
- Python 3.11 with type hints, linted with ruff. Backend uses FastAPI. Frontend uses Next.js, Tailwind and TypeScript.

### Workflow
- `docs/PLAN.md` is the source of truth. `docs/STATUS.md` tracks progress, and the lead updates it after each wave.
- Ask the user only when an action can't be undone, a credential is missing, or a gate fails twice.
