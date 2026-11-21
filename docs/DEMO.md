# Stream Sarthi: demo script and run book (T7.4, refreshed)

Refreshed 2026-11-21 against the uncommitted tree after Phases 5 and 6 landed and against `docs/STATUS.md`. Every number is from STATUS with its caveats. **Nothing has been verified live**: no Sarvam, Groq, YouTube or Twitch call has ever been made (all keys in `.env` are empty), the demo laptop (RTX 5050) has not been used, and no full demo rehearsal has happened. Most tasks are "built, reviewer pending" and builder-reported.

## 0. What can be shown today

Tags: **RUNS** (entry point exists and was run by a builder, locally, no keys), **PARTS** (built and tested on fakes/mocks/synthetic data; live wiring unproven), **NOT BUILT**.

| Piece | Tag | Evidence and gap |
|---|---|---|
| One-command demo: `scripts/demo.sh --raw --gpu` and the `/demo` page (RAW vs PROTECTED panes, state chip, captions, privacy-proof counter, risk meter, hold bar) | RUNS (keyless, SPECIMEN only) | Verified by the builders in a real browser on a quiet machine WITH the GPU: Aadhaar masked by a solid box, UPI QR blacked out, Hindi signboard blurred, faces blurred, risk meter moves Low / Watch / High. Without the GPU the protected pane stayed on the privacy slate for the text-heavy scenes even on a quiet machine: always use `--gpu`. Not rehearsed on the RTX 5050 |
| Go-live from the web app: `/go-live` page, `POST /api/session/start` (spawns the real engine, and `chat.main` only if chat is enabled), stop, session chip | RUNS (keyless) | `scripts/e2e_smoke.sh` passed 30 of 30 on its second full run, dev profile, no keys (the first run was 29 of 30: one privacy-scan hit that could not be reproduced). The web `/go-live` screen itself: jsdom tests only, no browser run |
| Rehearsal mode (keyless: no YouTube/Twitch, output only to the local `shielded` tap) | RUNS | `POST /api/session/start` with `{"rehearsal": true}`. There is no web toggle for it: call the API with the streamer token |
| Relay, delay buffer, mask compositor, fail-safe, slate | RUNS | G1 main and fail-safe legs pass on a quiet machine (section 8); real YouTube/Twitch egress never tried (loopback sink and tap only) |
| Hold | RUNS | Web button on `/live` (and `H` key on the page), `POST /api/hold`, chat `!hold` (code and tests; the live chat process has only run on fakes). Slate and audio silence verified; the G1 hold leg's two alignment checks are still failing (timing-log alignment, not behaviour) |
| Windows global hotkey | **NOT BUILT** | `scripts/hold_hotkey_win.py` does not exist. The API side exists (`POST /api/auth/hotkey-token`); no helper uses it. Say "hold button, API and chat command" |
| Video PII (faces + allowlist, Aadhaar, PAN, UPI QR, plates, signboards, phone/PIN/UPI/email, OTP, game IDs, tracking) | PARTS | Offline scoring, synthetic SPECIMEN, CPU. Only the cards detector has been measured end to end through the engine (G1, e2e smoke) |
| Audio PII mute, mouth blur | PARTS | Oracle recogniser and English TTS stand-in only; real speech-to-text never run. The real audio host does heartbeat inside the engine (e2e smoke) |
| Chat shield, moderator console | PARTS | `python -m chat.main` exists (live YouTube/Twitch connectors, its own ZeroMQ bus) but ran only against fakes and the real engine broker in a throwaway run. OAuth, live chat, live deletes: NOT VERIFIED. `/mod` has the polish round (keyboard shortcuts, stale-list notices); no browser run |
| Strike guard: risk meter, music, profanity | PARTS | Loaded by the engine through `runtime.extras` (`guard.risk.service:build`, `guard.music:build`); risk meter and captions control are on `/live`; `GET /api/risk`. Music only on SYNTHETIC songs (the user's Bollywood clips are not there); the engine feeds audio to the music service through a non-blocking tap, tested with fakes (a real `guard.music:build` run inside the engine is not recorded) |
| Live captions | PARTS | `audio/captions` (Sarvam translate, never called live) plus the renderer `engine/captions.py` (5 scripts, shaping checked against our own goldens and an independent Pillow+raqm raster). IndicTrans2 (demo profile) has NO weights: needs a Hugging Face token and a `trust_remote_code` conversion decision |
| Safety report | RUNS (keyless) | `/report/[sessionId]` page, `GET /api/report`, `/api/report/{id}`, `/printable`, `/export?format=json|html`; built in the e2e smoke (50 to 76 events, privacy scan clean). The page was built to the spec's shape and its reconciliation with the real API shape was still assigned |
| Phone ingest (Moblin or Larix) | PARTS | `GET /api/pairing` (RTMP/SRT URLs, Moblin import URL, no keys), QR PNGs at `/api/pairing/rtmp.png` and `/api/pairing/srt.png`, `relay/pairing.py`, SRT presets and passphrase (`relay/srt.py`), hold on transport loss measured on loopback. No real phone, no 4G, mirrored networking and the firewall rule are user admin steps not done (WSL is on NAT) |
| Red-team suites | RUNS | `python -m eval.run redteam-video`, `redteam-audio`, `redteam-chat`; reports in `eval/reports/2026-11-21-redteam-*.md` |
| Gates | G0 PASS, G1 FAIL on 2 alignment checks only, G2 FAIL at clean+light, G3 and G5 pass on synthetic/oracle data, G4 pass on the gate set but below target on the blind set, G6 and Final do not exist | `scripts/gate_phase0.sh` to `gate_phase5.sh`; there is no `gate_phase6.sh` or `gate_final.sh` |

**Known gaps in the demo (state them if asked):**
- The "enrolled face stays visible" moment needs a demo enrolment step: `scripts/demo_enrol.py` is being added and does not exist yet. Until then both faces in the reel are masked.
- The state chip mislabels SLATE while live masked video is shown (fix in progress).
- Spoken-PII muting on the reel's flite TTS voice is untested with a real recogniser; the reel does not exercise Sarvam or Groq.
- Nothing is live: no keys, no YouTube or Twitch; the demo runs as a keyless rehearsal session on the local tap.
- The browser test gate (Playwright) cannot run until `sudo apt-get install -y libnss3 libnspr4` is done on the machine (a user admin step); no accessibility or mobile-viewport run.
- One demo tab only (preview cap 4 clients per stream, `DEMO_MAX_CLIENTS`).
- Chat in the demo is a scripted mock YouTube feed through the real chat shield, not live chat; it runs once per session.

## 1. The 5-minute run order (tied to the 90 s demo reel)

The one-command launcher (section 3) loops a 90 s SPECIMEN reel (video and speech audio in ONE sender process) into a keyless rehearsal session. The reel timeline below is from `eval/sender/demo_scenes.py` (scene lengths) and `eval/data_gen/demo_audio.py` (what is said); the exact spoken times are in `/tmp/streamsarthi-demo/demo_reel_timeline.json`, written by `scripts/demo_assets.sh`. Times are seconds into the reel as seen on the RAW (source) pane; the PROTECTED pane, audio and captions show it about 4 to 5 s later (the broadcast delay). Open `/demo` (side-by-side RAW and PROTECTED, state chip, captions, privacy-proof counter, risk meter, hold bar). The reel loops every 90 s, so the story can start at any loop boundary.

| Reel time | Scene (source caption) | What to say and point at |
|---|---|---|
| 0-7 s | `demo_aadhaar_static`: "SPECIMEN Aadhaar (still)" | Problem in one line: one frame is enough to dox a streamer. RAW shows the card, PROTECTED shows a solid box. Mention the 4 s delay. Speaker: "welcome to the stream... testing the privacy shield" appears as a caption |
| 7-15 s | `demo_aadhaar_moving`: "SPECIMEN Aadhaar (moving)" | The mask follows the moving card (tracking) |
| 15-22 s | `demo_pan`: "SPECIMEN PAN card" | Same for PAN |
| 22-30 s | `demo_upi_qr`: "SPECIMEN UPI QR" | The QR is decoded, `upi://` checked, blacked out. A non-payment QR is not masked (not in the reel) |
| 30-38 s | `demo_plate`: "SPECIMEN number plate" | Indian plate masked |
| 38-46 s | `demo_signboard`: "Hindi signboard (SPECIMEN)" | Indian-script signboard blurred without reading it (mask-all-text) |
| 46-55 s | `demo_otp_phone`: "SPECIMEN phone with an OTP" | OTP notification masked. **Phone number spoken at 50.1 to 56.2 s**: output goes silent over the digits, the proof counter ticks, the alert feed shows an audio event (see the caveat on real recognition, section 5.3) |
| 55-64 s | `demo_bgmi_lobby`: "SPECIMEN BGMI lobby" | Player id, room id, password masked |
| 64-78 s | `demo_faces`: "Enrolled face + stranger" | Faces blur; the enrolled face is meant to stay visible once the demo enrolment step exists (section 0). The two people swap sides halfway. **UPI ID spoken at 70.5 to 75.5 s** ("demo dot streamer zz at ybl"): muted. A mild curse ("oh damn that was a crap shot", nominally around 73 to 74 s, check the timeline JSON) drives the risk meter to Watch or High |
| 78-90 s | `demo_calm`: "Calm scene: nothing to hide" | Nothing is masked: the shield does not blur everything. **Music bed at 80.5 to 83.5 s** (a synthetic song in the demo fingerprint library): risk meter and fingerprint warning. **Chat `!hold` at 84 s** (moderator): the slate covers the PROTECTED pane (retro hold masks the preceding 3 s too); the broadcaster's `!unhold` at 88 s releases it. Closing line, then open the safety report |

Chat runs alongside from the scripted feeder (`eval/demo/chat_feeder.py`, a mock YouTube chat feeding the REAL chat shield process). Times are seconds after the feeder starts (3 s start delay by default; chat reaches viewers about 5 s after the video): 1 friendly; 5 friendly; 10 friendly; **15 Hinglish abuse (Latin letters)**; **21 fake UPI payment link**; 27 moderator welcome (staff, never hidden); 33 friendly; **39 Devanagari abuse**; **44 doxxing attempt ("iska ghar kahan hai")**; 50 friendly; **58 link-shortener "free recharge" scam**; 66, 74, 81 friendly; **84 `!hold` (moderator)**; **88 `!unhold` (broadcaster)**; 89.5 friendly. Flagged items appear in `/mod` (moderator link) and only as a hidden counter for the streamer. The feeder is started once per session (no `--loop` in `scripts/demo.sh`), so chat follows the reel only for the first pass; the `!hold` at 84 s then lines up with the calm scene of the first loop. Print the plan: `SS python -m eval.demo.chat_feeder --print-plan`.

Wrap-up (about 4:30): the section 8 table with its caveats, `docs/COST.md` (typical INR 24 per hour, assumed rates, nothing measured live), then the safety report (`/report/<session id>`): category, time, confidence, never images or text. Opening and closing slides (the real, sourced incidents) are the user's to supply.

## 2. Pre-flight

**Two-minute pre-flight (right before going on)**
1. Close every other app (browsers except one tab, IDEs, chat clients, other WSL jobs). The delay maximum was within 0.2 s of its limit on a quiet machine.
2. `SS python scripts/doctor.py --profile demo --gpu`: exit 0 or 1 (warnings only) is fine, 2 is blocking.
3. `nvidia-smi` (run in WSL, or `SS nvidia-smi`): the RTX is visible and no other job holds it. The demo holds `/tmp/streamsarthi-gpu.lock` for its whole run and waits up to 10 minutes for it.
4. Ports free: 8000 (API), 3000 (web), 8890 (SRT), 1935 (RTMP), 9997 (MediaMTX API). The launcher refuses to start if any is taken (stop a previous demo, gate or smoke run first).
5. Power plugged in, performance mode, only ONE browser tab on `/demo`.

**Day-before checklist**

`SS` below means the wrapper, run from Git Bash on Windows:
`MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh`
(no `$VARS` or Windows paths inside the command; see `docs/AGENT_BRIEF.md`). Run heavy jobs one at a time: the wrapper caps memory at 3.5 GB per command and a runaway test once froze WSL.

1. **Hardware**: the RTX 5050 laptop, plugged in, performance power mode. Nothing else heavy open: delay maximums were within 0.2 s of the limit even on a quiet machine.
2. **WSL**: `.wslconfig` has `memory=6GB`, `swap=8GB`, `vmIdleTimeout=-1`. Mirrored networking and the Hyper-V firewall rules for UDP 8890 and TCP 1935 are user admin steps (docs/PLAN.md section 9) and not done; without them a phone cannot reach the engine. OBS on the same laptop works through loopback.
3. **Doctor**: `SS python scripts/doctor.py --profile demo --gpu` (exit 0 ok, 1 warnings, 2 blocking). Dev result: exit 1, warnings only (NAT, empty keys, `fpcalc` and `espeak-ng` missing). ONNX Runtime on sm_120 (RTX 5050) is untested; the first CUDA run JIT-compiles slowly: warm it up. If CUDA fails, run `dev`.
4. **Tools and models**: `SS python -m models.prepare --verify-only`; `.tmp/tools/mediamtx-v1.21.4/mediamtx` present (`SS bash scripts/fetch_tools.sh`, needs network, do it before demo day).
5. **Smoke test with no keys**: `SS bash scripts/e2e_smoke.sh --duration 45` (about 45 s plus about 30 s engine start; exit 0 all pass, 1 a check failed, 2 environment). Run it on the demo laptop first.
6. **Keys** in `.env` (never printed): `SECRET_KEY` (long random string), `SARVAM_API_KEY`, `GROQ_API_KEY`, `YOUTUBE_STREAM_KEY`, `TWITCH_STREAM_KEY`, `YOUTUBE_OAUTH_CLIENT_ID/SECRET`, `TWITCH_CLIENT_ID`; stream keys can also be typed on `/go-live` (RAM only). Demo captions with IndicTrans2 additionally need `HF_TOKEN` and the model terms accepted, plus the lead's decision on converting it (`trust_remote_code`). YouTube live must be enabled on the channel at least 24 h before; keep an unlisted test stream. For YouTube chat, the OAuth consent screen needs the streamer as a test user (7-day refresh tokens). `python -m chat.main --login` prints the consent URL or device code (the only thing it prints).
7. **Live API spike, once**: `SS python -m audio.spikes.stt_spike` with the keys exported (fills in Sarvam and Groq latency, and settles D-36: `saaras:v4` codemix mode).
8. **Suites**, one directory per process: `SS bash scripts/mem_check.sh contracts/tests engine vision models audio chat app/api`.
9. **Reports to have open**: `eval/reports/2026-11-21-phase2-offline-d48.md`, `-phase3.md`, `-phase4.md`, `-phase5.md`, `-redteam-video.md`, `-redteam-audiochat.md`.
10. **Backup recording** of a full run on the desktop: NOT YET RECORDED.
11. Hackathon rules on pre-built code and AI-tool disclosure: open user item.

## 3. Start sequence: one command

```
SS bash scripts/demo.sh --raw --gpu
```
`SS` is `MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh` (so the full command is `MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh bash scripts/demo.sh --raw --gpu`). Options: `--gpu` (always use it: vision on the NVIDIA GPU, CUDA start-up about 50 s, holds `/tmp/streamsarthi-gpu.lock`), `--raw` (enables the RAW preview pane, loopback only), `--no-build` (skip the web build), `--no-chat`, `--no-audio` (skip the TTS audio build).

What it does, in order: builds the demo assets (speech audio, NASA face portraits with a hash check, chat plan; `scripts/demo_assets.sh`, log `/tmp/ss_demo_assets.log`); builds the web app (`/tmp/ss_webbuild.log`); starts the API with a random `SECRET_KEY` (spawns the real engine when the session starts; log `/tmp/ss_api.log`) with `DEMO_RAW_PREVIEW=1` for `--raw`, and, unless `--no-chat`, `CHAT__ENABLED=true` and `SESSION_CHAT_CMD="python -m eval.demo.chat_feeder --profile {profile}"`; starts the web server on 127.0.0.1:3000 (`/tmp/ss_web.log`); then `scripts/demo_run.py` starts a keyless REHEARSAL session (`POST /api/session/start`, `rehearsal: true`, 4.0 s delay), waits for the engine to be ready, waits for the models (45 s with `--gpu`), turns captions on, and loops the 90 s reel into the engine's SRT ingest. It prints the local login link (`http://127.0.0.1:3000/#token=...`, it carries your token). Open it, then go to the Demo page in the top menu (`/demo`). **Ctrl-C stops everything.** Expect about 1.5 to 2 minutes from launch to a live pane.

Other pages: `/go-live`, `/live`, `/enrol`, `/controls`, `/mod` (moderator token via `POST /api/auth/mod-invite`), `/report/<session id>`. The hand-started sequence (API: `SS python -m app.api.main`, web: `SS bash scripts/web.sh run dev`, engine by hand: `SS bash scripts/run_engine.sh demo`, keyless session: `POST /api/session/start` with `{"rehearsal": true}`) still works for debugging.

Constraints: ONE demo tab (the RAW and PROTECTED previews share ffmpeg; cap 4 clients per stream, `DEMO_MAX_CLIENTS`, a second tab or a reload loop can starve the panes). The RAW pane shows unmasked SPECIMEN video: it works only with `--raw`, only from loopback, and must never be shown on a stream. After a sender restart the protected output recovers in about 3 to 8 s (relay timeline-reset fix).

**If something goes wrong**

| Symptom | Likely cause and action |
|---|---|
| Protected pane blank | More than one demo tab open, the GPU not in use, or the machine loaded. Close extra tabs (only one `/demo` tab), confirm the launcher was started with `--gpu`, close other apps, wait up to 8 s after any sender restart. |
| "Open the link from the terminal" | The login token changes at every API start. Use the link printed by the latest launch; clear site data for `127.0.0.1:3000` after a restart. |
| Protected pane stuck on the privacy slate | CPU path or an overloaded machine: the text-heavy scenes (signboard, OTP phone, BGMI lobby) exceed the CPU lane budget and the engine slates instead of risking a leak. Restart with `--gpu`, close other apps. On the CPU path this happened even on a quiet machine. |
| No chat items in `/mod` or the hidden counter | Chat not enabled: launched with `--no-chat`, or `CHAT__ENABLED` / `SESSION_CHAT_CMD` missing from the API environment. Check `/tmp/ss_api.log` for the chat process; remember the feeder runs once per session (first loop of the reel). |
| `port ... is already in use` | A previous demo, gate or e2e smoke is still running: stop it (`pkill -f engine.main`, or Ctrl-C the old launcher), then relaunch. |
| Launcher waits on the GPU lock | Another GPU job holds `/tmp/streamsarthi-gpu.lock`; it waits up to 600 s, then exits. Stop the other job. |
| "demo_assets failed" or "web build failed" | Read `/tmp/ss_demo_assets.log` or `/tmp/ss_webbuild.log`. The portraits need network once (hash-checked). |
| "API did not start" | Read `/tmp/ss_api.log` (missing dependency, port taken). |
| State chip says SLATE while masked live video is on screen | Known mislabel (a fix is in progress); trust the video. |
| No audio events, silent captions | Audio host still loading (wait for the warm-up), or `--no-audio` was used. Real recognition is not used in the demo: the reel's voice is flite TTS (section 5.3). |

## 4. SPECIMEN inputs (synthetic, visibly marked, never real data)

Seeded and deterministic. Every card carries a SPECIMEN watermark; Aadhaar numbers are Verhoeff-valid and start with 0 or 1; PAN 4th letter X; UPI IDs `zz-specimen-<hex>@...` (`eval/README.md`).

- Cards and QR stills, already generated: `eval/data/generated/specimen_v0_seed1234/` (`aadhaar_000x.png`, `pan_000x.png`, `upi_qr_000x.png`, `qr_negative_000x.png`). Regenerate: `SS python -m eval.run gen-specimen --out /tmp/specimen --n 5`. Hold them in an OBS image source or on a phone screen.
- Full video suite (plates, signboards in 5 scripts, OTP and BGMI/Free Fire screens, faces, clips): `SS python -m eval.run gen-video --out /tmp/video --n 40 --clips 2` (about 600 MB; needs network the first time for the SHA-checked face portraits).
- Faces: the eval set is NASA public-domain portraits (user-confirmed, D-48, `eval/data/generated/faces/ATTRIBUTION.md`). For the live enrolment use consenting teammates only: `/enrol`, 8 frames over about 3 s, RAM only, 12 h TTL. `face_match_cos` still needs a re-tune on teammate footage.
- Phone number for the audio scene: a teammate reads a MADE-UP 10-digit number starting with 9. Never a real one. Test audio from the generator: `eval/data/generated/audio/clips/*.flac` (English flite TTS, an English phonemiser for the other languages).
- Chat: typed by a teammate with made-up handles (`zz-specimen-1a2b@oksbi`) and abuse of their own wording. More: `SS python -m eval.data_gen.chat --out DIR`.
- Music: the library has 5 SYNTHETIC songs only. Real Bollywood clips (user-supplied, local only) go through `guard/music/build_library.py`; until then the "Bollywood track" scene cannot be real.

## 5. Per-feature run book

### 5.1 Relay, delay, hold
- Do: start a session, publish the source, watch `/live` reach `live`. Press HOLD (button, `H`, `POST /api/hold` with `{"active": true}`, or `!hold` in chat as broadcaster or moderator; `!unhold` is broadcaster only). Release.
- See: slate (English and Hindi) within one frame; the last `retro_seconds` (3.0, clamped to delay minus 0.5) of buffered frames and audio masked as well; audio silent during the hold (max -120 dBFS in G1).
- Measured (G1, full run, quiet machine, CPU / GPU RTX 3050): capture-to-tap delay p95 4.458 / 4.435 s (limit 5.0), max 5.318 / 4.99 s (limit 5.5), 30.4 fps, recall 1.0 on 2,178 / 2,297 card-visible frames, 0 Verhoeff-valid reads, slate 6.7 % / 2.9 % (limit 10 %). Fail-safe leg (a lane hangs): 0 card frames, 1,052 slate frames. The e2e smoke (real engine, no keys) saw delay p50 4.68 s / p95 4.85 s and 0 leaks over 162 card-visible frames. NOT run: soak, 30 min, live YouTube, RTX 5050. G1 is FAIL only on the hold leg's two alignment checks.
- Hotkey: **not built** (`scripts/hold_hotkey_win.py` does not exist). Say so; use the button.
- Fallback: show the local tap or `SS python -m eval.run loopback --seconds 60` if egress fails.

### 5.2 Video PII
- Do: show each SPECIMEN on the source and the output; toggle categories at `/controls` (switching off Aadhaar, PAN or UPI QR asks for confirmation).
- Offline, synthetic, CPU, clean and light levels only (D-48): faces, Aadhaar, PAN, UPI QR, signboard, phone, UPI ID, email, game IDs and plates masked 1.000; PIN 0.912; OTP 0.917 on stills. Failing at clean+light in the report: allowlisted faces visible (0.208 on stills, 0.750 clips settled; a later round reports 22/24 = 0.917 stills and 0.968 clips in the builder's harness, not re-run through the gate) and the ACCURATE lane p95 (530 ms on the shared machine, 453 ms on a quiet machine, budget 450 ms). G2 is FAIL until re-run.
- Heavy level (blur, heavy JPEG, dark; stress only, D-48): plates about 4.5 % to 7 %, OTP 0 to 31 %, game IDs about 5 to 13 %, Aadhaar clips 0.55, phone clips 0.33, even after the T7.5 mask-when-unsure fallbacks (OTP banner 0/16 to 5/16, BGMI 3/31 to 4/31).
- Video red team (quick subset: 46 cases, 727 frames, one seed): control 0/27 leaks; angle, distance, glare, screen-of-screen, photo-of-photo 0 leaks; leaks in `enter_leave` (a card entering or leaving the frame), `motion` (pans), `occlusion`, `flash`, `scene_cut`. The one confirmed leak (a card 40 % occluded from the left) was fixed (9/9 to 0/9 cases). Still open: card entering fast (7 of 35 frames leak after the fix), a card at the frame edge showing 35 % (100 % leak), QR entering or flashing, scene cuts, fast pans, a PAN occluded from the top. The full grid, more seeds and GPU were not run.
- Demo reel faces: the enrolled-face moment needs `scripts/demo_enrol.py` (not yet present); until it exists both faces are masked.
- Faces: stranger masked 97.2 % with 0 runs showing a stranger; allowlisted visible about 94 to 99 % once settled; the first ~1 s after a face appears is masked on purpose. The planner's allow-release was reviewed (APPROVE).
- Signboards: mask-all-text, so signage goes dark unread; it also masks text-heavy screens. Keep `signboard.enabled=false` for gaming and screen scenes.
- Fallback: show the per-category report `eval/reports/2026-11-21-phase2-offline-d48.md` and the stills; `SS bash scripts/gate_phase2.sh --plan`.

### 5.3 Audio PII mute and mouth blur
- Do: the teammate reads the made-up number with an enrolled face on camera. Output audio drops; the mouth is blurred for the muted span; an audio event appears in the alerts.
- Measured (G3, ORACLE recogniser built from truth words with seeded errors, English TTS, so timing, VAD, rules and mute application only; NOT recognition accuracy): PII mute coverage 95.95 % (target 90), digits 98.8 % (95), over-mute 14.4 % (limit 15, at the edge), pipeline p95 2.49 s (3.3), mouth blur 98.3 %, API-down PII 100 %, API-down all voiced speech 99.99 %, local fallback PII 91.6 % (70).
- Audio red team (oracle, record-only): after fixes an empty transcript with no error now mutes the segment (it leaked 100 % before). Still above the author's ceilings: letter-by-letter spelling 21.5 % (ceiling 10), ASR drops 17.5 %, phonetic UPI 16.7 %, overlapping speakers 18.6 %, fast speech 26.7 % (Silero does not detect 2.4x sped-up speech). A UPI id spelt letter by letter cannot be recognised within the ~2.5 s the delay allows.
- Not verified: real Sarvam or Groq latency, real Hinglish or regional speech, any real-STT accuracy. Mutes are segment-level (up to 1.5 s plus padding) because Sarvam gives no word times.
- Fallback with no network or no key: the audio host races Sarvam against local faster-whisper (base on CPU in `dev`) and mutes every voiced segment that has no verdict, so speech is choppy but safe. The engine also mutes all audio if the audio host stops heartbeating (grace clamped to delay minus 0.5 s).

### 5.4 Chat shield and moderator console
- Do (needs the live chat process and keys): the teammate posts Hinglish abuse and a fake UPI link. The streamer overlay shows only a hidden counter; `/mod` shows the queue with approve, delete, timeout, dismiss (keys D, T, J, K; staff messages cannot be deleted or timed out). Start the chat process through the session (`chat.enabled`), or by hand `SS python -m chat.main --login` (`--login` runs the OAuth flows when no token is stored; `--remember` keeps refresh tokens in the OS keyring).
- Today's evidence is fakes, mocks and the real engine broker: `SS python -m pytest chat -q` (about 940 passed, 2 live-key skips at last report) and `eval/reports/2026-11-21-phase4.md`.
- Gate set (templated, written with the author's visibility: optimistic) 100 % on abuse, scam, fake UPI, doxxing, clean false positives 0/698, p95 1.1 ms. **Blind held-out set** (1,513 messages, rules only, no LLM, the honest figure; not re-run after the red-team fixes): abuse 82.1 % (target 85; hard-flag only 42.7 %, the rest are holds for the moderator), scam 95.8 %, fake UPI 80.8 % (target 98), doxxing 88.7 % (hard only 75.3 %), clean false positives 1.9 % (13 of 667). Abuse and doxxing need the Groq stage, which cannot be measured without a key.
- Chat red team (rules-level, obedient-LLM stand-in): after fixes homoglyph, mixed script, visual tricks, URL obfuscation, UPI confusables, padding dilution and prompt injection are under their ceilings; still failing: split words 47.5 % bypass (ceiling 15) and leetspeak 24 % (15); encoding tricks (base64, hex, rot13) about 93 % pass (informational); a slur hidden in the middle of a long message of only unique words can still be missed. Viewer `!hold` spam no longer floods events; no `!hold` from non-staff or spoofed roles.
- Policy: real profanity in friendly banter ("bc kya clutch tha") is held for the moderator (`ALLOW_PROFANE_BANTER` flips it); expect many holds in Indian gaming chat.
- Platform actions: deletes and timeouts exist; YouTube timeouts cost 200 of 10,000 daily units and are off by default; one key supports about 3 to 4 streamed hours a day (`docs/COST.md`). Live endpoints: NOT VERIFIED.
- Fallback with no network: classifier tests and blind-set numbers only; do not present `/mod` as a live feed.

### 5.5 Strike guard and risk meter
- Do: the risk meter on `/live` (`GET /api/risk`: ok, watch, warn, danger) reacts to music and profanity. A library song raises it; a fingerprint match goes red.
- Measured on SYNTHETIC audio (G5 passes on it): music recall 28/30 = 0.933, speech false positives 0/20; **music-ON latency p95 4.5 s vs the 2 s in the spec** (informational, the gate checks the meter's reaction to an injected signal, 0.0 s); fingerprint 40/40 correct on 10 dB noise plus MP3, 0 wrong ids, 0/10 false matches, but 0/8 at +15 % tempo; profanity Hinglish recall 0.971 on 140 phrasings written by the eval author (clean false positives 0/48); regional lexicon is small (8/12). Real music and real speech: NOT VERIFIED. Not Content ID: it detects music and matches a small demo library.

### 5.6 Live captions
- Do: `/live` captions control (on/off, 10 languages). Captions are burned in on the delayed timeline; muted or fail-safe words appear as `▮▮▮` so PII is never shown.
- Built: Sarvam translate adapters (never called live), renderer for Devanagari, Kannada, Tamil, Telugu, Bengali (5/5 match an independent raster; Latin uses Atkinson, other scripts need their Noto font), redaction with a re-publish if a mute arrives late (the reviewer's blocker was fixed, re-review pending), timing 0/23 late with a stand-in translator. Cost via Sarvam about INR 65 per hour typical. IndicTrans2 for `demo`: no weights, needs an HF token and a trust decision. Live end to end: NOT VERIFIED.

### 5.7 Safety report
- Do: end the session; open `/report/<session id>` (also `GET /api/report/{id}`, `/printable`, `/export?format=json` or `html`; the viewer-readonly token can read; moderator tokens get 403). Totals, category timeline with hold bands, event table, guard warnings, chat counts, delay and fps.
- Privacy: only SafetyEvent fields, hold intervals, guard warnings, chat counts and timing. `validate_report` rejects strings over 64 characters, images, base64 and phone, email, UPI, Aadhaar, PAN or URL patterns on every build. Reports live in RAM (6 h, at most 8); `DELETE /api/session/{id}/data` erases one. A privacy scan of the e2e smoke's report found nothing.

## 6. Fallbacks (decide before going on stage)

| Failure | Do |
|---|---|
| Venue network down | Everything video runs locally: use rehearsal mode and the local tap. Audio falls back to local faster-whisper then fail-safe mute; chat runs rules only (no LLM; unserved borderline messages are held for the moderator after 10 s). Streaming to YouTube is not possible. |
| Sarvam or Groq key rejected or rate-limited | Automatic: Sarvam, then local; Groq Whisper is failover only. Expect more over-muting. Groq limits are unclear (`docs/COST.md` section 2): never rely on the Groq stage on stage. |
| GPU or ONNX Runtime CUDA fails on the 5050 | `dev` profile (CPU, stride 3); CPU is the default path and produced most numbers. |
| Overloaded engine (more slates) | Close other apps, `dev`, OBS exactly 720p30. Failures appear as more masking, not leaks. |
| YouTube rejects the stream or live not enabled | Twitch, or rehearsal mode with the local tap. |
| Phone ingest fails | Mirrored networking and firewall rules are not done: use OBS on the laptop and say the phone path is built but untested on a real phone and 4G. |
| Hotkey | Not built; use the button, API or `!hold`. |
| Anything else | Play the recorded backup (not yet recorded) and walk through the reports. |

Two profiles, same code (`config/profiles/dev.yaml`: CPU, stride 3, GPU cap 3.5 GB, local STT base, captions via Sarvam; `demo.yaml`: GPU on, stride 2, 7 GB cap, local STT small on CUDA, captions via local IndicTrans2).

## 7. Likely judge questions and honest answers

1. **How do you blur a number after it has been said?** A 4 s delay on audio and video, like a TV broadcast delay; detection finishes before output. Delay p95 at the egress tap was 4.46 s with max 5.3 s on a quiet laptop (45 to 120 s legs); YouTube adds its own 3 to 5 s, not measured.
2. **What if it misses an Aadhaar card?** It fails safe: slate when a lane is late, tracking, mask-when-unsure, 3 s retro hold, hold button, audio auto-mute when the audio host dies. On synthetic SPECIMEN cards at clean and light quality it masked 100 %; the red team found real misses on cards entering the frame fast, at the frame edge and during scene cuts (listed in 5.2); at heavy blur or darkness recall is far lower. Numbers are synthetic and optimistic, never from real cards or phone footage.
3. **Show precision and recall.** Gate 2 per-category reports; the blind chat set; red-team reports. Gates that still fail are reported as failing: G2 (allowlisted faces and ACCURATE-lane latency), G1 hold-alignment checks, blind-set abuse (82.1 % vs 85) and fake UPI (80.8 % vs 98).
4. **Is it real time?** 30 fps and p95 delay 4.46 s on a quiet laptop (CPU and RTX 3050), 4.85 s in the real end-to-end smoke. The 30-minute soak, a 4G network test through `scripts/netem_4g.sh` (loopback `tc netem` ran at builder level) and the RTX 5050 have not been done.
5. **Does the audio work in Hindi and other languages?** Not proven. The audio gate used an oracle recogniser and an English synthetic voice. Real Sarvam and Groq calls have never been made. Number-word rules cover five Indian languages but spellings are unverified on real speech.
6. **Doesn't YouTube already moderate chat?** Their filters are blocklists and English-first. On a blind set of 1,513 messages our rules alone flag 82 % of abuse (43 % hard-hidden, the rest held for a moderator) and 96 % of scam links, with 1.9 % false positives; the Groq stage meant to close the abuse and doxxing gap is unmeasured. Evasion tricks (split words 47.5 %, leetspeak 24 %, encodings) still get through.
7. **Do you store faces, streams or chat?** No images or raw PII at runtime; embeddings RAM-only with a 12 h TTL; flagged chat text never logged; the report has categories, times and confidences only, validated on every build; a privacy review approved the privacy-affecting parts (captions blocker, watchdog and QR fixes landed, re-review pending). Not an audit.
8. **Do you send audio to third parties?** Only audio segments (Sarvam, or Groq as failover) and chat text (Groq); video never leaves the machine. A local fallback exists but is poor on Hindi.
9. **Is this PrivaStream or IRL Shield?** Reused parts are credited in `docs/THIRD_PARTY.md`; the Indian layer, audio mute with mouth blur, chat shield, guard, captions and product are new. Offleak (AGPL) was not copied.
10. **Music and copyright?** It detects music and fingerprint-matches a small demo library; not Content ID. Tested only on synthetic songs.
11. **Cost?** Local processing, no hosting. API about INR 24 per streaming hour typical (INR 10 to 63), INR 89 typical with Sarvam captions; all request rates are assumptions and no key has been used (`docs/COST.md`).
12. **Licences?** Ultralytics (AGPL) and InsightFace weights (non-commercial) are optional adapters off the default path; marked "replace before commercial release". Test faces are NASA public-domain portraits.
13. **Phones?** Pairing QR and URLs, SRT presets and hold-on-drop are built and measured on loopback; no real phone, no 4G, networking admin steps pending.
14. **Why so many chat holds?** Policy: profanity in any framing, vague threats and sexual comments go to a moderator instead of being silently allowed.
15. **What is still open?** Hold-leg alignment in G1, G2 re-run, ACCURATE-lane latency, real-STT and real-chat verification, card-entry and edge leaks, split-word and leetspeak chat evasion, letter-spelled UPI audio, captions on the RTX 5050 (IndicTrans2), Playwright and accessibility checks (T6.5), `gate_phase6.sh` and the final gate.

## 8. Verified vs not verified (nothing is verified live)

| Item | Status | Evidence and caveat |
|---|---|---|
| Local end-to-end chain with no keys (API spawns engine, sender, tap, hold, stop, report) | YES, builder-reported | `scripts/e2e_smoke.sh`, 30/30 on the second run, dev profile, 45 s |
| Delay p95 <= 5 s, fps >= 29 | YES locally, quiet machine, CPU and RTX 3050 | G1 main leg 4.458 / 4.435 s, 30.4 fps; max 5.318 s (limit 5.5). 30 min soak, RTX 5050: NOT RUN |
| Fail-safe (lane hang, audio host death) | YES | G1 fail-safe leg 0 card frames; engine watchdog tests |
| Hold behaviour (slate, silence, retro) | YES behaviour; G1 alignment checks FAIL (n/a) | |
| Video recall per category | OFFLINE, synthetic, CPU, clean+light | G2 FAIL on allowlisted faces and ACCURATE lane p95; heavy level far below target |
| Video red team | PARTLY | quick subset only; open leaks in 5.2 |
| Audio PII mute and mouth blur | ORACLE ONLY | G3 passes on an oracle and English TTS; red-team leaks open |
| Sarvam, Groq, YouTube, Twitch calls (STT, LLM, translate, chat, quota, OAuth, stream egress) | NOT VERIFIED | no keys |
| Chat classifier | PARTLY | blind set abuse 82.1 %, fake UPI 80.8 % below target; Groq stage unmeasured; red-team split-word and leetspeak failing |
| Moderator console and web app | UNIT/JSDOM ONLY | no browser, Playwright, axe or mobile viewport (T6.5 pending) |
| Strike guard | SYNTHETIC ONLY | G5 passes on synthetic data; music-ON p95 4.5 s vs 2 s spec |
| Captions | BUILT, NOT LIVE | Sarvam never called; IndicTrans2 has no weights; re-review pending |
| Safety report | YES locally | e2e smoke, privacy scan clean; page-to-API shape reconciliation was still assigned |
| Phone ingest, pairing QR | LOOPBACK ONLY | no phone, no 4G, networking admin steps pending |
| Windows hotkey | NOT BUILT | `scripts/hold_hotkey_win.py` missing |
| RTX 5050 and sm_120 | NOT VERIFIED | |
| Cost per hour | ESTIMATE | `docs/COST.md` |
| Gate 6 and the final gate | DO NOT EXIST | `scripts/gate_phase6.sh`, `scripts/gate_final.sh` |

## 9. Rehearsal log (fill in)

| Date | Machine | Profile | Scenes that worked | What failed |
|---|---|---|---|---|
| | | | | |

The plan requires three full runs on the RTX 5050 before the demo.
