# Spec: eval (data, harness, gates, red team)

## 1. Purpose and owner
Everything that proves the platform works: synthetic SPECIMEN data, a synthetic stream sender with an optical stamp, the accuracy and latency harness, mock platforms, red-team suites and the runnable gate scripts.
Owner: **eval-engineer** (several instances across waves, each on its own subdirectory). Owns `eval/` and `scripts/gate_*`.

## 2. Directories owned and file layout
```
eval/
  run.py                       # CLI: `python -m eval.run <selftest|measure|report|…>`
  harness/
    timing.py                  # read/write timing.jsonl, StageTiming helpers
    metrics.py                 # recall/precision, mute coverage, over-mute, delay, flicker, Wilson CI
    report.py                  # markdown + JSON report writer (eval/reports/)
    procs.py                   # start/stop engine, MediaMTX, API, sender; psutil CPU sampling
    gpu_lock.py                # fcntl.flock on /tmp/streamsarthi-gpu.lock (same file as scripts/gpu_run.sh)
    fault.py                   # fault injection helpers (hang lane, kill encoder, API down)
  sender/                      # T0.2; adapt reference/irl-shield/relay/synthetic.py and fixture.py
    stamp.py  beep.py  scenes.py  sender.py        # SRT/RTMP sender (PyAV), scene scripts
    reader.py                  # reads the `shielded` tap, decodes stamps and beeps
  data_gen/
    video/                     # T2.1: cards.py qr.py plates.py signboards.py screens.py games.py faces.py augment.py
    audio/                     # T3.1: tts_compose.py numbers.py noise.py truth.py
    chat/                      # T4.1: abuse.py scams.py doxxing.py clean.py
    guard/                     # T5.5: music.py profanity.py
    common/                    # specimen.py (SPECIMEN rules), verhoeff.py, seeds.py, fonts.py
  mock_platforms/              # T4.1: youtube_mock.py twitch_mock.py rtmps_sink.py
  redteam/
    video/                     # T7.1: angle.py glare.py occlusion.py motion.py distance.py compression.py
    audio_chat/                # T7.2: fast_numbers.py accents.py noise.py codemix.py misspell.py homoglyph.py
  e2e/                         # T6.5: Playwright specs, FakeEngine, walkthrough.py
  baselines/                   # committed JSON baselines per phase (numbers only)
  reports/                     # committed markdown reports <date>-<phase>.md
  data/generated/              # gitignored (seeded, regenerable)
  data/private/                # gitignored (consented teammate faces)
  runs/<run_id>/               # gitignored: timing.jsonl, output taps, logs
  tests/
scripts/gate_phase0.sh … gate_phase6.sh  gate_final.sh  gate_lib.sh
```

## 3. Inputs
`contracts/` types and fakes, `Config` profiles, the running engine/API/chat processes (via bus and the MediaMTX `shielded` loopback tap), `.env` keys for Sarvam TTS and live legs (optional; offline fallbacks exist).

## 4. Outputs
- Datasets under `eval/data/generated/<name>/` with `manifest.json` (`seed`, `generator_version`, items with ground truth) so any dataset regenerates bit-for-bit from `(generator_version, seed)`.
- Reports `eval/reports/<date>-<phase>.md` plus `.json`, baselines in `eval/baselines/<phase>.json`.
- Gate scripts with the exit codes in section 5.9.

## 5. Behaviour

