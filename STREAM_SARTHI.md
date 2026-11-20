# Stream Sarthi: complete project brief

This document holds everything decided about Stream Sarthi, so the project can be built without the original chat. The name was earlier written "Stream Sathi"; both refer to the same project.

- **Project folder:** `C:\Users\siddh\Desktop\sathack26\stremsarthi`
- **Brief written:** 20 November 2026

**Files in this folder:**
- `STREAM_SARTHI.md`: this brief
- `CLAUDE.md`: rules for Claude Code
- `PROMPTS.md`: the build runbook and prompts
- `.claude/`: subagent definitions and settings (it started as `claude-setup/` and has been renamed)
- `reference/`: code copied from PrivaStream and IRL Shield
- `reference/REUSE_MAP.md`: what was copied and why

---

## 1. In one line

**Stream Sarthi protects Indian streamers while they're live.** It hides private information from viewers, keeps abusive chat off the streamer's screen, and warns them before something gets the stream struck.

---

## 2. Context: how we got here

### Hackathon setup
- The team is entering a hackathon whose form offers these tracks: Animal Welfare & Rescue, Sports & Fitness Technology, **Content Creation & Streaming**, AI & Cybersecurity, Accessibility & Assistive Technology, Hardware & IoT, and Open Innovation.
- **Chosen track: Content Creation & Streaming.** Open Innovation is the fallback.

