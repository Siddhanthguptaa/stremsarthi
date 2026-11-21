# Stream Sarthi: demo script and run book (T7.4)

Written 2026-11-21 against commit 8a1e6e3 plus the uncommitted tree. Every number is from `docs/STATUS.md` with its caveats. Nothing here has been rehearsed on the RTX 5050 demo laptop, and **no live YouTube, Twitch, Sarvam or Groq call has ever been made** (all keys in `.env` are empty).

## 0. Read first: what can be shown today

The plan's end-to-end go-live flow (T6.1 to T6.3, Phase 5, T6.4) is **not built yet**. Each scene below is tagged:

- **RUNS**: the entry point exists in the repo and was run by a builder (builder-reported unless noted).
- **PARTS**: the module and tests exist but the live wiring or the screen is missing; show it through the fallback listed.
- **NOT BUILT**: do not demo live; show the recording, the test report or say so.

| Piece | Tag | What is missing |
|---|---|---|
| Engine: ingest, 4 s delay buffer, mask compositor, hold slate, fail-safe | RUNS | G1 not passing yet (STATUS); egress to real YouTube/Twitch never tried |
| Relay: MediaMTX, x264/NVENC encoder, RTMPS egress | RUNS (loopback sink only) | no real destination |
| Video detectors: cards, UPI QR, text/signboards, faces + allowlist, plates, OTP and game IDs | RUNS in offline eval; live in-engine run is G1-scoped (cards only measured end to end) | see section 5.2 for recall |
| Audio PII mute and mouth blur | PARTS | measured only with an oracle recogniser, never real speech-to-text |
| Chat shield and moderator console | PARTS | `python -m chat.main` (live process, OAuth, ZeroMQ client) is not built; chat runs only against fakes and mocks |
| Strike guard (music, profanity, risk meter) | NOT BUILT per STATUS | `guard/` exists untracked (music, risk, profanity files), no STATUS entry, not in `runtime.modules`, no reviewer sign-off |
| Live captions | NOT BUILT | `audio/captions/` has translate, redact, langs, timing; T5.3 pending; `engine/captions.py` renderer exists, no live path measured |
| Go-live page, safety report page | NOT BUILT | `app/web/app/(streamer)/go-live/page.tsx` and `(report)/report/[sessionId]/page.tsx` are placeholders. The data exists: `GET /api/events` and `/api/events/summary` |
| Hotkey helper, Moblin pairing QR, phone ingest | NOT BUILT | `scripts/hold_hotkey_win.py` and `relay/pairing.py` do not exist. Hold works from the web button and `POST /api/hold` |
| App: `/live` (hold, status, alerts), `/enrol`, `/controls`, `/mod` | RUNS (jsdom and API tests; no browser, camera or axe run: T6.5 pending) | |

If the team cannot finish the NOT BUILT rows before the demo, the pitch must say "built and tested offline" for them. Sections 7 and 8 list what may be claimed.

## 1. The 5-minute story (target flow; skip scenes that are not live)

| Time | Scene | Say | Show |
|---|---|---|---|
| 0:00 | Problem | One frame, one spoken number or one chat message is enough to dox a streamer. Existing tools are English-first, desktop-first, and do not know Aadhaar, PAN, UPI. (Use 2 to 3 real, sourced incidents; the user still has to supply these, none are in the repo.) | Slide |
| 0:30 | Architecture | A broadcast delay (4 s) on audio and video, kept in sync. Detection finishes before anything goes out. Anything not yet checked goes out masked. | Architecture slide, then the `/live` page showing delay and engine status |
| 1:00 | Video | SPECIMEN Aadhaar, PAN, UPI QR, plate, OTP phone screen, BGMI room ID, Hindi signboard. Viewer-side output is blurred. A SPECIMEN face of an enrolled teammate stays visible, a stranger is blurred. | Left: raw OBS source. Right: delayed stream (tap or the YouTube unlisted viewer) |
| 2:15 | Audio | Teammate says a SPECIMEN phone number in Hinglish ("mera number hai nau aath ..."). Viewers hear a mute, the mouth is blurred. | Output audio and video, plus the alert feed |
| 3:00 | Chat | Teammate posts Hinglish abuse and a fake UPI link. Hidden from the streamer overlay, appears in the moderator queue. | `/mod` console next to the streamer overlay |
| 3:45 | Hold | Press HOLD. Slate covers the video and audio is muted within a frame; the last 3 s of buffered frames are masked too (retro hold). | `/live` HOLD button, then output |
| 4:15 | Strike guard, captions (only if built) | Play a library track: risk meter rises. Switch captions on in Hindi or Tamil. | Risk meter, caption overlay |
| 4:30 | Proof and cost | The section 8 table with caveats; cost per hour from `docs/COST.md` (typical about INR 24 per hour with captions off, all assumptions unmeasured). Safety report timeline: category, time, confidence, never images or text. | `GET /api/events/summary` JSON or the report page if built |