### 5.1 SPECIMEN rules (`data_gen/common/specimen.py`, enforced by test)
1. Every synthetic ID, card, plate, phone screen and QR scene carries a visible **SPECIMEN** mark: a diagonal watermark of at least 8 % of the image width on cards, and a "SPECIMEN" label near plates and screens. The watermark must **not** cover the digits the detector needs to read (it is placed on the card background).
2. **Aadhaar:** 12 digits, Verhoeff-valid, leading digit **0 or 1** (real Aadhaar numbers never start with 0 or 1, so a generated number cannot be a real one). Formatted `XXXX XXXX XXXX`. Name `SPECIMEN PERSON`, a synthetic silhouette in place of a photo, QR payload `SPECIMEN`. The `cards` detector must therefore not reject leading 0/1 (noted for the vision spec).
3. **PAN:** `[A-Z]{5}[0-9]{4}[A-Z]` with the 4th character `X` (not a valid holder-type letter, so it cannot be a real PAN), e.g. `ABCXE1234F`.
4. **UPI:** IDs of the form `zz-specimen-<8 hex>@<handle>` using real handles for detector coverage plus a reserved fake handle `@specimen`. QR payloads: `upi://pay?pa=zz-specimen-1a2b3c4d@oksbi&pn=SPECIMEN%20TEST&am=1&cu=INR` (positive), `https://example.com/` and `WIFI:T:WPA;S:specimen;P:specimen;;` (negative).
5. **Plates:** valid Indian formats (`KA01AB1234`, `MH12DE4567`, `22BH1234AA`) rendered on synthetic vehicle silhouettes or CC0 images, with a "SPECIMEN" sticker outside the plate. Collision with a real registration is possible in principle (UNVERIFIED); plates appear only on synthetic scenes and are never matched against any registry.
6. **Phone numbers:** drawn from a seeded pool of synthetic Indian mobile numbers, never tied to a person, never dialled or sent anywhere except into locally rendered images and TTS audio. Collision with a real subscriber is possible in principle (UNVERIFIED), so the pool lives only in `eval/` and generated data.
7. PIN codes: random valid-range 6-digit values (first digit 1–8). Emails/UPI in text: `…@example.invalid` style or the `zz-specimen-` prefix.
8. **Faces:** only (a) consenting teammates, stored in the gitignored `data/private/` and never uploaded, or (b) openly licensed CC0 images with the licence recorded in `data/generated/faces/ATTRIBUTION.md`. No scraped photos.
9. A test scans every generator output for forbidden patterns (a Verhoeff-valid number with leading digit 2–9, a PAN with a valid 4th letter) and fails the suite.

### 5.2 Video generators (`data_gen/video/`, T2.1)
Static images and 5–10 s clips (30 fps, 1280×720) per category with ground truth quads per frame (`truth.jsonl`): Aadhaar and PAN cards, UPI QR (on desk and on a phone screen), plates, signboards in Devanagari/Kannada/Tamil/Telugu/Bengali/Latin (Noto Sans family, OFL, rendered with **libraqm** so Indic shaping is correct), phone screens with OTP and notification banners (SMS style, fake text), BGMI and Free Fire lobby/custom-room overlays (player ID, room ID, password, rendered with our own synthetic UI, not game assets), faces (allowlisted vs strangers). `augment.py`: tilt ±30°, perspective, glare (specular blobs), Gaussian/motion blur, partial occlusion 0–40 %, distance (scale 0.15–1.0 of frame width), JPEG/x264 compression (crf 23–35), low light. Initial sizes: 200 clips per category, 40 % augmented heavily. A target is "visible" for scoring when ≥ 60 % of its quad is inside the frame and its shortest side ≥ 24 px.

### 5.3 Optical-stamp sender (`sender/`, T0.2)
- Renders each frame with a **stamp strip** in the top-left 256×16 px: 32 cells of 8×8 px, black/white. Payload = 24-bit frame counter (Gray-coded) + 8-bit CRC-8; a second strip (below, 256×16) carries a 32-bit capture-time in milliseconds since sender start. Cells are high contrast and 8 px wide so they survive x264 at crf ≤ 35 and the blur/pixelate of other masks. The compositor never masks this region (scenes place targets away from it; a test asserts this).
- Beep: a 1 kHz tone burst, 50 ms, every 1.000 s at a frame boundary, `-12 dBFS`; the stamp for that frame sets a "beep" flag bit.
- Scenes (`scenes.py`): `card_static` (40 s), `card_moving` (40 s), `card_absent` (40 s), `many_targets`, `idle`. Pure Python (numpy + Pillow + PyAV), no GPU. Sends SRT or RTMP to the MediaMTX ingest path, with `--clock monotonic`.
- Reader (`reader.py`): pulls the `shielded` loopback tap (RTSP/RTMP), decodes the stamp from each frame (sample cell centres, threshold at mid-grey, CRC check; frames with a bad CRC are counted as `stamp_unreadable`), records `t_read = time.monotonic()` when the frame was decoded, and detects beep onsets by short-time RMS (5 ms hop) crossing −30 dBFS.
- **Delay** per frame = `t_read − t_capture` where `t_capture` is the sender's stamp (sender and reader run on the same machine and clock). A calibration run (`eval.run calibrate`) sends through MediaMTX with the engine delay 0 and records the tap's own latency; it is subtracted and reported separately (expected ≈ 0.1–0.3 s, UNVERIFIED).
- **A/V offset** = `t_beep_audio_onset − t_beep_video_flag_frame` per beep; gate tolerance ≤ 40 ms.