### Constraints set at the start
- Software only (the team can't do hardware).
- The idea must be backed by real hackathon winners, not invented from scratch.
- Look at hackathons with prize pools of at least ₹2 lakh, focused on India.
- Last year's winning project, **Nudge**, can't be resubmitted.
  - Nudge: an AI system that turns content into conversations. Features: video generator, flashcards and quizzes, communication coaching, AI scoring, a dashboard.
  - Nudge's stack: FastAPI, MoviePy, FFmpeg, TensorFlow, Next.js, Tailwind, Framer Motion, Groq (Llama 3.1), ElevenLabs.

### Evidence that this kind of idea wins
| Project | Result | What it does |
|---|---|---|
| PrivaStream | TikTok TechJam 2025, 1st place overall | Real-time PII blurring in video (faces, plates, text) and audio (spoken PII) |
| Offleak | Codex × TwitchCon 2026 winner | An OBS filter that blurs strangers' faces, QR codes and phone numbers |
| IRL Shield | Codex × TwitchCon 2026, 3rd of 501 projects (2,217 participants) | A phone-to-Twitch relay that masks location text in IRL streams; hold button |
| Privify | TikTok TechJam 2025, 4th | Privacy protection for streams |

**Pattern:** live-stream privacy protection is the most consistently rewarded idea in the streaming space.

### Gaps found (no existing product)
- A live privacy guard built for India: Indian IDs, UPI, Hinglish speech
- Live dubbing into Indian languages during a stream
- Moderation of Hinglish live chat
- An AI co-host for Indian mobile games (Streamlabs' agent only supports Fortnite)

### Already common (avoid as the main idea)
- Auto-clipping and dubbing of uploaded videos: StreamYard AI Clips, Eklipse, OpusClip, Meta AI Reels translation, YouTube auto-dubbing, Sarvam Dub
- Twitch Stream Coach, which gives AI feedback after every stream
- YouTube won't monetise "generic or repetitive" AI content, and streamers pushed back against AI at TwitchCon 2026

### Alternatives considered and why they lost
| Option | Verdict | Why |
|---|---|---|
| **Stream Sarthi** | **Chosen** | Highest originality (no India-specific version exists), fits the team's video skills, strongest live demo, less crowded track |
| BimaSathi, extended (crop insurance claims by voice on WhatsApp) | Strong backup (see appendix) | Best impact story, but Open Innovation is crowded and crop insurance bots are common |
| A Content Room-style creator platform (AWS AI for Bharat media track winner) | Lowest odds | 11 stages that would each be shallow; mostly features existing tools already have; looks like a copy; too close to Nudge |
| PrivaStream as it is | Not allowed | It already won TechJam; we only use it as a base |

### Ranking
1. Stream Sarthi
2. BimaSathi, extended
3. A Content Room-style platform

**Choose Content Room-style instead only if** the hackathon is 24–36 hours with no prep time allowed, nobody on the team has done real-time video or OpenCV work, or there's no access to a GPU.

---

## 3. The problem

- Indian streamers leak private information live without noticing: an Aadhaar or PAN card on the desk, a UPI QR code on screen, a house number or shop signboard on an IRL walk, a phone number said out loud in Hinglish, an OTP notification on a phone screen, a BGMI custom-room password.
- **One frame is enough to dox someone.** Once it's live, it can't be taken back.
- Chat is full of Hinglish abuse that English-based filters miss. Women streamers in India face heavy harassment.
- Chat is also full of scam UPI links and fake giveaways aimed at viewers.
- Copyrighted Bollywood music playing in the background gets streams struck or muted.

---

## 4. The solution

Stream Sarthi sits between the streamer and the platform, with a 3–5 second broadcast delay, and cleans the stream before anyone sees it:
- **Video:** blurs IDs, QR codes, plates, signboards, OTP screens, game IDs and bystanders' faces, while the streamer and enrolled friends stay visible.
- **Audio:** mutes spoken phone numbers, UPI IDs and addresses in Hinglish and other Indian languages.
- **Chat:** hides abuse, doxxing and scam links from the streamer, and sends them to moderators.
- **Strike guard:** warns before music, profanity or risky content gets the stream struck.
- **Safety report** after each stream.

**Pitch line:** "Stream Sarthi protects Indian streamers while they're live. It hides your private information from viewers, keeps abusive chat off your screen, and warns you before something gets your stream struck."

**Who it's for:**
- Indian IRL streamers
- Mobile-gaming streamers (BGMI, Free Fire) on YouTube Live and other Indian platforms
- Creators who stream from home or vlog live
- Paid tier: creator agencies (MCNs), esports organisations and Indian streaming platforms, as a safety layer they can build in

---

## 5. Full feature list

### Core: privacy, from PrivaStream, Offleak and IRL Shield
1. **Relay and ingest:**
   - OBS (RTMP/SRT) or a phone app (Moblin on iPhone, Larix Broadcaster on Android) goes into the engine, then out to YouTube and Twitch.
   - A 3–5 second delay buffer keeps audio and video in sync.
   - **Fail-safe:** any frame not yet checked goes out masked.
2. **Hold (panic button):** instantly covers the whole screen with a privacy slate. Triggered from a hotkey, a phone tap, a chat command (broadcaster or moderators) or the app.
3. **Faces:** blurs everyone except an **allowlist** of the streamer and enrolled friends (allowlist idea from Offleak and PrivaStream).
4. **Video PII:**
   - Aadhaar and PAN cards
   - UPI QR codes
   - Indian number plates
   - Text on signboards in Indian scripts (location text, from IRL Shield)
   - PIN codes, phone numbers, UPI IDs and emails in on-screen text
5. **Spoken PII:** mutes phone numbers, UPI IDs and addresses spoken in Hinglish or regional languages, with mouth blur synced to the mute (from PrivaStream).
6. **Tracking:** masks stay on moving objects and don't flicker.

### Taken from Content Room and adapted for live streams
7. **Chat shield** (from Content Room's moderation and mental-health layers):
   - Catches toxic, abusive and doxxing messages in Hinglish and regional languages.
   - Hides them from the streamer's screen so they never see the abuse.
   - Sends them to a moderator queue for human review.
8. **Strike guard** (from Content Room's shadowban prediction, made live): a risk meter that warns before something gets the stream struck, muted or demonetised, such as copyrighted Bollywood music, banned on-screen content or a slur picked up by the mic.
9. **Safety report after the stream** (from Content Room's privacy-first and asset-explosion ideas):
   - A timeline of everything blurred, muted or hidden.
   - Clips export only from the cleaned version, so a highlight can't leak.

### Extras for India
10. **BGMI and Free Fire:** blurs player IDs and custom-room IDs and passwords, to stop stream sniping.
11. **Scam shield for viewers:** fake UPI payment links, phishing links and fake giveaways in chat.
12. **Phone screens showing OTPs or notifications:** blurred.
13. **Five or more Indian languages** in chat and speech: Hindi and Hinglish, Kannada, Tamil, Telugu, Bengali.
14. **Patchy networks:** the relay adapts quality on weak 4G connections (SRT input).
15. **Live captions in Indian languages** (IndicTrans2), switchable.

### Product
16. **Streamer app** (web, works on mobile):
    - go-live setup, face enrolment, an on/off toggle per category
    - a big hold button, the live risk meter, alerts
    - captions toggle, moderator console, safety report
17. **Landing page and one-command Docker install.**

### Deliberately left out
Content Room's creator studio, trend injector, competitor intelligence, calendar, scheduling and multi-platform posting. Existing tools already do these, and they have nothing to do with live safety.

---

## 6. The Indian layer, and how to make judges see it

**What makes it Indian:**
- **IDs:** Aadhaar, PAN, UPI QR codes and UPI IDs, Indian number plates (including the BH series), 6-digit PIN codes.
- **Scripts:** OCR for signboards in Devanagari, Kannada, Tamil, Telugu and Bengali.
- **Hinglish speech:** spoken numbers, including Hindi number words (ek, do, teen … nau, sau, hazaar).
- **Hinglish chat:** Hindi written in English letters, which English-first filters miss.
- **Bollywood music** in the strike guard.
- **Phone-first:** the mobile relay mode, since most Indians stream from phones.
- **The law:** blurring bystanders and storing nothing fits India's DPDP Act.
- **Games:** BGMI and Free Fire IDs. **Scams:** UPI scams in chat.

**Make it visible:** demo with a BGMI stream, or an IRL walk past Hindi signboards, with Hinglish chat on screen. Judges should see India within the first 10 seconds.

---

## 7. How each feature is built

### Fast version, no training (enough for a 24–48 hour build)
| Feature | Method |
|---|---|
| Aadhaar | OCR finds 12-digit numbers. Validate with the **Verhoeff checksum** that Aadhaar uses (cuts false positives), then blur the whole card area. |
| PAN | OCR plus the pattern `[A-Z]{5}[0-9]{4}[A-Z]` |
| UPI QR | Decode QR codes (OpenCV or zxing). If the payload starts with `upi://` or contains `pay?pa=`, blur it. |
| Indian plates | PrivaStream's plate model, plus OCR checked against Indian plate formats |
| Signboards and PIN codes | Pre-trained EasyOCR or PaddleOCR for Indian scripts, plus a 6-digit PIN code pattern |
| Phone numbers, UPI IDs, emails | Patterns on the OCR text (`name@bank` for UPI IDs) |
| Faces | PrivaStream's face allowlist (InsightFace embeddings) |
| Spoken PII in Hinglish | Whisper or Sarvam for speech-to-text, then rules that map Hindi number words to digits, then Groq as a fallback for unclear cases |
| Chat shield | Groq classifies Hinglish messages, a keyword list catches the rest, and link patterns catch scam UPI links |
| Strike guard | YAMNet detects that music is playing; Dejavu fingerprint-matches against about 5 demo songs; a profanity list runs on the transcript |
| Game IDs | OCR on the fixed screen regions where BGMI and Free Fire show IDs |
| Safety report | An event log and a timeline page |
| Relay and hold | IRL Shield's relay, with its output changed to YouTube |

### Full version (optional, once there's time to train; train on Colab or Kaggle, never on the laptops)
- **Card detector:** fine-tune on synthetic SPECIMEN Aadhaar and PAN cards, so it catches cards that are tilted, blurry or far away.
- **Plate model:** fine-tune on Indian plate datasets.
- **Chat:** fine-tune **MuRIL** on Hinglish abuse datasets. It's faster and cheaper than calling an LLM for every message, and works offline. Keep the LLM as a fallback for unclear cases.
- **Spoken PII:** retrain PrivaStream's DeBERTa PII model on Hinglish data (its training pipeline is in `reference/privastream/src/models/audio/training/`).
- **Tracking:** ByteTrack.
- **OTP screens:** detect phone screens and notification banners.

### Rules to keep in mind
- **Fail safe:** when the model isn't confident, blur anyway.
- **Audio delay:** spoken PII can only be muted if audio is delayed. The broadcast delay (3–5 seconds, audio and video in sync) must be longer than the time it takes to transcribe and detect.

---

## 8. Architecture

```
OBS (desktop) ─┐
               ├─ SRT/RTMP ─► INGEST (MediaMTX)
Phone app     ─┘                  │
                                  ▼
               DECODE (GPU) ─► DELAY BUFFER (3–5 s, A/V kept in sync)
                                  │
         ┌────────────────────────┼─────────────────────────┐
         ▼                        ▼                         ▼
   VIDEO DETECTORS          AUDIO PIPELINE             CHAT SHIELD
   faces · cards · QR       VAD → ASR → PII model      YouTube/Twitch chat
   plates · signs · OCR     → mute / mouth blur        → rules → MuRIL → LLM
   game IDs · tracking                                 → hide / hold / remove
         └──────────┬─────────────┘                         │
                    ▼                                       │
             MASK COMPOSITOR ◄── HOLD (panic) ◄── STREAMER APP / MOD CONSOLE
                    ▼                                       ▲
             ENCODE (NVENC) ─► YouTube · Twitch             │
                    │                                       │
                    └──► EVENT LOG ─► SAFETY REPORT · STRIKE GUARD · RISK METER
```

### Two ways in
- **Desktop:** OBS streams to the local Stream Sarthi engine. One pipeline serves both modes, so no OBS plugin is needed at first.
- **Phone:** the phone app streams over SRT to a cloud or laptop relay, which cleans the stream and forwards it to YouTube or Twitch.

### Module directories (see `CLAUDE.md`)
| Directory | Contents |
|---|---|
| `contracts/` | Shared schemas |
| `engine/` | Frame loop, delay buffer, compositor, encode |
| `relay/` | Ingest and outputs |
| `vision/` | Video detectors |
| `audio/` | Speech-to-text, spoken PII, captions |
| `guard/` | Strike guard |
| `chat/` | Chat shield |
| `app/api/` | FastAPI backend |
| `app/web/` | Next.js app |
| `eval/` | Test data and harness |
| `scripts/` | Setup, run and gate checks |
| `docs/` | Plan, status, specs, decisions, third-party list |

### Contracts to define first
- Data types: Frame, AudioChunk, Detection, MaskRequest, MuteSpan, ChatMessage, ChatVerdict, SafetyEvent, RiskSignal, Config
- The detector plugin interface

### Privacy design
- Processing happens on the device or in the relay's memory.
- Nothing is stored except event logs (category, timestamp, confidence, bounding box), never images or raw PII.

---

## 9. Reused code and building blocks

The code copied from PrivaStream and IRL Shield is in `reference/`. **`reference/REUSE_MAP.md` gives the full file-by-file map, what was left out, and known issues.**

### What to take from each source
| Source | Take | Licence |
|---|---|---|
| **PrivaStream** (base engine) | Face blur with allowlist, plate detection (97.62% mAP50 reported), text PII (OCR plus classifier), spoken PII (Faster Whisper, Silero VAD, DeBERTa at 96.99% reported), mouth blur synced to audio, Next.js app with face enrolment | Apache-2.0 |
| **IRL Shield** (relay) | Phone → RTMP → GPU relay with a buffer → NVENC → secure output; fail-safe; hold from chat or phone; dashboard; Twitch EventSub chat commands; PP-OCRv5 text masking | MIT |
| **Offleak** | **Reference only.** Its ideas (crew allowlist, QR and phone number blur, lookahead buffer, OBS plugin in C) can be borrowed, but **never its code** | AGPL-3.0 |

### Other building blocks
| Purpose | Building block | Licence |
|---|---|---|
| Translation into 22 Indian languages, for captions | IndicTrans2: https://github.com/AI4Bharat/IndicTrans2 | MIT |
| Real-time speech-to-text | WhisperLiveKit: https://github.com/QuentinFuxa/WhisperLiveKit | Apache-2.0 |
| Live transcription server | WhisperLive: https://github.com/collabora/WhisperLive | MIT |
| Hinglish hate-speech bot (starting point) | https://github.com/AindriyaBarua/anti-hate-reddit-Hinglish-bot-with-PRAW-and-BERT | Apache-2.0 |
| Toxicity detection in Hindi-English video (code and datasets) | ToxVidLM: https://github.com/justaguyalways/ToxVidLM_ACL_2024 | MIT |
| Hinglish abuse classifier | MuRIL (google/muril-base-cased) | Apache-2.0 |
| Music detection and audio fingerprinting | YAMNet; Dejavu | Apache-2.0; MIT |
| Ingest server | MediaMTX | MIT |
| Tracking | ByteTrack | MIT |
| OCR | PaddleOCR, EasyOCR | Apache-2.0 |
| Face detection with permissive licences (for later) | YuNet and SFace (OpenCV Zoo) | Permissive |
| Detector with a permissive licence (for later) | RT-DETR | Apache-2.0 |
| Speech in Indian languages; LLM fallback | Sarvam API; Groq | Paid APIs |
| Video dubbing, dubbing and subtitles (studied only) | SoniTranslate (Apache-2.0); pyvideotrans (GPL-3.0) | — |
| Clipping, if a post-stream clip feature is wanted | AI-Youtube-Shorts-Generator (MIT); ClipsAI (MIT) | MIT |

---

## 10. Machines and approach: API-first, video on the CPU first

### The two laptops
| | Development laptop | Demo laptop |
|---|---|---|
| CPU | Intel Core i5, 13th gen | (whatever it comes with) |
| GPU | RTX 3050 laptop: 4 GB (2,048 CUDA cores, 128-bit) or 6 GB (2023 refresh, 2,560 cores, 96-bit). Ampere, sm_86. Check with `nvidia-smi`. | RTX 5050 laptop: 8 GB GDDR7, 2,560 CUDA cores, up to 115 W. Blackwell, sm_120. |
| Role | All development and testing | Demo-day machine, with extras switched on |

**Rule:** build everything to work on the RTX 3050. The demo laptop only switches extras on through a config profile (`dev` and `demo`), never through code changes.

### Why most of it goes through API keys
| On the laptop (CPU first, GPU optional) | Through API keys |
|---|---|
| Relay and ingest (MediaMTX), delay buffer, hold | Speech-to-text in Hinglish and Indian languages (Sarvam, or Groq's Whisper) |
| Face blur (YuNet and SFace, or InsightFace buffalo_s) | Spoken-PII decisions for unclear cases (Groq LLM) |
| Aadhaar and PAN (OCR plus Verhoeff checksum and pattern) | Chat shield: abuse, scams, doxxing (Groq) |
| UPI QR (decode and check `upi://`) | Captions in Indian languages (Sarvam translation) |
| Plates, signboards, game IDs (PP-OCR mobile, nano detectors) | Music identification for the strike guard (a paid service, if you want real matching) |
| Tracking, compositing, encoding (x264 on the CPU, or NVENC) | |

**Why video can't go through an API:** every frame has to be checked before it goes out.
- **Too slow:** each frame would need a round trip over the internet, at 30 frames a second.
- **Too expensive and rate-limited:** about 108,000 frames per streaming hour.
- **It defeats the point:** you'd be uploading raw frames showing Aadhaar cards to a third party.

The video models are small and run on a 13th-gen i5's CPU (IRL Shield already has a CPU mode at 720p and 30 fps). **The GPU only adds speed and headroom.**

**API rules:**
- Every call is async, with a timeout and a local fallback (rules; faster-whisper tiny or base on the CPU).
- Calls never block the frame path.
- They add a second or two, which has to fit inside the 4–5 second delay. Measure this on your connection.
- They cost money per streaming hour. Check Sarvam's and Groq's pricing; free tiers usually cover development.

**No model training in v1:** rules, pre-trained models and LLM APIs only.

### Setup (both laptops)
- Windows 11 with **WSL2 and Ubuntu 24.04**. NVIDIA driver 570+ on Windows. WSL2 uses the Windows driver, so never install a Linux NVIDIA driver inside WSL.
- **One stack for both GPUs:** PyTorch 2.7+ built with CUDA 12.8 (cu128) supports both sm_86 (RTX 3050) and sm_120 (RTX 5050).
  ```bash
  pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
  python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_arch_list())"   # True; includes sm_86 and sm_120
  ```
- **onnxruntime:** IRL Shield pins `onnxruntime-gpu 1.20.2` (CUDA 12.4). That works on the RTX 3050 but not the RTX 5050, so use one CUDA 12.8+ build on both.
- **Check NVENC** with `ffmpeg -hide_banner -encoders | grep nvenc`. If it's missing (possible under WSL2), encode with x264 on the CPU.
- **Phone into WSL2:** set `networkingMode=mirrored` in `%UserProfile%\.wslconfig`, or forward ports.
- **Location:** keep the project in the WSL home folder (`~/stremsarthi`), not under `/mnt/c`, because file access there is slow.

### Why Ubuntu (Linux) at all
- IRL Shield's relay is written for Linux. Its README says it needs Linux x86-64 and Python 3.11, and its scripts use bash, `flock` and Linux paths.
- MediaMTX, ffmpeg and the ML libraries are simplest on Linux, and the servers you'd deploy to later are Linux.
- **You don't need to dual boot.** WSL2 gives you Ubuntu inside Windows. Running natively on Windows would mean porting IRL Shield's relay, which isn't worth the time.

### GPU memory budget
| Profile | Budget | What's different |
|---|---|---|
| `dev` (RTX 3050) | 3.5 GB in total, so it fits the 4 GB card | Detect every 3rd frame; speech-to-text through the API |
| `demo` (RTX 5050) | Up to 7 GB | May detect every 2nd frame, run the local speech-to-text fallback on the GPU, and run IndicTrans2 captions locally |

### Other rules
- Only one GPU job at a time: `flock /tmp/streamsarthi-gpu.lock <command>`.
- Any training (later, optional) goes to Colab or Kaggle.
- Keep the laptops plugged in and in performance mode; a cooling pad helps on long runs.
- **Get the RTX 5050 laptop at least a week before the demo.** Set up the same WSL2 environment and run the full demo on it at least 3 times. Blackwell needs the cu128 stack, so don't discover a problem on demo day.

---

## 11. Licences and rules

- Keep the LICENSE and NOTICE files for PrivaStream (Apache-2.0) and IRL Shield (MIT), and credit both in the README.
- **Never copy Offleak's code.** It's AGPL, which would force the whole project to be AGPL.
- PrivaStream depends on **Ultralytics YOLO (AGPL)** and **InsightFace (weights for non-commercial use only)**. Fine for a hackathon; replace them before any commercial product.
- **Check the hackathon rules:**
  - Is pre-built code allowed, or must it be disclosed?
  - Must AI coding tools (Codex, Claude Code) be disclosed?
  - If pre-built code is banned, the PrivaStream fork can't be used, and the scope has to shrink.
- When you reuse code, credit PrivaStream and IRL Shield openly and pitch the Indian layer as your contribution. Judges respect that more than finding out later that you hid it.
- **Never use real Aadhaar, PAN or bank cards, or anyone's real personal data.** Test data must be synthetic and printed with "SPECIMEN".

---

## 12. Senior judge's evaluation

### Scorecard
| Criterion | As an idea | After fixes | What gets it there |
|---|---|---|---|
| Problem and impact | 7 | 9 | Real reported cases of Indian streamers who were doxxed or harassed |
| Innovation | 7 | 8 | Indian IDs, Indian scripts, Hinglish speech and chat, all in one live layer |
| Technical depth | 9 | 9 | Real-time vision, audio and chat processing together |
| Feasibility | 5 | 9 | A broadcast delay, measured numbers, the mobile relay |
| Demo | 7 | 9 | A scripted, instant before-and-after |
| Scalability and business | 4 | 8 | A business model and GPU cost per streaming hour |

**Overall:** about 7 out of 10 as an idea, about 9 after the fixes. It loses points only on doubt that it works reliably live.

### Hard questions and how to answer them
1. **"How do you blur a phone number after it's already been said?"** With a 3–5 second delay on both audio and video, kept in sync. Detection finishes before anything goes out, the same way TV uses a broadcast delay. Show the delay setting.
2. **"What if the model misses an Aadhaar card?"**
   - It's built to fail safe: when unsure, it blurs; tracking keeps blurs on tilted or moving cards; a panic hotkey or phone tap covers the whole screen.
   - Show precision and recall for each type of PII on a synthetic test set.
3. **"Indian streamers mostly use phones. You need a GPU?"** There are two modes, an OBS plugin or relay on a desktop, and a cloud relay for phones. Know the GPU cost per streaming hour.
4. **"YouTube and Twitch already moderate chat."** Their filters are built mainly around English and blocklists. Show it live: Hinglish abuse written in English letters gets past a basic word filter and is caught by Stream Sarthi.
5. **"Isn't this just PrivaStream?"** "PrivaStream blurs generic PII. We built for India: Aadhaar, PAN, UPI QR codes, Indian number plates, signboards in Hindi and other Indian scripts, Hinglish speech, a chat shield, a strike guard and a mobile relay."
6. **"How do you detect copyrighted music?"** It detects that music is playing and fingerprint-matches it against a demo library. **Don't claim to replicate Content ID.**
7. **"Do you store faces or streams?"** No. Processing happens on the device or in the relay's memory, and the safety report holds only timestamps and categories. That fits India's DPDP Act.
8. **"Who pays?"** A free tier for individual streamers, and a paid tier for creator agencies, esports organisations and Indian streaming platforms.
9. **"You send streamers' audio to third parties?"** Video never leaves the machine. Only audio chunks and chat text go to the speech and LLM APIs, and Stream Sarthi stores nothing. There's a local fallback (rules plus on-device speech-to-text) for streamers who want nothing sent out.

### Checklist before you submit
1. All features work live. Nothing is mocked.
2. You've measured end-to-end delay and frames per second, and you know which hardware the numbers came from.
3. You have precision and recall for each type of PII, and a red-team miss rate.
4. The phone relay works.
5. You have 2–3 real, sourced incidents for the problem slide. **Find real reported cases; don't invent any.**
6. You have a backup recording of the demo.
7. You have the cost per streaming hour (API usage, plus GPU if a cloud relay is used).
8. The full demo has run at least 3 times on the RTX 5050 demo laptop.

---

## 13. Demo and pitch

### The winning demo (3 minutes)
1. Go live from a phone, on a BGMI stream or an IRL walk past Hindi signboards.
2. Hold up a **SPECIMEN** Aadhaar card. Viewers see it blurred.
3. Read out a phone number in Hinglish. Viewers hear it muted, and the mouth is blurred.
4. A teammate posts Hinglish abuse and a scam UPI link in chat. It's hidden from the streamer and sent to the moderator queue.
5. Play a Bollywood track. The strike guard warns.
6. Press hold. The privacy slate covers the screen.
7. End on the safety report timeline.

Optional extra moment: switch on live captions in an Indian language.

### Pitch outline
1. **The problem:** one frame is enough to dox someone. Use real Indian incidents.
2. **Why existing tools fail:** they're English-first, made for desktops, and don't handle Indian IDs.
3. **Stream Sarthi in one line,** then the live demo.
4. **How it works:** the architecture diagram, the delay buffer and the fail-safe.
5. **Proof:** precision and recall, delay, the red-team miss rate, and pilot results.
6. **Business:** free tier, paid tier for agencies and platforms, cost per hour.
7. **Close** with the pitch line.

---

## 14. Build timelines

Unless noted, these assume a team of 4 reusing PrivaStream and IRL Shield, with an AI coding agent (Codex or Claude Code) on every track.

| Setup | Time | What you get |
|---|---|---|
| Normal pace, reusing the repos, no AI agent | About 4 weeks | Full platform |
| From scratch, no AI agent | 6–8 weeks | Full platform |
| With an AI coding agent, normal pace | About 2.5 weeks (18 days) | Full platform, hardened |
| Hackathon, 24 hours | 24 h | Every feature, rule-based versions only, no training |
| Hackathon, 48 hours | 48 h | Plus fine-tuned models, tracking, phone relay, 5 languages, measured numbers |
| Hackathon, 64 hours | 64 h | Plus real-streamer pilot, red-team results, captions, phone control app, cost numbers, landing page |
| One week of full-time work (8–10 hours a day) | 7 days | About the same as the 64-hour plan: full, at the quality a hackathon demo needs |

### API-first build (no training) on your own laptops
| Who | Demo-ready version (relay, Aadhaar/PAN/UPI/face blur, chat shield, hold) | Full feature set, with test numbers |
|---|---|---|
| Team of 4 with Claude Code or Codex | 2–3 days | About 1.5–2 weeks |
| One person with Claude Code, full-time | About 1 week | About 3 weeks |
| Hackathon sprint, team of 4 | 24 hours | 48 hours |

These are rough estimates. Most of the time goes into testing on real streams, not writing code.

A production product that people rely on daily is a months-long effort.

**The biggest risk is real-time delay.** If a SPECIMEN Aadhaar card can't be blurred live with under 5 seconds of delay by the end of day 2, cut the scope to video blur, the chat shield and hold, and polish those.

### 18-day plan with an AI coding agent
| Days | Work |
|---|---|
| 1–3 | Fork PrivaStream, merge in IRL Shield's relay, build the delay buffer, set up a latency harness |
| 4–8 | Indian detectors and data generators; start fine-tuning |
| 9–12 | Spoken PII in Hinglish, chat shield and scam links, strike guard |
| 13–15 | Dashboard and safety report; measure precision, recall and latency |
| 16–18 | Test on real streams, fix issues, rehearse the demo |

### 24 hours (4 people, rule-based versions)
| Hours | Pipeline | Video | Audio and chat | Dashboard |
|---|---|---|---|---|
| 0–2 | PrivaStream Docker image running on the GPU | Generate SPECIMEN cards, UPI QR codes, plates | Set up YouTube chat API and Groq | Set up Next.js |
| 2–8 | Relay to YouTube, A/V delay buffer, latency harness | Aadhaar, PAN, UPI QR and plate detectors | Speech-to-text and Hinglish PII muting | Live event feed, hold button |
| 8–14 | Merge detectors into the pipeline | Signboard OCR, BGMI IDs | Chat shield, scam links | Safety report, moderator queue |
| 14–18 | Bug fixes, latency tuning | Precision and recall on 50 test images | Strike guard | Polish, Indian branding |
| **18** | **Feature freeze** | | | |
| 18–22 | Full end-to-end runs on a real stream | Fix misses | Fix misses | Pitch deck |
| 22–24 | Backup demo video, two rehearsals | | | |

Before the clock starts (if allowed): a GPU machine with CUDA working, the PrivaStream image pulled, a YouTube stream key, API keys, the phone app tested, SPECIMEN cards, 5 Bollywood clips, and a Hinglish abuse test list.

### 48 hours
- **Day 1:** follow the 24-hour plan, with the feature freeze at hour 20.
- **Day 2:**
| Hours | Pipeline | Video | Audio and chat | Product |
|---|---|---|---|---|
| 24–30 | ByteTrack tracking | Fine-tune a card detector on synthetic cards (in the background) | Fine-tune MuRIL on Hinglish abuse | Phone relay on 4G with adaptive quality |
| 30–36 | Swap in trained models; latency regression tests | Fine-tune the plate model on Indian plates | Tamil, Telugu and Bengali in OCR and chat | Safety report, settings, onboarding |
| 36–40 | Bug fixes | Evaluate on 200+ test images | 50 recorded Hinglish phrases | Deck with measured numbers |
| **40** | **Final freeze** | | | |
| 40–46 | 3 live runs (OBS, phone IRL, BGMI) | Fix misses | Fix misses | Backup video |
| 46–48 | Rehearse twice, prepare Q&A | | | |

**Sleep in shifts:** two people sleep 4 hours while the fine-tunes run (hours 24–32), then swap.

### 64 hours
Hours 0–48 follow the 48-hour plan. Then:
| Hours | Pipeline | Video | Audio and chat | Product |
|---|---|---|---|---|
| 48–54 | Pilot with 2–3 real Indian streamers live: log catches, collect quotes | Red-team the video (angles, glare, partly covered cards, fast movement) | Live captions in Indian languages (IndicTrans2) | Phone control app (hold, risk meter, alerts) |
| 54–56 | Measure GPU cost per streaming hour | Final evaluation on 300+ images | Red-team chat and speech (misspellings, fast numbers, mixed scripts) | Landing page and one-command Docker install |
| **56** | **Final freeze** | | | |
| 56–62 | 3 live runs, backup demo, pilot clips edited in | | | Deck with pilot, red-team and cost slides |
| 62–64 | Rehearse three times, mock Q&A | | | |

Sleep shifts at hours 24–32 and 44–50.

### One week (full-time)
| Day | Work | Checkpoint |
|---|---|---|
| 1 | PrivaStream on the GPU, relay to YouTube, delay buffer, latency harness | **A SPECIMEN Aadhaar card blurred live with under 5 s delay.** If this fails, fix it before anything else. |
| 2 | All video detectors (rules and pre-trained), Hinglish spoken PII, chat shield, scam links | Every type of PII caught in a test stream |
| 3 | Strike guard, game IDs, dashboard, safety report, hold | **Every feature working end to end** |
| 4 | Fine-tune card, plate and MuRIL models; tracking; Tamil, Telugu, Bengali; phone relay on 4G | Trained models in place, with no added latency |
| 5 | Captions in Indian languages, phone control app, red-team testing and fixes | Miss rate measured and reduced |
| 6 | Pilot with 2–3 streamers, evaluation on 300+ images, GPU cost per hour, landing page | Real usage logs and quotes |
| 7 | Freeze. Three live runs, backup video, deck, three rehearsals and mock Q&A | Ready to submit |

### Full build phases (no deadline)
| Phase | What you get | Checkpoint before moving on |
|---|---|---|
| 0. Study | Both reference projects running; each one's delay and misses measured | You know what breaks |
| 1. Core engine | PrivaStream engine combined with IRL Shield's relay, fail-safe and hold; output to YouTube and Twitch | Under 5 s delay; no unchecked frame goes out |
| 2. Indian video layer | Cards, UPI QR, Indian plates, OCR for Indian scripts, OTP screens, tracking | Recall for each PII type above target |
| 3. Hinglish audio | Speech-to-text, PII model and muting in 5+ languages | Spoken numbers muted inside the delay window |
| 4. Chat shield | Abuse, scams and doxxing; moderator queue | False positives low enough that streamers keep it on |
| 5. Strike guard and games | Music, profanity and policy risk; game ID blur | A working risk meter |
| 6. Product | Streamer app, onboarding, safety report, captions | A new user is live within 10 minutes |
| 7. Hardening | Evaluation suite, red-team suite, latency checks on every change, multiple streams per GPU | Known miss rate and cost per hour |
| 8. Pilot and launch | 10–20 Indian streamers live, then open beta | Real logs and retention |

---

## 15. Building with Claude Code (the kit in this folder)

### How it's set up
- **Planning on Opus, building on Sonnet:** the `opusplan` model setting runs **Opus 5.5 in plan mode** and **Sonnet 5.5 for execution**.
- **Builder subagents,** all on Sonnet 5.5, each owning separate directories so they can work in parallel:
| Agent | Owns |
|---|---|
| `pipeline-engineer` | `engine/`, `relay/` |
| `vision-engineer` | `vision/` |
| `audio-engineer` | `audio/`, `guard/` |
| `chat-engineer` | `chat/` |
| `product-engineer` | `app/api/`, `app/web/` |
| `eval-engineer` | `eval/`, `scripts/gate_*` |
| `reviewer` | Read-only; reviews each round of work before it's committed |
- **Rules:** `CLAUDE.md` holds the project rules (scope, ownership, licences, hardware, privacy, quality). Only the lead commits; `reference/` is read-only; one GPU job at a time.
- **Settings:** `.claude/settings.json` sets the model to `opusplan`, pre-allows common commands, and blocks `git push`, reading `.env` and editing `reference/`.

### Steps (full detail in `PROMPTS.md`)
1. Set up WSL2 with Ubuntu 24.04 (the NVIDIA driver stays on Windows), Python 3.11 (via `uv`), Node 20+ and ffmpeg. Install or update Claude Code (`claude update`; setting `opusplan` from `/model` needs v2.1.265 or later).
2. Copy `stremsarthi` into the WSL home folder (`cp -r /mnt/c/Users/siddh/Desktop/sathack26/stremsarthi ~/`). Run `cp .env.example .env`, then `git init && git add -A && git commit`.
3. Run `claude --model opusplan --permission-mode plan` and paste **Prompt 1**. Opus studies the reference code and drafts the architecture, contracts, VRAM and latency budgets, phases with gates, the task graph, specs and risks.
4. Approve the plan. Claude switches to Sonnet and writes `docs/PLAN.md`, `docs/specs/`, `contracts/`, `docs/STATUS.md`, `docs/THIRD_PARTY.md` and `docs/DECISIONS.md`.
5. Paste **Prompt 2**. The orchestrator runs each round of tasks with parallel subagents, then the reviewer, CPU tests and the gate script, then commits and updates the status file.
6. In a new session, use **Prompt 3** to resume. If a gate fails twice, use **Prompt 4** to re-plan on Opus.

**Optional:** agent teams (experimental; set `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`) let builders message each other directly, but use more tokens.

### Tips for any AI coding agent (Codex or Claude Code)
1. Build the latency and accuracy harness first.
2. Write one spec per module: inputs, outputs, test cases, a latency target.
3. One person owns and reviews each module. Judges will ask how it works.
4. Run tracks in parallel.
5. Disclose AI tools if the hackathon requires it.

---

## 16. Getting started (Phase 0, 2–3 days)

1. **Before code (half a day):** read the hackathon rules (pre-built code, AI tools); get a GPU; assign owners for Pipeline, Video, Audio and chat, and Product; set up accounts (section 17).
2. **Get both reference projects running (day 1):**
   - PrivaStream: install the cu128 PyTorch build first, then its requirements.
   - IRL Shield: run local mode, with no Twitch account needed: `scripts/walk-linux --local-only --cpu --host 127.0.0.1 --venv .venv --model-cache models/cache --mediamtx-dir .tmp/tools/mediamtx-v1.21.3`. See `reference/irl-shield/docs/RUN.md`.
3. **Build the test set and measure (day 2).** Record about 10 short clips:
   - SPECIMEN Aadhaar and PAN cards
   - A UPI QR code on screen
   - A street with Indian plates
   - Hindi and Kannada signboards
   - Someone reading a phone number and UPI ID in Hinglish
   - A BGMI lobby with IDs visible
   - A chat log with Hinglish abuse and scam links

   Run every clip through both projects and record **what each one missed, and the end-to-end delay.**
4. **First build goal:** a SPECIMEN Aadhaar card held up to the camera, blurred live on YouTube, with under 5 seconds of delay.
5. **Today:** message 5–10 small Indian IRL or BGMI streamers about a pilot.

---

## 17. Accounts, keys, and things only you can do
- [ ] Turn on live streaming for the YouTube channel. The first time can take up to a day.
- [ ] A YouTube stream key, plus a Google Cloud OAuth client with the YouTube Data API enabled, for chat moderation.
- [ ] A Twitch developer app (client ID and secret) and a Twitch stream key.
- [ ] A Groq API key and a Sarvam API key.
- [ ] Moblin (iPhone) or Larix Broadcaster (Android) on a phone; Tailscale if using IRL Shield's phone relay.
- [ ] Fill in `.env` from `.env.example`. Never commit `.env`.
- [ ] Arrange the RTX 5050 demo laptop at least a week before the demo, and set up the same WSL2 environment on it.
- [ ] Find PrivaStream's plate model weights (GitHub releases or `ghcr.io/saximn/privastream`), or plan to train one (see `reference/REUSE_MAP.md`).

---

## 18. Pilot plan
- **Who:** small Indian IRL or BGMI streamers with 1–10k followers. They usually say yes to a free safety tool.
- **Book them early.** For a 64-hour hackathon, they go live in hours 48–54. For the one-week plan, on day 6.
- **Collect:** catch logs (category counts), delay, a quote from each, and short before-and-after clips. Use only what they agree to share, and store no PII.
- **Pitch line from the pilot:** "N Indian streamers used it live, and it caught X leaks." Use the real numbers from the logs.

---

## 19. Risks and fallbacks
| Risk | Fallback |
|---|---|
| Live delay above 5 seconds | Detect every 3rd frame and let tracking fill the rest; drop to 720p or 24 fps; check the GPU is actually used (cu128 PyTorch, onnxruntime on CUDA 12.8) |
| Running out of GPU memory on the RTX 3050 | Run detectors on the CPU; nano models; detect every 3rd frame; speech-to-text, chat and captions through APIs |
| API too slow, down or rate-limited | Async calls with timeouts; local fallbacks (rules, faster-whisper tiny on the CPU); batch chat messages; a bigger delay buffer |
| API costs during testing | Use free tiers; cache results; replay recorded clips instead of going live for every test |
| The demo laptop behaves differently | Same WSL2 and cu128 stack on both; the `demo` profile only turns extras on; at least 3 full runs on the RTX 5050 before demo day |
| Hinglish speech-to-text too weak or slow | Compare the Sarvam API, IndicConformer and faster-whisper; use rules for numbers |
| Missed cards or plates | Fail-safe blur when unsure; blur the whole card; tracking; the hold button; fine-tuned detector |
| Phone relay unstable on 4G | SRT ingest, adaptive bitrate, a bigger buffer; desktop OBS mode as the demo backup |
| Plate weights not available | Train a plate detector on Indian plate datasets |
| Chat API limits or OAuth delays | Read-only chat at first; hide on our side instead of using platform actions |
| Too many false positives in chat | Rules plus a confidence threshold; hold for a moderator instead of deleting |
| Hackathon bans pre-built code | Cut to relay, Aadhaar, PAN and UPI blur, chat shield and hold, written during the event |
| Live demo fails on stage | A recorded backup demo video, and a local OBS demo with no internet |
| Licence questions | Credit PrivaStream and IRL Shield; never copy Offleak; list AGPL and non-commercial items to replace |

---

## 20. Appendix

### A. BimaSathi, extended (the backup idea, if switching to Open Innovation)
- **Pitch:** "Farmers lose claims to paperwork, not to the weather. BimaSathi makes filing a claim as easy as sending a voice note."
- **Farmer flow:**
  1. The farmer sends a voice note in Kannada, Hindi or another regional language.
  2. The bot asks follow-up questions by voice: crop, land, when the damage happened.
  3. The farmer sends photos of the field; the bot tags each with location and time.
  4. The AI fills in and files the claim form.
  5. Status updates and missing-document alerts arrive on WhatsApp.
- **New on top of the original:**
  - Damage scoring from photos
  - Fraud checks (duplicates, reused photos, mismatched location or time)
  - An insurer and official dashboard
  - A village loss heatmap
  - A deadline guard
- **Stack:** Sarvam or Bhashini, Gemini or Groq, a vision model, the WhatsApp Business API, FastAPI, Postgres and Next.js.
- **About the original:** BimaSathi was built by Team Rayquaza EX for the AWS AI for Bharat Hackathon, on AWS (Bedrock Nova Pro, Rekognition, Transcribe, Polly, Step Functions, Twilio). Repo: https://github.com/ammarsksk/BimaSathi.

### B. Content Room (AWS AI for Bharat, media track winner)
- **Its 11-stage workflow:** idea capture, creator studio, moderation and safety, mental health layer, shadowban prediction, asset explosion, regional and cultural adaptation, trend injector, competitor intelligence, content calendar, scheduling. Plus multi-platform output.
- **What probably won it:** shadowban prediction, the mental health layer, regional and cultural adaptation, and moderation with a human in the loop.
- **Status:** its code isn't public. Its ideas were adapted into Stream Sarthi's chat shield, strike guard and safety report.

### C. Links
- PrivaStream: https://github.com/Saximn/privastream · Devpost: https://devpost.com/software/live-privacy-shield · Demo: https://www.youtube.com/watch?v=7zQslm_6C4A
- IRL Shield: https://github.com/elannash/irl-shield · Devpost: https://devpost.com/software/irl-shield · Demo: https://youtu.be/DIRiN-hniaI
- Offleak (reference only, AGPL): https://github.com/azharizz/offleak
- Stream privacy guard (GPL-2.0, reference only): https://github.com/neanicc/stream-privacy-guard
- PyTorch with CUDA 12.8 wheels: https://download.pytorch.org/whl/cu128
- Claude Code docs: model configuration https://code.claude.com/docs/en/model-config · subagents https://code.claude.com/docs/en/sub-agents · agent teams https://code.claude.com/docs/en/agent-teams · permissions https://code.claude.com/docs/en/permissions