## 2. Pre-flight checklist (do the day before and 30 min before)

In every command below, `SS` stands for the wrapper, run from Git Bash on Windows:
`MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh`
(do not put `$VARS` or Windows paths inside the command; see `docs/AGENT_BRIEF.md`).

1. **Hardware**: demo laptop is the RTX 5050 one, plugged in, power mode performance, second display or phone for the viewer side. Open nothing heavy: the G1 numbers were taken on a loaded machine and the delay maximum was tight.
2. **WSL**: `C:\Users\siddh\.wslconfig` has `memory=6GB`, `swap=8GB`, `vmIdleTimeout=-1` (HANDOFF). Networking is still NAT: mirrored mode and the Hyper-V firewall rule for UDP 8890 and TCP 1935 are user admin steps not yet done (STATUS open items). Without them a phone cannot reach the engine; OBS on the same laptop can (loopback).
3. **Environment**: `SS python scripts/doctor.py --profile demo --gpu` (exit 0 ok, 1 warnings, 2 blocking). Last recorded dev result: exit 1, warnings only (NAT, empty keys, `fpcalc` and `espeak-ng` missing). Re-run on the demo laptop: ONNX Runtime on sm_120 (RTX 5050) is untested (STATUS open items); the first CUDA run JIT-compiles and is slow, so warm it up before the demo. If the CUDA provider fails, use `dev` (CPU).
4. **Models and MediaMTX**: `SS python -m models.prepare --verify-only` (hash check, no network). `.tmp/tools/mediamtx-v1.21.4/mediamtx` must exist (`bash scripts/fetch_tools.sh` fetches it; needs network, run before the demo day).
5. **Keys** in `.env` (never print them): `GROQ_API_KEY`, `SARVAM_API_KEY`, `SECRET_KEY` (any long random string), `YOUTUBE_STREAM_KEY`, `YOUTUBE_OAUTH_CLIENT_ID/SECRET`, `TWITCH_CLIENT_ID`, `TWITCH_STREAM_KEY`. Optional: `INGEST_PUBLISH_USER` and `INGEST_PUBLISH_PASSWORD` (letters, digits, `_`, `-` only) so OBS keeps one login across restarts (otherwise random per run; they are supported by `relay/runtime.py` but missing from `.env.example`). YouTube live must be enabled on the channel at least 24 h earlier; test with an unlisted stream. YouTube chat OAuth consent screen with the streamer as a test user (refresh tokens last 7 days in Testing status).
6. **Live API spike, once, before demo day**: `SS python -m audio.spikes.stt_spike` with the keys exported fills in Sarvam and Groq latency (never measured; D-36 also needs the first live call to settle `saaras:v4` codemix mode).
7. **Run the suites that exist** (each in its own process, never all at once: a runaway test once froze WSL): `SS bash scripts/mem_check.sh contracts/tests engine vision models audio chat app/api`.
8. **Gate reports** to have open on the laptop: `eval/reports/2026-11-21-phase2-offline-d48.md`, `eval/reports/2026-11-21-phase3.md`, `eval/reports/2026-11-21-phase4.md`.
9. **SPECIMEN inputs** (section 4) generated and open on a second screen. A recorded backup of the whole run exists and is on the desktop (NOT YET RECORDED).
10. Confirm the hackathon rules on pre-built code and AI-tool disclosure (open user item, PLAN section 9).

