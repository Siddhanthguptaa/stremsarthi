# Stream Sarthi: go-live runbook (from this repo to a REAL stream)

Written 2026-11-21 (updated the same day for the relay, chat and report changes) from the repo as it stands (uncommitted tree), `docs/STATUS.md`, `docs/DEMO.md`, `docs/COST.md`, `docs/PLAN.md` sections 2 and 9, the scripts and the code. Every command, flag, path, route and variable below was checked by reading the repo; anything that could not be checked is marked **UNVERIFIED**. Nothing in this file was run.

Conventions: `SS` means the WSL wrapper, always typed in full from **Git Bash** (in PowerShell drop the leading `MSYS_NO_PATHCONV=1 `, UNVERIFIED). Never put `$VARS` or Windows paths inside the WSL command. Never paste a key, token or login URL into a chat, ticket or screenshot: edit `.env` yourself.

```
MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh <command>
```

---

## Fastest path to a first real test (no keys, 10 minutes)

Nothing leaves this PC and no account or key is needed. Everything is typed from Git Bash (`SS` below is the long wrapper from the top of this file).

1. **Check the machine (about 10 s):** `MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh python scripts/doctor.py --real`. Fix anything marked `FIX` for a REQUIRED item (busy ports, missing weights).
2. **Start the local rehearsal (2 to 3 min):**
   ```
   MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash -c "SS_MEM_MAX=7000M bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh bash scripts/real.sh --gpu"
   ```
   Open the `http://127.0.0.1:3000/#token=...` link it prints in the Windows browser. It also prints the RTMP and SRT address to publish to (these contain the session login: keep them private).
3. **OBS on this PC:** Settings, Stream, Service Custom, Server = the printed RTMP URL, Stream Key empty (details and the SRT fallback in section 3.2). Video 1280x720, 30 fps. Start Streaming.
4. **Open `/demo`** (top menu "Demo", ONE tab only). The right pane, "what viewers get", should leave "waiting" within a few seconds. Expect up to 1 to 3 minutes of slate flapping while the GPU warms up: that is the fail-safe, not a bug.
5. **Enrol your face** (top menu "Faces", `/enrol`, about 3 s) so you stay visible; anyone not enrolled is masked.
6. **Test with SPECIMEN or made-up data only** (section 3.6). Press H to hold. Ctrl-C the launcher when done.

What is still UNVERIFIED on this path: OBS has never been run against this stack, and the enrolment-to-engine allowlist wiring has unit tests only (section 0).

## Going live: checklist

Do these in order; each one is described in the section named. Stop at the first thing that does not pass.

- [ ] Level 1 rehearsal passed with your own camera and microphone, the test script in section 3.6 done, one `/demo` tab, no other heavy apps (sections 3 and 5).
- [ ] Doctor clean for REQUIRED items: `scripts/doctor.py --real` (section 2.1). Plugged in, performance mode.
- [ ] YouTube live enabled on the channel at least 24 h ago; a broadcast created as **Unlisted or Private**; `YOUTUBE_STREAM_KEY` and/or `TWITCH_STREAM_KEY` in `.env` (sections 1.1, 4.1).
- [ ] `GROQ_API_KEY` and `SARVAM_API_KEY` in `.env` if you want abuse/doxxing detection and captions (names are checked by the doctor, values never printed).
- [ ] Chat shield (optional): one-time standalone login done for YouTube and/or Twitch (section 4.2); a second account ready to post test chat.
- [ ] Phone only: `--phone` with `INGEST_SRT_PASSPHRASE` set, mirrored networking and firewall rules in place, SRT chosen in the app (section 3.3).
- [ ] Start with `--gpu --chat` (or add `--live`), switch to the live session from `/go-live`, delay **4 s**, profile `dev` (section 4.3). Copy the NEW publish login into OBS or the phone.
- [ ] Watch your own stream in a private window, signed out, and repeat the section 3.6 test there before telling anyone (section 4.4).
- [ ] Hold is reachable: button or key H on `/live`, `!hold` in chat, tab not hidden behind other windows (section 4.5).
- [ ] After the stream: Stop on `/go-live` FIRST, then Ctrl-C the launcher. The report is saved to the sessions dir when a session stops (**UNVERIFIED** until the product agent reports; see section 7 for where and how to export).

---

## 0. What you get, and what is verified

**What you get:** a privacy layer between OBS or a phone app and YouTube/Twitch. Video, audio and chat are checked before viewers see them, with a 4 s broadcast delay and a fail-safe (anything unchecked goes out as a privacy slate or muted). Pages: `/go-live`, `/live`, `/demo`, `/enrol` (shown as "Faces"), `/controls`, `/mod`, `/report/<session id>`.