### 5.4 Timing JSONL schema (`runs/<run_id>/timing.jsonl`)
First line: `{"type":"header","run_id":…,"profile":…,"git_rev":…,"t0_wall":iso,"hardware":{"cpu":…,"gpu":…,"driver":…,"cuda":…},"seed":…}`. Then one line per `StageTiming`: `{"type":"timing","stage":str,"seq":int,"pts":float|null,"t_start":float,"t_end":float,"proc":str}` (monotonic seconds). Stage names: `ingest_decode, lane_fast, lane_accurate, motion, plan, compose, encode, egress, vad, stt, pii, mute_apply, chat_classify, caption_translate, music, api_hold`. Other line types: `{"type":"event","name":…,"t":…}` (hold, fault, slate). Never any image, audio or text.

### 5.5 Metric definitions (`harness/metrics.py`)
- **Visible target** and **leak** (output-based, used for cards, QR, text, plates): a visible target frame leaks if the target is *recoverable from the output frame*: QR decodes, or OCR (Latin + the matching script recogniser) reads a Verhoeff-valid Aadhaar / valid PAN / phone / plate / UPI ID from the target's quad region of the composited output, or SSIM between output and unmasked reference in the quad > 0.6.
- **Frame-level recall** = 1 − leaked frames / visible target frames, over frames after a 5 s engine warm-up. Reported per category with a 95 % Wilson interval.
- **Faces:** recall = non-allowlisted face quads with ≥ 90 % area covered by a mask in the output; allowlisted-visible rate = allowlisted face frames with < 10 % masked area.
- **Precision (frame-level)** = masked quads whose IoU ≥ 0.5 with a ground-truth target / all masked quads (excluding hold/PROTECT frames).
- **Flicker** = fraction of (target, second) windows where a continuously visible static target's mask turns off and on again.
- **Mute coverage** = Σ duration of ground-truth PII words (or digit sequences) whose **output** audio RMS ≤ −50 dBFS for ≥ 90 % of the word interval / Σ duration of all ground-truth PII words. **Over-mute** = seconds of non-PII speech with output RMS ≤ −50 dBFS / total non-PII speech seconds.
- **Delay** = per-frame values from 5.3, p50/p95/max. **fps** = decoded frames at the tap / elapsed seconds over ≥ 120 s. **CPU** = psutil process-tree CPU seconds / wall seconds, sampled at 1 Hz. **VRAM** = `nvidia-smi --query-gpu=memory.used` sampled at 1 Hz, run under the GPU lock.
- **Chat:** per-category precision/recall vs labelled set, latency percentiles from `chat_classify` timings.
- Report math uses fixed seeds; rerunning a report on the same artefacts must give identical numbers.

### 5.6 Report format (`eval/reports/<date>-<phase>.md`)
Header: git rev, profile, hardware, seed, dataset names and versions, commands run. Then per-category table (n, recall, precision, 95 % CI, target, PASS/FAIL), latency table (p50/p95/max per stage), perf table (fps, CPU cores, VRAM peak), list of failing case ids (ids only; failing images regenerate from the seed, never embedded), regression vs `baselines/<phase>.json`. A JSON twin holds the same numbers; gates read the JSON.