## 3. Start sequence (four terminals inside WSL, or four `SS` commands)

```
# 1. engine (ingest, delay, masks, egress, ZeroMQ bus); demo profile runs under the GPU lock
SS bash scripts/run_engine.sh demo          # or: dev
# 2. API (prints http://127.0.0.1:3000/#token=<streamer token>; run with SECRET_KEY in the environment or .env)
SS python -m app.api.main
# 3. web app (Next.js dev server on 3000, API expected at http://127.0.0.1:8000)
SS bash scripts/web.sh run dev
# 4. source: OBS on the laptop, or the synthetic sender (section 4)
```

Open the printed URL once in the browser (the token is in the URL fragment). Then `/live` shows the hold button, engine chip and alert feed; `/enrol`, `/controls`, `/mod` exist. `POST /api/session/start` exists but the go-live page that calls it does not (placeholder): start via the API, or let the engine run directly. There is no `run_api` or `run_chat` script; `python -m chat.main` does not exist yet.

**OBS settings** (desktop path; the phone path is NOT BUILT): output to MediaMTX on the laptop, `srt://127.0.0.1:8890?streamid=publish:camera:<user>:<password>` (builder in `relay/security.py`), or RTMP `rtmp://127.0.0.1:1935/camera?user=<user>&pass=<password>`. 720p30, x264, keyframe 2 s, no B-frames. The engine reads from MediaMTX over loopback RTMP (D-40).

## 4. SPECIMEN inputs (synthetic, visibly marked, never real data)

All generators are seeded and deterministic. Every card carries a SPECIMEN watermark; Aadhaar numbers are Verhoeff-valid and start with 0 or 1; PAN 4th letter is X; UPI IDs look like `zz-specimen-<hex>@...` (`eval/README.md`).

- Cards, QR codes (already generated): `eval/data/generated/specimen_v0_seed1234/` holds `aadhaar_000x.png`, `pan_000x.png`, `upi_qr_000x.png`, `qr_negative_000x.png`. Regenerate: `SS python -m eval.run gen-specimen --out /tmp/specimen --n 5`. Show on a second monitor or phone held to the camera, or play as an OBS image source.
- Full video suite (plates, signboards in 5 scripts, OTP and BGMI/Free Fire screens, faces, clips): `SS python -m eval.run gen-video --out /tmp/video --n 40 --clips 2` (about 600 MB; needs network the first time for the SHA-checked face portraits). Pick images and clips from its buckets; the truth files say what is in each.
- Faces: the eval face set is NASA public-domain portraits (user confirmed 2026-11-21, D-48; `eval/data/generated/faces/ATTRIBUTION.md`). For the live enrolment demo use **consenting teammates only** and enrol 5 to 12 frames at `/enrol` (RAM only, 12 h TTL). Frontal well-lit data: `face_match_cos` still has to be re-tuned on teammate footage (STATUS T2.6).
- Synthetic stream with optical timestamp (fallback source): `SS python -m eval.sender.sender --url "srt://127.0.0.1:8890?streamid=publish:camera:<user>:<password>" --script card_static:60,card_moving:30,card_absent:20`. Scenes: `card_static`, `card_moving`, `card_hold`, `card_absent`, `many_targets`, `idle`.
- Phone-number audio: the spoken-PII test audio is `eval/data/generated/audio/clips/*.flac` (English flite TTS: regional speech is an English phonemiser, not real Hindi or Tamil). For the live scene a teammate reads a made-up 10-digit number starting with 9, never a real number. Real Hinglish speech has never been run through the pipeline (STATUS T3.1).
- Chat: messages are typed by a teammate; use made-up UPI handles (`zz-specimen-1a2b@oksbi`) and Hinglish abuse of the teammate's own choosing. Do not use real names, numbers or addresses. Chat datasets: `SS python -m eval.data_gen.chat --out DIR`.
- Music: the demo fingerprint library needs 5 Bollywood clips only the user can supply (`guard/music/library/` is empty). Without them the music scene cannot run.