| Verified | Not verified |
|---|---|
| Local chain with no keys: API spawns engine, relay, delay, hold, slate, report (`scripts/e2e_smoke.sh` 36/36 on an idle machine; G1 delay p95 about 4.4 s, 30 fps, 0 card leaks on SYNTHETIC SPECIMEN video, CPU and RTX 3050) | Any live YouTube or Twitch egress, chat read, chat delete, OAuth (all keys have always been empty) |
| A synthetic demo reel through `scripts/demo.sh --raw --gpu` in a real browser WITH the GPU: Aadhaar, UPI QR, signboard masked, risk meter moves | Real Sarvam or Groq calls, real Hinglish or regional speech, real spoken-PII muting |
| Local fallbacks exist and are unit-tested (local rules, local faster-whisper) | A real OBS or phone publishing in; mirrored networking; 4G |
| | Anything on the RTX 5050 laptop; a 30-minute soak |
| | Face enrolment through the web page end to end (the allowlist wiring fix is new: enrolled face may stay blurred) |
| | Settings changed in the web app now reach the engine (new `config_sync`): not verified live |

Accuracy numbers are synthetic and optimistic. Do not stream anything where a leak would hurt until you have rehearsed it yourself (Level 1) and watched the output (Level 2, private stream). See section 8.

---

## 1. Prerequisites (who, what, how long)

### 1.1 Accounts and keys

All values go into `C:\Users\siddh\stremsarthi\.env` (copy `.env.example` to `.env` first if it does not exist). Edit the file yourself; the doctor checks only that a name is non-empty and never prints values. You can skip any row: the last column is what still works.

| What | Time | `.env` variable(s) | Without it |
|---|---|---|---|
| **Groq** account, API key | 5 min | `GROQ_API_KEY` | Local faster-whisper `base` on CPU for speech; rule-based chat and spoken-PII classifiers. Abuse and doxxing detection are weaker (they need the Groq stage) |
| **Sarvam** account, API key (INR 100 signup credit, per `docs/PLAN.md`) | 5 min | `SARVAM_API_KEY` | Groq or local speech-to-text (Hinglish only); **no caption translation** |
| **YouTube live enabled** on the channel | **up to 24 h**, start first | (none) | No YouTube output (rehearsal only) |
| YouTube **stream key** (YouTube Studio, Go live, Stream) | 2 min after enablement | `YOUTUBE_STREAM_KEY` (or type it on `/go-live`: kept in RAM only) | No YouTube output |
| YouTube chat: Google Cloud project, YouTube Data API v3 on, OAuth consent screen with YOU as a **test user**, OAuth client of type **Desktop app** | 20-30 min | `YOUTUBE_OAUTH_CLIENT_ID`, `YOUTUBE_OAUTH_CLIENT_SECRET` | Chat shield off for YouTube. A consent screen in "Testing" gives refresh tokens that expire after 7 days (per `docs/DEMO.md`): log in again then (section 4.2) |
| **Twitch** developer app (device-code flow, public client) | 10 min | `TWITCH_CLIENT_ID` (a **Public** client; `TWITCH_CLIENT_SECRET` is in `.env.example` and the doctor, but the chat code reads only the client id) | Chat shield off for Twitch |
| Twitch **stream key** | 2 min | `TWITCH_STREAM_KEY` (or type it on `/go-live`) | No Twitch output |
| **Hugging Face** token (optional) | 5 min | `HF_TOKEN` | Only needed for local IndicTrans2 captions in the `demo` profile. Not needed in `dev` |
| Stable pairing login (optional) | 1 min | `INGEST_PUBLISH_USER`, `INGEST_PUBLISH_PASSWORD` (`[A-Za-z0-9_-]`, password 8+ characters) | Random login per session. New in the uncommitted tree: `fixed_login()` in `app/api/routes/session.py` makes the API use these values instead of a random login, so the OBS or phone setup can be one-time. **UNVERIFIED** (read in code only; a bad value silently falls back to the random login, and a phone-reachable bind needs a password of 12+ characters). Without it, expect to re-copy the URL after every start |

What runs with an EMPTY `.env`: everything in Level 1 (local video protection, local audio fail-safe and local speech-to-text, no captions, chat disabled, output only to the local `shielded` tap). A second YouTube or Twitch account (or device) is needed to post test chat.

### 1.2 Windows admin steps (only needed for a PHONE; OBS on the same PC works without them)

Do these when no stream is running. **`wsl --shutdown` kills everything inside WSL, including a live stream and any unsaved report.**

1. Edit `C:\Users\siddh\.wslconfig`. It currently has `memory=6GB`, `swap=8GB`, `vmIdleTimeout=-1` under `[wsl2]`. ADD the line below under `[wsl2]` (do not replace the file). Optionally raise `memory=` to `8GB` if the PC has 16 GB or more (the doctor warns the engine can need up to 5.5 GB):
   ```
   networkingMode=mirrored
   ```
2. In a normal PowerShell: `wsl --shutdown`, then start WSL again by running any wrapper command.
3. In an **admin** PowerShell, create the Hyper-V firewall rules (from `docs/specs/ops.md` section 5.6; the spec itself says "verify at setup": UNVERIFIED on this machine):
   ```powershell
   New-NetFirewallHyperVRule -Name "StreamSarthi-SRT"  -DisplayName "Stream Sarthi SRT"  -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol UDP -LocalPorts 8890
   New-NetFirewallHyperVRule -Name "StreamSarthi-RTMP" -DisplayName "Stream Sarthi RTMP" -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 1935
   ```