### 5.7 Mock platforms (`mock_platforms/`, T4.1)
- `youtube_mock.py` (aiohttp): `liveBroadcasts.list`, `liveChatMessages.list` (honours `pageToken`, returns `pollingIntervalMillis`, injectable 403/503/quotaExceeded), `liveChatMessages.delete`, `liveChatBans.insert`; counts quota units per call (list 5, delete 20, ban 200, broadcast 1) and exposes `GET /_quota`.
- `twitch_mock.py`: device-code OAuth, EventSub WebSocket (welcome, keepalive, notification, `session_reconnect`, forced drop), Helix delete and bans with scope checks. Pattern from `reference/irl-shield/tests/integration/mock_twitch.py` and `tests/m3/`.
- `rtmps_sink.py`: a TLS RTMP sink with a self-signed CA written to `runs/<id>/ca.pem` (egress accepts it through `tls_ca_file`), counts bytes, can reject the key or drop the connection.

### 5.8 Red-team suites (T7.1, T7.2)
Layout in section 2. Each module exports `cases(seed) -> Iterable[Case]` (id, category, params, truth) and a `run` that pushes cases through the real pipeline. Video: angle (0–70°), glare, occlusion (0–60 %), motion (pan/shake speeds), distance, compression, partial card at frame edge, card on phone screen, mirrored card. Audio: fast digit runs (≥ 6 digits/s), regional accents/voices (Sarvam bulbul voices, espeak-ng), SNR 20/10/5 dB with music, code-mixed Hindi/English numbers, "double/triple", spelled UPI. Chat: misspellings, homoglyphs, zero-width, leet, mixed scripts, spaced letters. Output: miss rate per category with Wilson CI into `eval/reports/<date>-redteam-{video,audio_chat}.md`.

### 5.9 Gate scripts (`scripts/gate_phase0..6.sh`, `gate_final.sh`, shared `gate_lib.sh`)
All are bash (`set -euo pipefail`), run from the repo root inside WSL, wrap every GPU step in `flock /tmp/streamsarthi-gpu.lock`, write `eval/reports/<date>-<phase>.md`, print a summary table, and exit with: **0** pass, **1** a criterion failed, **2** environment/setup error (missing tool, key, dataset; nothing was measured), **3** a required hardware leg was skipped (e.g. `--require-gpu` with no GPU). Numeric targets are the plan's initial targets; changing one needs a `docs/DECISIONS.md` entry.

**gate_phase0.sh**
1. `python scripts/doctor.py` (checks in `ops.md`) → exit 2 on failure.
2. `pytest -q contracts` ; `ruff check .` ; `ruff format --check .`.
3. `python -m eval.run selftest` (generators deterministic, stamp encode/decode round trip, metrics on toy data, report writer).
4. Loopback smoke: start MediaMTX via `relay` supervisor, sender → SRT, reader → null sink for 60 s. Pass: frames received ≥ 98 % of sent, 0 gaps > 100 ms.
5. `docs/spikes/stt-spike.md` exists and contains: Sarvam timestamps yes/no, STT p50/p95 per provider, Groq limits observed (T0.6 output). Missing → exit 1.

**gate_phase1.sh [--live-youtube] [--require-gpu]**
1. Start engine with `--profile dev`, scene scripts `card_absent 10 s → card_static 40 s → card_moving 40 s → card_absent 30 s` (120 s total), once with `gpu.enabled=false` and, if a GPU exists, once with it on.
2. Pass (each leg): delay p95 ≤ 5.0 s and max ≤ 5.5 s; output fps ≥ 29; SPECIMEN card leak-free (no Verhoeff-valid OCR read, recall ≥ 98 % of card-visible frames after the 5 s warm-up); A/V offset ≤ 40 ms; stamp_unreadable ≤ 1 %.
3. **Fail-safe test:** engine started with `SS_FAULT=lane:accurate:hang@t=20s`. Within 1.0 s after the first missed deadline every output frame is PROTECT (slate = uniform frame, std-dev < 4, no card pixels) and **0 frames** show the card.
4. **Hold test:** `POST /api/hold` at `t=60 s` while the card is visible; slate starts within 1 frame of the command's engine-receipt time; frames captured in `[t−3.0 s, t]` that had not yet left the buffer are slates (retro window); audio RMS ≤ −50 dBFS during the hold; release via API returns to LIVE and requires newer frames (fence).
5. `--live-youtube` (needs `YOUTUBE_STREAM_KEY`, a prepared unlisted broadcast): 3 min run, asserts egress connected, no reconnect, bytes sent > 0. Viewer-side latency (informational, never gating) is recorded from `--viewer-latency-s <value>` if the user supplies it; otherwise written as `null`.
6. Writes `eval/baselines/phase1.json` on the first passing run (delay p50/p95, fps, CPU).