## 5. Per-feature run book

Each row: what to do, what viewers see, the honest status, the fallback.

### 5.1 Relay and hold
- Do: start engine, point OBS or the synthetic sender at the `camera` path, watch `/live` go from waiting to live. Press HOLD (button, or `H` key on the page, or `POST /api/hold` with `{"active": true}` and the streamer token). Release.
- See: slate (English and Hindi text) within one frame (measured: hold to slate <= 1 frame, T1.1); the last `retro_seconds` (3.0) of buffered frames and audio masked as well (D-23). Delay is a config knob: `delay_seconds` 4.0 in both profiles, range 3.0 to 5.0.
- Measured: delay p95 4.71 s CPU / 4.72 s GPU, max 5.44 / 5.50 s against a 5.5 s limit, 31 fps, slate fraction 3.3 to 4.1 % (limit 10 %), 0 card leaks, in a 45 s leg on a loaded laptop after the D-43 fix. Gate G1 is still FAIL overall (full 120 s run and soak not done, hold-leg alignment open). Viewer-side YouTube latency is not gated and not measured.
- Fail-safe demo: `SS bash scripts/gate_phase1.sh --help`; `--plan` prints legs. The fail-safe leg (vision lane hangs, 0 card frames after the hang, 1,129 slate frames) passed in an earlier run.
- Fallback: if egress to YouTube fails, show the `shielded` tap or the loopback sink (`SS python -m eval.run loopback --seconds 60`).
- Hotkey: not built. Use the button or the API. Chat `!hold` (broadcaster and moderators) exists in `chat/commands` but needs the live chat process.

### 5.2 Video PII
- Do: show each SPECIMEN on the source feed; compare with the delayed output. Toggle categories at `/controls` (turning off Aadhaar, PAN or UPI QR asks for confirmation).
- See and measured (offline, CPU, synthetic SPECIMEN, clean+light levels only per D-48; heavy level reported separately): Aadhaar, PAN, UPI QR, signboard, phone, UPI ID, email, game IDs, plates, faces masked 1.000 on stills and clips at clean+light; PIN 0.912, OTP 0.917 on stills. Failing even at clean+light: allowlisted faces visible 0.208 on stills and 0.750 on clips; accurate-lane p95 530 ms vs 450 ms budget. Gate G2 = FAIL. Heavy level (blur, heavy JPEG, dark): plates about 4.5 %, OTP 0 %, game IDs about 5 %, Aadhaar clips 0.55, phone clips 0.33.
- Face allowlist: enrolled teammate visible (settled 99.4 %, steady 94.0 % below the 95 % target; first about 1 s after the face appears is masked on purpose); a stranger masked 97.2 % with 0 runs showing a stranger (8 NASA people, async embedding). The face release path was changed late (`vision/planner.py`) and is marked "reviewer must check: privacy-affecting".
- UPI QR: payment QR recall 100 % (72) with 98.6 % precision; the payload is never logged. A non-payment QR is not masked.
- Signboards: all text is masked (script-agnostic), so a Hindi/Tamil/Kannada signboard goes dark without being read. It also masks some text-free scenes and plain signage.
- Fallback: if the live path is unstable show the per-category table in `eval/reports/2026-11-21-phase2-offline-d48.md` and the stills/clips, and run `SS bash scripts/gate_phase2.sh --plan`. If the machine lags, the engine masks instead of leaking (slate), which is visible as more black frames: say so. Moving a card slowly in `card_moving` shows tracking.