4. Find the PC's LAN address (PowerShell `ipconfig`, IPv4 of the Wi-Fi adapter, for example `192.168.x.x`). The phone must be on the same Wi-Fi (guest networks with client isolation will fail). For a phone on 4G the plan is Tailscale on both (`docs/PLAN.md` section 9; the code accepts 100.64.0.0/10 addresses): UNVERIFIED.
5. Check it took effect: the doctor in section 2 reports "WSL networking: mirrored".

### 1.3 One-time machine facts to know

- WSL has about 6 GB RAM (`.wslconfig`). Close browsers and other heavy apps when streaming: the engine alone uses about 4 cores and 3.5 to 5.5 GB.
- Plug in, performance power mode, nothing else heavy open. The delay maximum was within 0.2 s of its limit even on a quiet machine.

---

## 2. First-run checks (every time)

**2.1 Readiness checklist** (read-only; about 10 s; exit 0 = every REQUIRED item passes):

```
MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh python scripts/doctor.py --real
```

Items print as `PASS`, `FIX` (with the fix command) or `OPTIONAL`. REQUIRED: ports 8000, 3000, 8890 (UDP), 1935, 9997 free; ffmpeg with libx264 and libsrt; dev-profile model weights present and hash-checked. Optional items report GPU and VRAM, ONNX Runtime CUDA provider, GPU lock, NVENC, which key NAMES are set, network reachability, WSL networking mode, memory and disk. `--json` gives the same as JSON. If a port is busy: `pkill -f engine.main; pkill -f mediamtx; pkill -f app.api.main; pkill -f next-server` inside WSL (the doctor prints it).

**2.2 Memory cap.** `ss.sh` runs every command in a cgroup that is killed at **3500M** by default (exit 137, message "command killed at the ... memory limit"). The real engine can need more, so run real mode with `SS_MEM_MAX=7000M` (the exact command in section 3 does). The VM itself has about 6 GB (`.wslconfig`): if the OOM killer still fires, raise `memory=` there (section 1.2) and `wsl --shutdown`. `SS_NO_MEMCAP=1` disables the cap entirely.

**2.3 GPU lock.** `scripts/real.sh --gpu` takes `/tmp/streamsarthi-gpu.lock` for the whole run (waits up to 600 s, then exits 2). Only one GPU job at a time: no gates, tests or demo at the same time. `nvidia-smi` inside WSL should show the RTX with no other job. Dev profile GPU budget is 3.5 GB; expect about 0.7 to 1.7 GB in use.

**2.4 Day before (once):** `scripts/doctor.py --profile dev --gpu` (older, wider check), and `bash scripts/e2e_smoke.sh --duration 45` (keyless, about 75 s; exit 0 all pass) per `docs/DEMO.md` section 2. Do not run them while a stream is running.

---

## 3. LEVEL 1: real input, local output (no keys needed)

Your real camera and microphone go through the whole protection chain. The output goes only to the local `shielded` tap and is shown on `/demo`. **Nothing leaves the machine.** Use this to rehearse.

### 3.1 Start

```
MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash -c "SS_MEM_MAX=7000M bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh bash scripts/real.sh --gpu"
```

Options (`scripts/real.sh`): `--gpu` (use it: the CPU path sat on the privacy slate for text-heavy scenes), `--no-build` (skip the web build), `--raw` (adds the unmasked RAW pane on `/demo`, loopback only: never show it on a stream), `--chat` (Level 2). **Ctrl-C stops everything.** Allow about 2 to 3 minutes (web build, then CUDA start-up about 50 s).

It prints, in order: `Open this link (local only, it carries your login): http://127.0.0.1:3000/#token=...` (open it in the Windows browser; the token changes at every start), then `start session: 200`, `engine ready: True`, and the publish addresses:

```
RTMP: rtmp://127.0.0.1:1935/camera?user=...&pass=...
SRT : srt://127.0.0.1:8890?streamid=publish:camera:<user>:<pass>&pkt_size=1316&mode=caller&latency=120000
Moblin import link: moblin://?...
```
(The SRT URL here comes from the API's own builder; `latency=120000` is in microseconds = 120 ms, the LAN preset.) These URLs contain the per-session publish login: treat them as secret and do not paste them anywhere. Logs: `/tmp/ss_api.log`, `/tmp/ss_web.log`, `/tmp/ss_webbuild.log` (inside WSL).

### 3.2 OBS on the same PC (works without any Windows admin step)

Use the printed `127.0.0.1` URL.

1. Settings, Video: Base and Output resolution **1280x720**, **30 fps**.
2. Settings, Output (Advanced): encoder **x264** (or NVENC H.264), rate control **CBR**, bitrate **4500 kbps**, **keyframe interval 2 s**, audio **AAC**, sample rate **48 kHz** (Audio settings).
3. Settings, Stream: Service **Custom**. Put the printed **RTMP** URL (the whole line, including `?user=...&pass=...`) in **Server** and leave **Stream Key** empty. OBS publishes RTMP to MediaMTX path `camera` (`relay/security.py`).
   - **UNVERIFIED:** OBS has never been run against this stack. If OBS rejects it or appends a `/`, use Settings, Output, Recording type **Custom Output (FFmpeg)**, output type **Output to URL**, URL = the printed **SRT** URL, container **mpegts**, video H.264, audio AAC, then start Recording (not Streaming). Format details are best-effort: the SRT path is the one the harness sender and the pairing code use.
4. Click Start Streaming (or Recording for the SRT fallback). The `/demo` protected pane should leave "waiting" within a few seconds.

The login is per session: after any Stop/Start of the session (or restarting `real.sh`), re-copy the new URL.

### 3.3 Phone (Moblin on iPhone, Larix Broadcaster on Android)

Needs mirrored networking and the firewall rules (section 1.2) **and** an ingest that listens on the LAN. By default the ingest binds `127.0.0.1`, which a phone cannot reach. Use `--phone` (it sets that up and checks the passphrase), or set the variables yourself:

| Variable | Needed | Meaning (verified in `relay/runtime.py`, `relay/security.py`, `relay/pairing.py`, `relay/netenv.py`) |
|---|---|---|
| `INGEST_BIND_HOST` | yes for a phone | `0.0.0.0` (every interface; accepted only because it is written down on purpose). A specific loopback, LAN private (10.x, 172.16-31.x, 192.168.x) or Tailscale (100.64/10) address also works |
| `INGEST_SRT_PASSPHRASE` | **mandatory on any non-loopback bind** | 10 to 79 characters of `A-Za-z0-9_-`. Your own long random string. The ingest **refuses to start** without it (and a bad format is refused too). A generated one is no longer used: the API must know the same value to build the phone URLs |
| `PAIRING_HOST` | optional | The PC's LAN IPv4 (a literal LAN private or Tailscale address). Needed under WSL **NAT** networking: with a `0.0.0.0` bind the address shown to the phone is auto-detected, and under NAT the auto-detect returns a WSL-internal 172.x address the phone cannot reach. Under mirrored networking auto-detect should give the Windows LAN address (UNVERIFIED on this machine) |

Put them in `.env` (edit it yourself) or in the process environment. `relay/netenv.py` reads `INGEST_BIND_HOST`, `INGEST_SRT_PASSPHRASE`, `INGEST_PUBLISH_USER`, `INGEST_PUBLISH_PASSWORD` and `PAIRING_HOST` from `.env` as well; **the process environment wins** over `.env`. The API reads the same passphrase (`app/api/pairing.py`), so both sides agree.

Start with `--phone` (exports `INGEST_BIND_HOST=0.0.0.0` unless you already set one; exits 2 with a message, without printing the value, when `INGEST_SRT_PASSPHRASE` is missing or not 10 to 79 characters of `A-Za-z0-9_-`; prints a `PAIRING_HOST` hint):

```
MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash -c "SS_MEM_MAX=7000M bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh bash scripts/real.sh --gpu --phone"
```

(`--phone` is for `scripts/real.sh`; passing the variables on the command line before `bash ...ss.sh` inside the quotes still works.) The relay logs which address phones should dial and where it came from (`bind`, `PAIRING_HOST` or `autodetect`) and says if it cannot find one (it then names `PAIRING_HOST`).

Then in the phone app (open the RTMP/SRT QR from `GET /api/pairing/srt.png` or the printed Moblin link; the `/go-live` page also has an ingest card):

- **Protocol: SRT.** SRT is encrypted with your passphrase. **RTMP from a phone sends the publish login in cleartext** over your Wi-Fi: prefer SRT, and use RTMP only on a network you trust. **Latency 400 ms on 4G** (the pairing URL defaults to `latency=400`; weak 4G: 500; Wi-Fi 200). Moblin: import the `moblin://` link. Larix: paste the SRT URL. 720p, 30 fps, about 4 Mbps, keyframe 2 s.
- **UNVERIFIED on a real phone:** whether Moblin and Larix honour the `latency` and `passphrase` URL query (`relay/pairing.py` says so). If the phone connects but is rejected, set latency and passphrase by hand in the app.
- The `/demo` preview works with a `0.0.0.0` bind: the API shows the preview from `127.0.0.1` (`app/api/main.py`), and the pairing URLs are built with the resolved LAN address (not `0.0.0.0`). Code read only, not run: **UNVERIFIED**. With a specific LAN-address bind the anonymous preview reader is allowed from that address too (`relay/security.py:mediamtx_users`).
- Stay off a VPN that blocks LAN traffic; keep the phone awake and plugged in. Treat the passphrase like a password; never paste it or a pairing URL anywhere.

### 3.4 Open `/demo` and enrol your face FIRST

1. Open the link from the terminal, then **Demo** in the top menu (`http://127.0.0.1:3000/demo`). Keep **ONE** browser tab on `/demo` (the preview cap is 4 clients per stream; more tabs starve the panes).
2. **Enrol your face before you test:** top menu **Faces** (`/enrol`): type a label, let it capture about 8 frames over about 3 s (RAM only, 12 h expiry, no photos stored). Anyone not enrolled is always masked.
   - **UNVERIFIED live:** the allowlist wiring fix (engine now applies enrolments) has unit tests only. If your face stays blurred after enrolling, that is the known risk, not a mistake of yours: see the troubleshooting table.
3. The left pane is "what the streamer sees" (a text explanation unless you started with `--raw`), the right pane "what viewers get". State chip: LIVE / HOLD / SLATE. Also on the page: captions strip (off without a Sarvam key), risk meter, privacy-proof counter, alert feed, hold bar.

### 3.5 What to expect

- **Delay about 4 s** (p95 4.4 to 4.9 s measured on synthetic runs).
- **The first 1 to 3 minutes the protected output may flap between live and the privacy slate** ("Checking privacy coverage") while the GPU warms up and under load. That is the fail-safe working, not a leak. Keep one tab, close other apps, wait.
- A brief blank flash about every 4 minutes (preview token refresh) is known.
- With no keys: speech is handled by local speech-to-text; voiced segments without a verdict are muted, so audio can be choppy but safe.

### 3.6 Test script (SPECIMEN or made-up only, never real private data)

Do these in front of the camera, one at a time, and look at the PROTECTED pane (and listen to the output):

1. Hold up a **printed SPECIMEN Aadhaar or PAN** (pictures in `eval/data/generated/specimen_v0_seed1234/`; regenerate with `... ss.sh python -m eval.run gen-specimen --out /tmp/specimen --n 5`) or show one full-screen in an OBS image source. Expect a solid box.
2. Show a **SPECIMEN UPI QR** (`upi_qr_000x.png`). Expect the QR blacked out.
3. Show a phone screen with a **fake OTP** you typed yourself. Expect the banner masked.
4. Say a **made-up** phone number starting with 9 ("nine nine nine nine nine one two three four five") and a made-up UPI id ("demo dot streamer zz at ybl"). Expect the output audio to go silent over them. Real-voice spoken-PII muting is UNVERIFIED: judge it by listening, do not trust it.
5. Walk a second person into frame who is NOT enrolled: they must be masked; you (enrolled) should end up visible after about 1 s.
6. Press **H** (or click Hold): slate immediately and silence; press again to release (it asks first).
7. Stop OBS, wait, restart it: expect the output to recover in about 3 to 8 s.

Then Ctrl-C the launcher.

---

## 4. LEVEL 2: live to YouTube or Twitch

Do Level 1 first and be happy with it. **Start with a private or unlisted test broadcast, not your channel's public stream.**

### 4.1 Keys

Put `YOUTUBE_STREAM_KEY` and/or `TWITCH_STREAM_KEY` in `.env` (or type them on `/go-live`, RAM only). Add `GROQ_API_KEY` and `SARVAM_API_KEY` if you have them. Re-run the section 2.1 doctor: the key rows should say `PASS` (names only).

YouTube side: in YouTube Studio create a broadcast set to **Unlisted or Private**, with the stream-key ingest; leave it waiting for data. The stream key ties to that broadcast.

### 4.2 Chat login (once; only if you want the chat shield)

The login is a **standalone command**: it needs no running session, no engine and no bus. It logs in to every platform in `CHAT__PLATFORMS` (default both) that has credentials, stores the tokens and exits (exit 0 done, 2 nothing to log in to, 5 a login failed). Run it once, ahead of stream day:

```
MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash -c "CHAT__ENABLED=true bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh python -m chat.main --login"
```

`.env` names it reads (edit the file yourself, never paste values):

| Variable | Used for |
|---|---|
| `YOUTUBE_OAUTH_CLIENT_ID`, `YOUTUBE_OAUTH_CLIENT_SECRET` | YouTube login. The Google Cloud OAuth client type must be **Desktop app** (section 1.1) |
| `TWITCH_CLIENT_ID` | Twitch login. The Twitch app must be a **Public** client (device code) |
| `GROQ_API_KEY` | Not needed for the login; the chat classifier uses it later (without it: rules only) |
| `CHAT__ENABLED` | `true` to turn chat on (the command above sets it for that run; `real.sh --chat` sets it for a session) |
| `CHAT__PLATFORMS` | Optional: which platforms (default both). A nested-env list; JSON form such as `["youtube"]` (**UNVERIFIED** format: pydantic-settings parses lists from JSON) |
| `CHAT__TWITCH__CHANNEL_LOGIN` | Twitch only: the channel to read when it is not the logged-in account |

**YouTube.** The command prints a consent URL (with a short explanation) on stderr. Open it in the Windows browser and approve. Google then redirects to `http://127.0.0.1:8765/callback`; WSL2 forwards that loopback port to Windows and the command finishes by itself. The port is `CHAT_YT_LOGIN_PORT` (default 8765; if it is busy a random port is used and the printed URL carries the real one). **Fallback:** if the browser says it cannot connect, copy the full address from the browser's address bar, paste it into the terminal where the command runs, and press Enter (works only when the terminal is interactive). While the OAuth consent screen is in **Testing**, the refresh token expires after **7 days**: run the command again then.

**Twitch.** The command prints "open https://www.twitch.tv/activate (the printed URI) and enter the code ...". Open it in the Windows browser, enter the code, approve; the command finishes by itself.

**Where the tokens go.** The OS keyring when a real one exists (Windows credential store, macOS Keychain, Secret Service). In headless WSL there is none, so they are stored in a JSON file `chat-tokens.json` under `$STREAMSARTHI_TOKEN_DIR`, else `$XDG_CONFIG_HOME/streamsarthi`, else `~/.config/streamsarthi` (inside WSL, as root: `/root/.config/streamsarthi`), mode 0600 in a 0700 directory, outside the repo. The command prints a one-time NOTICE with the path. Delete the file to log out. Anyone who can read your WSL home can use those tokens.

**At stream time.** The API starts `python -m chat.main --profile dev` itself when chat is on (`real.sh --chat`); it reads the stored tokens and never prompts. The YouTube live chat id is **discovered at runtime**: the broadcast must be live (the poller retries every 10 s until it is). If a platform has no stored login or credentials the chat process skips it with a fixed code (`yt_login_required`, `twitch_no_credentials`, ...) and tells the moderator console "chat unavailable"; with none at all it exits 2 and the stream is unaffected.

**UNVERIFIED:** every part of this against the real Google and Twitch services (the code has only run against fakes), including WSL2 port forwarding for the 8765 callback and the keyring-versus-file choice on this machine.

### 4.3 Start and go live

1. Start the stack with chat:
   ```
   MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash -c "SS_MEM_MAX=7000M bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh bash scripts/real.sh --gpu --chat"
   ```
   (add `--phone` as in section 3.3 if a phone will publish). `--chat` only sets `CHAT__ENABLED=true`; it needs the stored login from 4.2.
2. `real.sh` starts a **rehearsal session** (no destination) unless you pass `--live` (then it goes live to every platform that has a stream key). To go live from the web app instead, open the printed link, go to **Go live** (`/go-live`) and **Stop** that session first. New in the uncommitted tree: the start request accepts `replace_rehearsal` (`app/api/routes/session.py`; the web client has it too), which stops a running rehearsal and then starts the live session; **UNVERIFIED** in a browser.
3. On `/go-live`: switch on YouTube and/or Twitch (keys from `.env` are recognised, or type them), keep delay **4 s**, profile `dev`, check the setup panel, then **Start**. Only a stream key is checked for presence: errors say `missing_keys` with names only.
4. A new session means a **new publish login**: copy the ingest URL shown on the `/go-live` ingest card (or `GET /api/pairing`) into OBS or the phone again, then start publishing.
5. When the state shows `live`, start the broadcast in YouTube Studio (or Twitch) if it is not already receiving.
6. Does GPU stay on after the restart? The engine inherits `GPU__ENABLED=true` from the launcher environment (`scripts/real.sh`); not verified across a Stop/Start. Check `nvidia-smi` memory use goes up.

### 4.4 Verify viewers get the protected stream

1. Open the stream's **watch page in a private/incognito window, signed out** (and, if you can, on a phone off your Wi-Fi). YouTube adds its own latency on top of the 4 s (3 to 5 s, not measured).
2. Repeat the section 3.6 test script there. What viewers see is the truth, not the `/demo` pane. Mute your own speakers (feedback) and keep the private window on a second display only if that display is not captured.
3. Check the engine did not just fall back silently: `/live` and `/demo` chips should show LIVE (not stuck on SLATE), and `GET /api/session/health` reports engine and chat state.
4. Stop the test with **Stop** on `/go-live`.

### 4.5 Hold / panic

- **Web:** the Hold button on `/live` or `/demo`; key **H** on those pages (ignored while a dialog is open). Releasing asks first.
- **Chat:** `!hold` (broadcaster or moderator), `!unhold` (broadcaster only). Needs the chat process running.
- **API:** `POST /api/hold` with `{"active": true}` (streamer token).
- **Windows global hotkey: NOT BUILT** (`scripts/hold_hotkey_win.py` does not exist). Keep the browser tab reachable.
- A hold also masks the preceding 3 s that are still in the delay buffer, and silences audio.

### 4.6 Moderator console

On `/live` use the moderator invite card to create a link of the form `http://127.0.0.1:3000/mod#token=...` (it carries a token: send it only to your moderator; they must be able to reach your web port, so for a remote moderator this needs a LAN or Tailscale setup that is NOT covered here). `/mod` lists the held chat messages (approve, delete, timeout, dismiss; keys D, T, J, K). Staff messages are never deleted. YouTube timeouts are off by default (quota cost 200 units).

---

## 5. Operations and safety

- **One browser tab on `/demo`; close other apps.** Plug in; performance mode.
- **Never show the RAW pane (`--raw`) on a stream or screen share.** It is the unmasked video.
- The delay is fixed at session start (`delay_fixed_while_attached` 409 if you change it while an engine is attached). Edit it on `/go-live` before Start.
- **The login URL token changes at every API start.** After a restart use the new link; if the page says "Open the link from the terminal", clear site data for `127.0.0.1:3000`.
- **Reports:** kept in the API's RAM (6 h, at most 8) and, new in the uncommitted tree, saved to the sessions dir (`SESSIONS_DIR`, default `~/.streamsarthi/sessions/<session id>/report.json` inside WSL, mode 0600) when a session stops. **UNVERIFIED** until the product agent reports: still open or export the report before you Ctrl-C the launcher (section 7).
- Stream keys typed on `/go-live` live only in the API process memory; `.env` is the other place. Never commit `.env`.
- Quota and cost: about INR 24 per streaming hour typical (INR 10 to 63), INR 89 typical with Sarvam captions; all rates are assumptions, no live call has ever been made (`docs/COST.md`). YouTube chat supports about 3 to 4 streaming hours a day on one project key. Groq rate limits are documented inconsistently; do not depend on the Groq stage (it fails toward holding messages for the moderator).
- Do **not** run `wsl --shutdown`, a gate, a test run or another stack while streaming.
- Stop the stream with Stop on `/go-live` first (builds the report), then Ctrl-C the launcher.
- First time on the RTX 5050 demo laptop: run section 2 and Level 1 there first (CUDA JIT for sm_120 is slow on the first run; if CUDA fails run without `--gpu`, which stays on the slate for text-heavy scenes).

---

## 6. Troubleshooting

| Symptom | Likely cause and action |
|---|---|
| Protected output sits on the privacy slate, or flaps live/slate | The fail-safe. First 1 to 3 min after start while the GPU warms up is normal. Persisting: not started with `--gpu` (the CPU lane misses its deadline on text-heavy scenes), another tab or app loading the machine (load average above about 9), memory cap. Close apps, one `/demo` tab, restart with `--gpu`. Check `nvidia-smi` shows memory in use |
| State chip says SLATE while masked live video is on screen | Known mislabel on `/live` (fixed on `/demo`): trust the video |
| Panes show "Stream not available" | Preview cap (4 clients per stream; 503 `preview_clients_full`): close extra tabs. Not live yet. It retries with 1 to 15 s backoff. With a `0.0.0.0` bind the preview should work (section 3.3); with a specific LAN-address bind it is UNVERIFIED |
| "Open the link from the terminal" | The token changes at every API start. Use the new link printed by the launcher; clear site data for `127.0.0.1:3000` |
| Phone cannot connect | WSL still on NAT (check section 2.1 "WSL networking": needs `mirrored`; `wsl --shutdown` after editing `.wslconfig`), Hyper-V firewall rules missing (admin PowerShell, section 1.2), `INGEST_BIND_HOST` not set (default binds loopback only: use `--phone`), `INGEST_SRT_PASSPHRASE` missing or not 10 to 79 characters of `A-Za-z0-9_-` (the ingest refuses to start; `real.sh --phone` exits 2), the app has a different passphrase, the phone was given a 172.x address (set `PAIRING_HOST` to the PC's LAN IPv4), phone not on the same Wi-Fi, guest-network isolation. Fallback: OBS on this PC |
| OBS connects then drops, or is rejected | Wrong or stale login (the login changes every session: re-copy the URL); port taken; check `/tmp/ss_api.log` |
| After restarting OBS/sender the output is stuck or `MuxerError` / long `auto_egress` hold in logs | Fixed in the relay (timeline-reset). Expect recovery in 3 to 8 s. If it repeats every ~14 s, restart the session. Verified on a rig, not live |
| YouTube shows "not live" / no data | Live not enabled on the channel (up to 24 h), wrong or expired stream key, broadcast not started in Studio, session state not `live`, no internet. Try Twitch or the rehearsal tap |
| No YouTube chat | Not started with `--chat`; no stored OAuth login (`yt_login_required` in `GET /api/session/health` status codes: run the standalone login, section 4.2); consent screen not in test-user list; 7-day token expiry (log in again); broadcast not active (the poller retries every 10 s) |
| Groq or Sarvam errors / odd speech handling | Bad key, or rate limits (Groq limits are inconsistent in docs and unconfirmed for your account; Sarvam rounding of short requests unconfirmed). The system falls back automatically (Sarvam, then local STT, then mute voiced segments without a verdict): expect choppy but safe audio and more chat messages held for the moderator. `groq_key_missing` in the chat status = rules only |
| Audio is muted | The engine watchdog mutes audio when the audio host stops heartbeating (grace about 2.5 s, vouch margin 1.0 s), or the thin-transcript fail-safe fired. It recovers when the host does; watch the "Live and masked. Still catching up: audio." line; if it persists, restart the session; check memory (OOM kill) |
| Your own face stays blurred after enrolling | The allowlist wiring fix (`vision/allowlist_feed.py`) is new and **not yet verified live**. Re-enrol (good light, face only, you alone in frame), wait a few seconds. Faces are masked for about 1 s after appearing on purpose. If still blurred, accept being masked: it fails safe |
| A setting changed on `/controls` seems to do nothing | Config is now republished to the engine after it starts (`config_sync`), but nothing has been verified live. `PATCH /api/config` answers `applied: published` or `pending`; neither means the engine applied it. Detection stride and GPU keys only apply at the next session start |
| `ss.sh: command killed at the 3500M memory limit` (exit 137) | Run with `SS_MEM_MAX=7000M` (or `SS_NO_MEMCAP=1`); raise `.wslconfig` `memory=` |
| `port ... is in use` on launch | An old stack: `pkill -f engine.main; pkill -f mediamtx; pkill -f app.api.main; pkill -f next-server` in WSL |
| Launcher waits and then exits 2 on the GPU lock | Another GPU job holds `/tmp/streamsarthi-gpu.lock` (gate, test, demo). Stop it |
| "web build failed" / "API did not start" | Read `/tmp/ss_webbuild.log` or `/tmp/ss_api.log` in WSL |
| Bad quality on a phone over 4G | Raise SRT latency to 500 ms, lower bitrate to about 3 Mbps; weak links trigger transport holds when the ingest is silent for more than 1 s (`relay.ingest_hold_after_s`) |

---

## 7. After the stream

1. Click **Stop** on `/go-live` (the engine and chat processes are stopped and the report is built in the background, about a few seconds).
2. Open the report from the top menu or `http://127.0.0.1:3000/report/<session id>` (the id is on `/go-live`, or in `GET /api/session`). It shows totals, a category timeline with hold bands, event table, guard warnings, chat counts, delay and fps. **Never images or raw PII** (every report is privacy-validated).
3. Export before closing the launcher: `GET /api/report/<id>/export?format=json` or `format=html`, or `/printable` (streamer token required).
4. Erase one with `DELETE /api/session/<id>/data`. Reports are saved to the sessions dir when a session stops (**UNVERIFIED** until the product agent reports; the code writes `report.json` next to the session's event log). Until that is confirmed, assume a report that you did not export is lost when you Ctrl-C `real.sh`.
5. Note the real numbers for `docs/COST.md` (the Sarvam and Groq invoices, the YouTube quota dashboard) and fill in the rehearsal log in `docs/DEMO.md` section 9.

---

## 8. Known limits (read before trusting it)

- **Accuracy numbers are synthetic** (SPECIMEN cards, TTS speech, scripted chat), measured at clean and light quality. At the heavy blur/dark level recall is far lower (plates about 5 %, OTP 0 to 31 %, game IDs about 5 to 13 %); only the OTP banner has a fallback on by default.
- Video red-team leaks still open: a card entering the frame fast, a card at the frame edge, scene cuts, fast pans, a QR entering or flashing.
- Signboards use mask-all-text: text-heavy screens also go dark. Keep `signboard` off for game and screen scenes.
- **Abuse and doxxing detection need the Groq key**; rules alone catch about 82 % of abuse on the blind set (fake UPI links 81 %). Split words and leetspeak chat evasion still get through. Expect many moderator holds on gaming chat.
- **Spoken-PII on real voices is unverified**: the audio gate used an oracle recogniser and English TTS. Real Sarvam, Groq and Hinglish/regional speech have never been run. A UPI id spelled letter by letter cannot be recognised within the delay.
- Nothing has been rehearsed on the RTX 5050 laptop; no 30-minute soak; no 4G test on a real phone; OBS and Moblin/Larix never used with this stack.
- **Control-plane settings** (category toggles, spoken-PII toggles, audio languages, mute mode, styles) are now republished to the engine, vision and audio, but this has **not been verified live**; the UI cannot confirm the engine applied a change.
- The face allowlist wiring fix is new and not verified live (section 6).
- Captions need `SARVAM_API_KEY` (never called live); local IndicTrans2 has no weights.
- Chat OAuth, live deletes and timeouts, YouTube quota behaviour: never run against the real platforms.
- The Windows global hotkey is not built.

---

## 9. Quick reference

| Task | Command |
|---|---|
| Readiness | `MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh python scripts/doctor.py --real` |
| Level 1 | `MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash -c "SS_MEM_MAX=7000M bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh bash scripts/real.sh --gpu"` |
| Level 2 | same, with `--gpu --chat`, then stop the rehearsal session and start from `/go-live` |
| Phone | add `--phone` to the `real.sh` command, with `INGEST_SRT_PASSPHRASE=<yours>` in `.env` (and `PAIRING_HOST=<LAN ip>` under WSL NAT) |
| Chat login (once) | `... bash -c "CHAT__ENABLED=true bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh python -m chat.main --login"` (standalone; no session needed) |
| Hold | button, key H, `!hold`, `POST /api/hold` |
| Stop | Stop on `/go-live`, then Ctrl-C the launcher |
| Ports | 8000 API, 3000 web, 8890/udp SRT, 1935 RTMP, 9997 MediaMTX control |