**gate_phase2.sh**
1. Generate datasets if missing (seeded).
2. Offline accuracy leg (`--offline`, faster than real time, CPU, no stream): per-category recall/precision on the eval sets. Pass (initial targets): faces 95 % (non-allowlisted blurred) with allowlisted visible ≥ 95 %; Aadhaar/PAN 95 %; UPI QR 98 %; signboard text 85 %; phone/PIN/UPI ID/email 90 %; OTP screens 85 %; game IDs 90 %; plates 85 %; flicker ≤ 2 %.
3. Real-time perf leg with **all** detectors on (`many_targets` scene, 120 s): fps ≥ 29, delay p95 ≤ 5.0 s, CPU ≤ 5 cores, VRAM ≤ profile cap (3.5 GB dev).
4. Regression vs `baselines/phase1.json`: delay p95 not worse by > 0.2 s.

**gate_phase3.sh**
1. Generate audio set (150 clips, ≈ 500 PII spans, truth timestamps by construction).
2. Real-time-paced run through audio + engine mute-apply. Pass: mute coverage ≥ 90 % of PII words (≥ 95 % of digit sequences); over-mute ≤ 15 %; audio pipeline p95 ≤ 3.3 s; mouth blur present on ≥ 95 % of muted-span frames where an allowlisted face is visible.
3. **API-down leg** (`SS_FORCE_API_DOWN=1`): voiced speech with no verdict is 100 % muted (fail-safe) and the local path covers ≥ 70 % of PII words.
4. Captions disabled in this gate.

**gate_phase4.sh**: starts mock platforms; runs the 2,000-message chat set through `chat.main`. Pass: abuse recall ≥ 85 % / precision ≥ 90 %, scam link recall ≥ 95 %, fake UPI ≥ 98 %, doxxing ≥ 85 %, clean false-positive ≤ 3 %, classify p95 ≤ 300 ms; YouTube mock: quota arithmetic (planned 3 h ⇒ interval 6.75 s ± 0.1), delete and timeout round trips; Twitch mock: connect/drop/reconnect with no duplicates and no HOLD on drop; `!hold` accepted only for broadcaster/moderator; log scan finds no flagged text.

**gate_phase5.sh**: music vs speech clips recall ≥ 90 % / false positive ≤ 5 %; fingerprint match ≥ 90 % on 5 s noisy snippets of the demo library; Hinglish profanity recall ≥ 85 %; risk meter level changes ≤ 2 s after an injected signal; captions render in 5 scripts (raqm shaping test compares glyph runs to references) and each caption event arrives before its display time.

**gate_phase6.sh**: `npm ci && npm run build && npm run lint && npx tsc --noEmit` in `app/web`; `pytest -q app/api`; Playwright suite (`eval/e2e`); safety-report schema test; `eval/e2e/walkthrough.py` (fresh-clone-to-live timing, informational ≤ 10 min).

**gate_final.sh**: runs gates 0–6 in `--quick` mode, then the red-team suites (publishes miss rates, no pass/fail except a regression vs the previous report > 5 points), a 30 min soak (0 unmasked frames, RSS growth ≤ 10 %, no thread leak), a netem 4G profile (`tc qdisc add dev lo root netem delay 80ms 30ms loss 2%` on the ingest path; needs `sudo`, exit 2 if unavailable): no hang, slate during drops, recovery ≤ 10 s; computes cost per streaming hour from API counters (Sarvam ₹29.4/h STT assumption, Groq, translate); writes `eval/reports/<date>-final.md` and updates `docs/STATUS.md` numbers via the lead.