### 5.3 Audio PII: mute and mouth blur
- Do: teammate reads the made-up number while an enrolled face is on camera. Output audio drops during the number; the mouth region is blurred for the muted span; the alert feed shows an audio event.
- Measured (G3, oracle recogniser built from truth words with seeded errors, English TTS stand-in for 6 languages, so this tests timing, VAD, rules and mute application, NOT real recognition): PII mute coverage 95.6 % (target 90), digit coverage 98.6 % (95), over-mute 12.7 % (15), pipeline p95 2.49 s (3.3), mouth blur 98.2 % (95), API-down fail-safe: PII muted 100 % and all voiced speech 99.99 %. Local-only fallback PII 90.2 % (70). Weak: spoken UPI IDs about 85 %, hard-negative number phrases muted 18.6 % (not gated). A blind held-out audio set is not built.
- Not verified: real Sarvam or Groq latency (no keys), real Hinglish speech, mute granularity on Sarvam (segment-level, up to 1.5 s plus padding, D-4/D-36).
- Fallback if Wi-Fi or Sarvam is down: the audio host races Sarvam against local faster-whisper (base on CPU in `dev`, small on GPU in `demo`) and mutes every voiced segment with no verdict (fail-safe, `failsafe_mute_voiced`). Say "the demo will sound choppy when the API is down; that is the safe direction". Offline proof: `SS bash scripts/gate_phase3.sh --plan`, report above.