## 6. Reuse
| Source | Target | Change |
|---|---|---|
| `reference/irl-shield/relay/synthetic.py`, `fixture.py` | `eval/sender/` | Optical-stamp and beep latency measurement; adapt to our stamp format |
| `reference/irl-shield/bench/metrics.py`, `scoring.py`, `independence.py`, `provenance.py` | `eval/harness/metrics.py` | Interval-leak scoring idea; replace Latin-only judges |
| `reference/irl-shield/tests/integration/mock_twitch.py`, `tests/m3/*` | `eval/mock_platforms/twitch_mock.py` | Extend with EventSub reconnect and Helix scopes |
| `reference/privastream/benchmarks/ml_latency_harness.py` | `eval/microbench.py` | p50/p95/p99 JSON timer as is |
| `reference/privastream/src/models/audio/check_gpu.py` | `scripts/doctor.py` (lead) | Idea only |
| `reference/irl-shield/tools/demo_renderer`, `fixtures/fonts` | not copied | Our own SPECIMEN renderer |

## 7. Edge cases
Stamp strip hit by a mask (generator bug, asserted in tests); x264 corrupting cells at low bitrate (use crf ≤ 35, report `stamp_unreadable`); sender and engine clock drift (same host monotonic clock, documented); reader joining mid-stream; GPU busy with another job (lock wait, report wait time); API keys missing (generators fall back to espeak-ng for TTS; live legs exit 2); WSL without `tc` privileges; non-deterministic OCR between runs (fix thread count and seed); datasets too large for disk (cap 5 GB, cleanup command).

## 8. Config keys used
`profile`, `delay_seconds`, `detect.*`, `gpu.*`, `categories.*`, `audio.*`, `chat.*`, `eval.{seed=1234, warmup_s=5, tap_url, report_dir}` (eval-only keys under `Config.eval`, ignored by the engine), fault hooks `SS_FAULT`, `SS_FORCE_API_DOWN`.

## 9. Test cases (CPU, `eval/tests/`)
1. Stamp encode/decode round trip for 10,000 random counters, with x264 crf 23/30/35 round trip: ≥ 99.9 % decode.
2. Verhoeff generator: 1,000 numbers all valid and all with leading digit 0/1; PAN generator 4th char is `X`.
3. SPECIMEN lint: every generated image has the watermark pixels in the expected region; forbidden-pattern scan passes.
4. Dataset determinism: same seed → identical manifest hash; different seed → different.
5. Metrics: toy truth/prediction sets give the exact expected recall, precision, Wilson CI, mute coverage and over-mute; delay on a synthetic timing file equals the injected delay.
6. Report writer produces both files and a stable JSON.
7. Mock YouTube quota accounting: 10 lists + 2 deletes + 1 ban = 50 + 40 + 200 units.
8. Mock Twitch scope enforcement: delete without the scope → 401/403.
9. Gate scripts: `gate_lib.sh` exit-code mapping unit-tested with fake steps (0/1/2/3).
10. Red-team case generators are reproducible and cover every listed parameter range.
**Acceptance:** T0.2 (`selftest`, sender works), T1.4 (`gate_phase1.sh` passes on a known-good engine and fails on a deliberately broken one: engine with fail-safe disabled → exit 1), T2.10, T3.1, T4.1, T5.5, T6.5, T7.x each demonstrate their gate fails on a seeded defect and passes on the fixed build.

## 10. Latency target
Harness overhead: stamp decoding ≤ 5 ms/frame at the tap; metrics for a 120 s run ≤ 60 s; offline accuracy leg (Gate 2, ~1,000 clips) ≤ 30 min on CPU.

## 11. VRAM / CPU target
No GPU use of its own except to measure; sender ≤ 1 core; reader ≤ 0.5 core. Disk for generated data ≤ 5 GB.

## 12. Tasks
**T0.2** harness skeleton + sender + `gate_phase0.sh` (deps T0.1) → **T1.4** `gate_phase1.sh` → **T2.1** video data (deps G1) → **T2.10** `gate_phase2.sh` → **T3.1** audio data + `gate_phase3.sh` → **T4.1** chat data + mocks + `gate_phase4.sh` → **T5.5** guard data + `gate_phase5.sh` → **T6.5** e2e + `gate_phase6.sh` → **T7.1/T7.2** red team → **T7.6** `gate_final.sh`.

## 13. Out of scope
Real ID cards, real people's data, real streamer recordings in git, model training, load testing beyond one stream, cloud CI (local only), automated viewer-side YouTube latency measurement (manual, informational).