### 5.4 Chat shield and moderator console
- Do (if the live chat process is wired by then): teammate posts Hinglish abuse ("bc ... " style, their own wording) and a fake UPI link in the chat; streamer overlay shows the hidden counter only; `/mod` queue shows the item with approve, delete, timeout, dismiss.
- Today: the classifier, mod queue, overlay feed, Twitch/YouTube connectors and platform actions are tested against fakes and mock servers (`eval/mock_platforms/`, `chat/testing/`); there is no runnable `python -m chat.main`. Do not present `/mod` as a live feed. Show instead `SS python -m pytest chat -q` (779 passed, 1 live skip at last report) and `chat/classify/README.md` (taxonomy and policy), and the report `eval/reports/2026-11-21-phase4.md`.
- Measured: gate set (templated, written with the chat author's visibility: optimistic) 100 % on abuse, scam, fake UPI, doxxing, 0/698 clean false positives, p95 0.75 ms. **Blind held-out set** (1,513 messages, rules only, no LLM): abuse 82.1 % (target 85; hard-flag only 42.7 %, the rest are holds for the moderator), scam 95.8 %, fake UPI 80.8 % (target 98), doxxing 88.7 % (hard only 75.3 %), clean false positives 1.9 % (13 of 667). Abuse and doxxing need the Groq stage to close the gap; it cannot be measured without a key.
- Policy to warn about: real profanity used as banter ("bc kya clutch tha") is held for moderation even in friendly framing, so a streamer in Indian gaming chat will see many holds (`ALLOW_PROFANE_BANTER` flips it).
- Platform actions: delete works on both platforms in the mock; YouTube timeouts cost 200 units each and are off by default; quota 10,000 per day supports about 3 to 4 streamed hours (see `docs/COST.md`). Live endpoints UNVERIFIED.
- Fallback: with no network, run only the classifier tests and show the mod console UI against test fixtures (UI tests only, say so).

### 5.5 Strike guard and risk meter (NOT BUILT per STATUS)
- Do not demo live unless the lead has finished T5.1/T5.2 and wired `guard` into the engine. `guard/music/` and `guard/risk/` exist as untracked files; `guard/tests/test_guard_risk_profanity.py` exists. `guard/music/library/` is empty, so music matching needs the user's 5 clips (`guard/music/build_library.py`). YAMNet weights are in `models/weights/yamnet.onnx`. Never claim Content ID equivalence: it detects music and fingerprint-matches a small demo library.
- Gate 5 has not been written or run (no `scripts/gate_phase5.sh`).

### 5.6 Live captions (NOT BUILT)
- Planned: Sarvam Mayura or sarvam-translate through the API (about INR 65 per hour typical, `docs/COST.md`) or IndicTrans2 locally in `demo`. Muted spans appear as `▮▮▮` so PII is never burned in (D-30). Renderer: `engine/captions.py` with Noto fonts and libraqm; shaping goldens are checked by eye. Not wired live; T5.3 pending. IndicTrans2 needs a Hugging Face token with the model terms accepted.

### 5.7 Safety report
- Page is a placeholder. Show the data: `GET /api/events/summary` and `GET /api/events` (streamer token, filters by modality, category, since, until). Records hold category, timestamp, confidence and bounding box; the contracts forbid free text and images (`contracts/privacy.py`, `test_no_free_text.py`). The alerts view is allowlisted (no bbox, source, pts).

## 6. Fallbacks (decide before going on stage)

| Failure | Do |
|---|---|
| Wi-Fi or venue network down | Everything video runs locally. Audio falls back to local faster-whisper then fail-safe mute; chat classifier runs rules only (no LLM; unserved borderline messages go to HOLD_FOR_MOD after 10 s). Streaming to YouTube is impossible: show the `shielded` tap or loopback sink on a local player. |
| Sarvam or Groq key rejected or rate-limited | Race and failover are automatic (Sarvam, then local; Groq Whisper is failover only; Groq cap is 20 to 30 requests per minute). Expect more over-muting. |
| GPU or ONNX Runtime CUDA fails on the 5050 | `bash scripts/run_engine.sh dev` (CPU, stride 3). CPU is the default path and was used for most measurements. |
| Engine overloaded (fps drops, many slates) | Close other apps; `dev` profile; lower OBS to 720p30 exactly; stride is a config key (`detect.stride`), not code. |
| YouTube rejects the stream or live not enabled | Use Twitch, or the local sink. Always keep an unlisted test stream ready. |
| Phone ingest | Not built (no pairing QR, firewall rule pending). Use OBS on the laptop and say the phone path is on the roadmap. |
| Anything else | Play the recorded backup (to be recorded; not yet done) and walk through the reports. |

Two profiles, same code: `config/profiles/dev.yaml` (CPU, stride 3, GPU cap 3.5 GB, local STT base, captions via Sarvam) and `demo.yaml` (GPU on, stride 2, 7 GB cap, local STT small on CUDA, captions via local IndicTrans2). The engine's own fail-safe (mask or mute when unsure, slate on a missed deadline) is the real fallback: failures show up as extra masking, not leaks.

## 7. Likely judge questions and honest answers

1. **How do you blur a number after it has been said?** A 4 s delay on both audio and video; detection finishes before output, like a TV broadcast delay. The delay is a setting (3 to 5 s). Capture-to-egress delay p95 was 4.7 s on a loaded laptop with the max 5.4 to 5.5 s. Viewer-side YouTube latency adds its own 3 to 5 s and is not measured.
2. **What if it misses an Aadhaar card?** It fails safe: slate when a lane is late, tracking holds masks, `mask_when_unsure`, retro hold masks the last 3 s, and the hold button. On synthetic SPECIMEN cards at clean and light quality it masked 100 %; at heavy degradation (blur, compression, dark) recall is far lower (Aadhaar clips 0.55, plates about 4.5 %, OTP 0 %, game IDs about 5 %). All numbers are synthetic and optimistic; none are from real phone footage or real cards.
3. **Show me precision and recall.** Gate 2 reports per category with confidence intervals, plus the blind chat set. Gate 2 and Gate 1 are still failing on some rows (allowlisted-face visibility, ACCURATE lane latency, delay maximum). We report them as failing.
4. **Is it real-time?** Delay and fps held on a loaded laptop (31 fps, p95 delay 4.7 s) for 45 s legs; the full 120 s gate, a 30 min soak and the SRT 4G network test have not been run.
5. **Does the audio really work in Hindi?** Not proven. The audio gate used an oracle recogniser with seeded errors and an English synthetic voice; real Sarvam and Groq calls are untested. Rules cover Hindi, Kannada, Tamil, Telugu and Bengali number words, but spellings are unverified on real speech.
6. **Doesn't YouTube already moderate chat?** Their filters are blocklist-based and English-first. Our rules alone catch about 82 % of Hinglish abuse and 96 % of scam links on a blind set, and hold the rest for a moderator; abuse and doxxing need the Groq stage, which we could not measure without a key. Fake UPI recall is 80.8 % against a 98 % target.
7. **Do you store faces, streams or chat?** No. Embeddings are RAM-only with a 12 h TTL (opt-in encrypted persistence), no frames or audio are written, event logs keep category, time, confidence and box only, flagged chat text is never logged, moderator queue is RAM-only with a TTL. This is enforced by contract tests, not only by convention.
8. **Do you send streamers' audio to third parties?** Only audio chunks (speech segments) go to Sarvam or Groq, and chat text to Groq; video never leaves the machine. Nothing is stored by us. A local fallback (rules plus faster-whisper) exists, but its quality on Hindi is poor (a spotting fallback).
9. **Is this just PrivaStream or IRL Shield?** We reused tested parts (credited in `docs/THIRD_PARTY.md`: relay, engine, detectors, audio PII, web components) and added the Indian layer, the audio mute with mouth blur, the chat shield and the product. Offleak (AGPL) was not copied.
10. **Music and copyright?** Detect music and fingerprint-match a small demo library. Not Content ID. (Not built yet.)
11. **Who pays, what does it cost?** Local processing, so no hosting. API cost about INR 24 per streaming hour typical (INR 10 to 63), INR 89 typical with API captions; every request rate is an assumption and no key has been used (`docs/COST.md`). Business model (free tier, creator agencies, esports organisers) is an idea, not validated.
12. **Licences?** Ultralytics (AGPL) and InsightFace weights (non-commercial) are optional adapters off the default path (YuNet and SFace are used); marked "replace before commercial release". The NASA public-domain portraits are the test faces.
13. **What about phones?** The phone relay is not built (no pairing QR, mirrored networking pending). Desktop OBS works.
14. **Why are there so many chat holds?** Policy: profanity in any framing, vague threats and sexual comments go to the moderator rather than being silently allowed.

## 8. Verified vs not verified

| Item | Status | Evidence and caveat |
|---|---|---|
| Delay <= 5 s p95, fps >= 29 | PARTLY (45 s legs, loaded dev laptop, CPU and GPU) | p95 4.71 / 4.72 s, max 5.44 / 5.50 s (limit 5.5), 31 fps. Full 120 s gate, soak and RTX 5050: NOT RUN |
| Fail-safe (no unmasked frames when a lane dies) | YES (G1 fail-safe leg, earlier run) | 0 card frames after the hang |
| Hold within one frame, retro hold | YES in engine tests (<= 1 frame); eval hold leg alignment OPEN | |
| Relay encode to RTMPS | YES to a local loopback sink only | h264 720p30, AAC 48 kHz, 0 dropped in a 60 s soak, builder-reported, reviewer pending. Real YouTube/Twitch: NOT VERIFIED |
| Video recall per category | YES offline, synthetic, CPU, clean+light | G2 still FAIL on allowlisted faces and the ACCURATE lane latency; heavy level well below target |
| Faces allowlist | PARTLY | privacy-affecting planner change awaits reviewer; 8 NASA people, no teammate footage |
| Audio PII mute, mouth blur | PARTLY | oracle recogniser, English TTS; real STT NOT RUN |
| Sarvam and Groq calls | NOT VERIFIED | no keys |
| Chat classifier | PARTLY | gate set optimistic; blind set abuse 82.1 % and fake UPI 80.8 % below target; Groq stage unmeasured |
| YouTube and Twitch live chat, deletes, timeouts, OAuth | NOT VERIFIED | mocks only; live chat process not built |
| Moderator console | PARTLY | API and jsdom tests; no browser, no live feed |
| Strike guard, captions | NOT BUILT (STATUS) | files exist, unreviewed |
| Safety report page | NOT BUILT | events API exists |
| Phone ingest, hotkey | NOT BUILT | |
| Web app in a real browser, mobile viewport, accessibility | NOT VERIFIED | Playwright and axe are T6.5 |
| RTX 5050 and sm_120 | NOT VERIFIED | |
| Cost per hour | ESTIMATE only | `docs/COST.md` |
| Red team, 30 min soak, 4G netem | NOT RUN | Phase 7 |

## 9. Rehearsal log (fill in)

| Date | Machine | Profile | Scenes that worked | What failed |
|---|---|---|---|---|
| | | | | |

The plan requires three full runs on the RTX 5050 before the demo (PLAN section 5, Phase 7).
