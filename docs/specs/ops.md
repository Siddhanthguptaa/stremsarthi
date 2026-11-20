# Spec: ops (environment, config profiles, scripts, models)

## 1. Purpose and owner
Everything repo-wide that no builder is allowed to touch: Python/Node environment, pinned requirements, config profiles, setup and doctor scripts, tool fetching (MediaMTX), model manifest, GPU lock wrapper, the Windows hotkey helper, `.env.example` additions.
Owner: **lead** (task **T0.1**, Wave 0.A). `scripts/run_*` belong to pipeline-engineer and `scripts/gate_*` to eval-engineer. Nothing here is installed without the user's go-ahead (section 7).

## 2. Directories owned and file layout
```
pyproject.toml                     # project metadata, ruff, pytest, markers
requirements/
  base.txt  gpu.txt  extras-torch.txt  extras-indic-ocr.txt  extras-agpl.txt  dev.txt
config/
  profiles/dev.yaml  demo.yaml          # base values live in contracts/config.py
  settings_schema.md               # generated from the pydantic Config (docs only)
scripts/
  setup_wsl.sh  doctor.py  fetch_tools.sh  gpu_run.sh  hold_hotkey_win.py  bounded.py
  (run_engine, run_api, run_chat belong to their owners; gate_*.sh belong to eval)
models/
  prepare.py  manifest.json  tools.lock.json  fonts.json
assets/fonts/                      # Noto Sans + Devanagari/Kannada/Tamil/Telugu/Bengali (OFL), fetched not committed
.env.example                       # additions in section 5.9
```

## 3. Inputs
The user's machine (Windows 11 + WSL2 Ubuntu 24.04, dev: i5-13450HX + RTX 3050 laptop 6 GB, driver 581.86; demo: RTX 5050 8 GB), network access for downloads (after approval), `.env`.

## 4. Outputs
A reproducible `.venv` (Python 3.11 via `uv`), verified tool and model files (SHA-256), loaded `Config` for either profile, a doctor report, and the helper scripts below.

## 5. Behaviour

### 5.1 `pyproject.toml`
`requires-python = ">=3.11,<3.12"`. Packages: `contracts`, `engine`, `relay`, `vision`, `audio`, `guard`, `chat`, `app.api`, `eval` (installed editable, `pip install -e .` with no dependencies listed here; dependencies come from `requirements/`). Tooling:
- `[tool.ruff]` `line-length = 100`, `target-version = "py311"`, select `E,F,I,UP,B,ASYNC,S,RUF`, ignore `S101` in tests; exclude `reference/`.
- `[tool.pytest.ini_options]` `testpaths = ["contracts/tests","engine/tests","relay/tests","vision","audio","guard","chat","app/api/tests","eval/tests"]`, `addopts = "-q -m 'not gpu and not live and not slow'"`, markers `gpu`, `live`, `slow`, `redteam`. Module tests live in each module's own `tests/` directory.
- `[tool.mypy]` optional, `strict = false`, only for `contracts`.

### 5.2 Requirements (pins from the dependency research; Python 3.11)
`requirements/base.txt`
```
numpy==2.2.6                       # 2.5.x needs Python 3.12
opencv-contrib-python-headless==4.13.0.92   # only this OpenCV package; never install the other three
onnx==1.23.2
rapidocr==3.10.0
pyclipper==1.4.0
shapely==2.2.0                      # UNVERIFIED exact patch; confirm at T0.1
omegaconf==2.4.0
PyYAML==6.0.3
pillow==11.2.1
supervision==0.32.0                 # ByteTrack (MIT)
zxing-cpp==3.1.1
av==16.0.1                          # 19.x needs Python 3.12; <=18.1.0 cap
silero-vad==6.2.3
faster-whisper==1.2.1
ctranslate2==4.9.0                  # CUDA build compatibility UNVERIFIED; CPU path is the default
soundfile==0.14.0
pyacoustid==1.3.1                   # Chromaprint (system libchromaprint/fpcalc)
sarvamai==0.1.48                    # licence field blank on PyPI: UNVERIFIED
groq==1.7.0
twitchAPI==4.5.0
keyring==25.6.0
cryptography                        # allowlist encryption at rest (pin at T0.1)
google-api-python-client==2.202.0
google-auth-oauthlib                # loopback PKCE flow (pin at T0.1)
aiohttp==3.14.5
httpx==0.28.1
fastapi==0.165.0
uvicorn==0.54.0
websockets==17.4
pyzmq                               # event bus (pin at T0.1)
pydantic==2.14.4
pydantic-settings                   # pin at T0.1
python-dotenv==1.2.4
psutil==7.0.0
qrcode==8.2                         # Moblin pairing QR
espeakng-loader                     # offline TTS fallback for eval (or system espeak-ng)
```
`requirements/gpu.txt`: `onnxruntime-gpu==1.26.0` (the ONLY onnxruntime; never alongside `onnxruntime`; avoid 1.23.0/1.23.1: sm_120 kernel regression), plus `nvidia-*` CUDA 12.8 wheels only if torch is not installed (let torch cu128 supply cuDNN/cuBLAS when the extra is present). Call `onnxruntime.preload_dlls()` at start and set `CUDA_CACHE_MAXSIZE=4294967296`, `CUDA_CACHE_PATH=~/.cache/streamsarthi/cuda` so the sm_120 JIT result is cached.
`requirements/extras-torch.txt` (optional: IndicTrans2, EasyOCR): `--extra-index-url https://download.pytorch.org/whl/cu128`, `torch==2.11.0`, `torchvision==0.26.0`, `torchaudio==2.11.0` (vision/audio pairing UNVERIFIED), `transformers==4.57.*`, `sentencepiece==0.2.3`, `IndicTransToolkit==1.1.1`.
`requirements/extras-indic-ocr.txt`: `easyocr==1.7.2` (Kannada/Bengali recognisers on crops; pulls torch).
`requirements/extras-agpl.txt`: `ultralytics==8.3.189` (**AGPL-3.0**) and `insightface==0.7.3` (code MIT, **weights non-commercial**). Optional adapters only, flagged "replace before commercial release" in `docs/THIRD_PARTY.md`.
`requirements/dev.txt`: `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `mypy`, `types-PyYAML`, `playwright`.
Explicitly **excluded**: `onnxruntime` (CPU wheel) with GPU, `opencv-python`/`opencv-python-headless` (conflict), `paddlepaddle`/`paddleocr`/`paddlex`/`modelscope`, `openai-whisper`, `webrtcvad`, `asyncio` (PyPI backport), `flask*`, `eventlet`, `mediasoup`.
Node: Node 20+, `app/web/package.json` per `app-web.md`. System packages (user runs with sudo): `ffmpeg` (Ubuntu 24.04 has 6.1.1 with libsrt, libx264, nvenc, chromaprint: checked on the dev machine), `libraqm0`, `libchromaprint-tools`, `espeak-ng`, `fonts-noto-core` (optional; we fetch our own), `iproute2` (for `tc`), `build-essential` if wheels are missing.

### 5.3 Config profiles (`config/profiles/{dev,demo}.yaml`, loaded by `contracts.config.load_config(profile)`)
There is no `base.yaml`: the defaults live in `contracts/config.py` (the authoritative key list, with ranges and validators) and each profile file lists only the values it changes. Priority: init kwargs > process env > `.env` > profile YAML > defaults. Env variables use the plain field name, with `__` for nesting: `STREAM_DELAY_SECONDS` (alias of `delay_seconds`), `DETECT__STRIDE=2`, `GROQ_API_KEY`. `STREAMSARTHI_PROFILE` picks the profile when none is passed.

Top-level sections of `Config`: `profile`, `delay_seconds` (3.0-5.0), `fps`, `canvas`, `detect` (`stride`, `fast_hz`, `accurate_hz`, `accurate_width`), `engine` (`window.before/after`, `lane_max_age.fast/accurate/slow`, `protect_fill_s`, `max_mask_area_ratio`, `timing_log_path`), `runtime.modules` (`module:build` paths), `relay` (`srt_latency_ms`, `video_bitrate_kbps`, `audio_bitrate_kbps`, `keyframe_seconds`, `encoder`, `ingest_hold_after_s`, `mediamtx_api_port`), `vision` (`allowlist_dir`, `face_match_cos`, `face_vote_window`, `face_vote_min`), `ocr`, `signboard`, `gpu` (`enabled`, `mem_limit_mb`, `ort_provider`, `lock_path`), `categories.<category>` (`enabled`, `style`, `sensitivity`, `mask_when_unsure`, `padding_px`), `audio` (segmenter, `vad.*`, `stt_order`, `stt.sarvam.transport`, `race_hedge_s`, `watch_window_s`, `llm_rpm`, `groq_rpm`, `local_stt_model/device`, `languages`, `mute_mode`, `address_mute`, `failsafe_mute_voiced`), `hold` (`retro_seconds`, slate texts), `chat`, `guard` (thresholds, `weights`, `risky_text_blocklist`), `captions` (`enabled`, `target_lang`, `provider`, `on_late`), `destinations`, `api`, `enrol`, `eval`, ports and secrets (`SecretStr`, env only).

`dev.yaml` sets `profile: dev`, `detect.stride: 3`, `gpu.enabled: false` (CPU default), `gpu.mem_limit_mb: 3500`, `audio.local_stt_model: base`, `captions.provider: sarvam`. `demo.yaml` sets `profile: demo`, `detect.stride: 2`, `detect.fast_hz: 15`, `detect.accurate_hz: 5`, `gpu.enabled: true`, `gpu.ort_provider: cuda`, `gpu.mem_limit_mb: 7000`, `audio.local_stt_model: small`, `audio.local_stt_device: cuda`, `captions.provider: indictrans2`. Validators enforce the profile limits (dev GPU cap 3500 MB, demo 7000 MB, local IndicTrans2 is demo-only). Profiles change values, never code paths. Secrets never appear in YAML. Hotkey and `encode` presets are not config keys: the hotkey helper takes argv, and `relay.encoder` plus the bitrate keys cover encoding.

### 5.4 `scripts/doctor.py` (exit 0 ok, 1 warnings only, 2 blocking failure; JSON with `--json`)
Checks, in order, each printing PASS/WARN/FAIL and the fix:
1. OS is Linux under WSL2; Python is 3.11.x; `uv` present; venv active.
2. `nvidia-smi` works; driver ≥ 570; GPU name and compute capability; VRAM total vs profile cap (`dev` cap 3.5 GB must be ≤ 85 % of total).
3. `/usr/lib/wsl/lib/libnvidia-encode.so.1` present; `ffmpeg -encoders` lists `libx264`, `h264_nvenc` (WARN if missing: x264 fallback) and `ffmpeg -protocols` lists `srt`.
4. `onnxruntime.get_available_providers()` contains `CUDAExecutionProvider` when `gpu.enabled`; a 1-op CUDA session runs; only one onnxruntime distribution installed (`importlib.metadata`).
5. If the torch extra is installed: `torch.cuda.is_available()` and `torch.cuda.get_arch_list()` includes `sm_86` (dev) and `sm_120` (demo) as appropriate (printed in full).
6. `cv2` import works; exactly one OpenCV distribution; `cv2.FaceDetectorYN` and `cv2.wechat_qrcode` exist.
7. `libraqm` available (`PIL.features.check('raqm')`).
8. MediaMTX binary hash matches `models/tools.lock.json`; model files match `models/manifest.json` SHA-256.
9. Ports 8890/udp, 1935/tcp, 8000, 3000 free; WSL networking mode (`wslinfo --networking-mode` when present; WARN if not `mirrored`).
10. `.env` has the keys the chosen features need (reports presence only, never values).
11. `fpcalc` present (Chromaprint); `espeak-ng` present (WARN).
12. Clock sanity and free disk ≥ 10 GB; `/tmp/streamsarthi-gpu.lock` creatable.

### 5.5 `scripts/fetch_tools.sh`
Downloads MediaMTX **v1.21.4** `linux_amd64` (`https://github.com/bluenviron/mediamtx/releases/download/v1.21.4/mediamtx_v1.21.4_linux_amd64.tar.gz`) into `.tmp/tools/mediamtx-v1.21.4/` (gitignored). The SHA-256 is not known in advance (UNVERIFIED): on first run T0.1 downloads the release's published checksums file, verifies the archive against it, and writes `models/tools.lock.json` `{name, version, url, sha256}` (committed). Every later run refuses a mismatch (adapt `reference/irl-shield/scripts/m2-tools`, which pins v1.21.3). No execution before verification; extraction into a fresh empty directory; the config is passed on stdin by `relay/mediamtx.py` (adapted from IRL Shield).

### 5.6 `scripts/setup_wsl.sh` (idempotent, prints each step, never runs `sudo` itself; it prints the commands for the user)
1. Check WSL version and Ubuntu 24.04. Print the **user-run** steps: `sudo apt update && sudo apt install -y ffmpeg libraqm0 libchromaprint-tools espeak-ng iproute2 build-essential`.
2. `uv python install 3.11`; `uv venv --python 3.11 .venv`; `uv pip install -r requirements/base.txt -r requirements/dev.txt` and `requirements/gpu.txt` if `nvidia-smi` works; extras only with `--extras torch,indic-ocr`.
3. `python -m pip check`; `bash scripts/fetch_tools.sh`; `python models/prepare.py --profile dev`.
4. `npm ci` in `app/web` once it exists; `npx playwright install chromium` (dev).
5. `cp -n .env.example .env`; remind the user to fill it. Run `python scripts/doctor.py`.
**Windows-side steps the user runs (admin PowerShell, not automated):**
```
# %UserProfile%\.wslconfig
[wsl2]
networkingMode=mirrored
# then: wsl --shutdown
New-NetFirewallHyperVRule -Name "StreamSarthi-SRT"  -DisplayName "Stream Sarthi SRT"  -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol UDP -LocalPorts 8890
New-NetFirewallHyperVRule -Name "StreamSarthi-RTMP" -DisplayName "Stream Sarthi RTMP" -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 1935
```
(Rule syntax per Microsoft Learn WSL networking page; verify at setup.)

### 5.7 GPU lock wrapper
`scripts/gpu_run.sh`: `#!/usr/bin/env bash` / `exec flock /tmp/streamsarthi-gpu.lock "$@"`. Python code uses `eval/harness/gpu_lock.py` (`fcntl.flock` on the same path) so shell and Python jobs exclude each other. Unit tests never use the GPU.

### 5.8 Hotkey helper (`scripts/hold_hotkey_win.py`, runs on **Windows**, stdlib only)
Registers global hotkeys with `ctypes.windll.user32.RegisterHotKey` (default `Ctrl+Alt+H` = hold, `Ctrl+Alt+J` = unhold, configurable by argv), runs a message loop, and on press POSTs `http://127.0.0.1:8000/api/hold` (`{"active": true|false}`) with `Authorization: Bearer <hotkey token>` read from `%USERPROFILE%\.streamsarthi\hotkey_token`. The token comes from `POST /api/auth/hotkey-token` (hold/unhold scope only, TTL 24 h; `app-api.md`). Beeps (`winsound.Beep`) on success/failure, retries once, timeout 1 s, never blocks. Works because WSL mirrored networking shares `127.0.0.1`. Needs only the Windows Python already installed; no pip packages.

### 5.9 `.env.example` additions
`SECRET_KEY=` (HMAC signing, required), `REQUIRE_AUTH=true`, `STREAMSARTHI_PROFILE=dev`, `API_PORT=8000`, `WEB_PORT=3000`, `HF_TOKEN=` (IndicTrans2 gated model, demo only), `ALLOWLIST_KEY=` (optional, only with `enrol.persist`), `GROQ_API_KEY`/`SARVAM_API_KEY` already present. Existing keys stay. `.gitignore` additions: `.tmp/`, `eval/data/private/`, `eval/runs/`, `assets/fonts/`, `*.enc`, `~/.streamsarthi` is outside the repo.

### 5.10 `models/manifest.json` (extends IRL Shield's hash-pinned format; `models/prepare.py` adapted from `reference/irl-shield/models/prepare.py`)
Each entry: `id`, `file`, `source_url`, `sha256` (mandatory; filled when first fetched, then pinned), `size_bytes`, `license`, `commercial_ok` (bool), `used_by`, `device`. Entries to add beyond IRL Shield's PP-OCRv5/v6 detection and en rec:
| id | file | licence | commercial_ok | used by |
|---|---|---|---|---|
| `yunet-2023mar` | `face_detection_yunet_2023mar.onnx` (OpenCV Zoo) | MIT | yes | faces |
| `sface-2021dec` | `face_recognition_sface_2021dec.onnx` (OpenCV Zoo) | Apache-2.0 | yes | faces |
| `ppocr-rec-devanagari`, `ppocr-rec-ta`, `ppocr-rec-te` | RapidOCR ONNX recognisers | Apache-2.0 | yes | text |
| `silero-vad` | bundled with the `silero-vad` package | MIT | yes | audio |
| `yamnet` | ONNX/tflite export (source UNVERIFIED) | Apache-2.0 | yes | guard |
| `noto-*` (Sans, Devanagari, Kannada, Tamil, Telugu, Bengali; listed in `models/fonts.json`) | `assets/fonts/*.ttf` | OFL-1.1 | yes | engine captions, eval |
| `insightface-buffalo_s` (optional extra) | – | non-commercial | **no** | optional adapter |
| `yolo-plate` (optional, only if weights exist) | – | AGPL-3.0 | **no** | optional plates |
`prepare.py --profile dev|demo` fetches only what the profile needs; `--check` verifies hashes and exits non-zero on mismatch.

## 6. Reuse
| Source | Target | Change |
|---|---|---|
| `reference/irl-shield/models/prepare.py`, `manifest.json` | `models/prepare.py`, `models/manifest.json` | Add entries, `commercial_ok`, profiles |
| `reference/irl-shield/scripts/m2-tools` | `scripts/fetch_tools.sh` | v1.21.4, lock file, Linux only |
| `reference/irl-shield/scripts/bounded.py` | `scripts/bounded.py` | As is |
| `reference/privastream/src/models/audio/check_gpu.py` | `scripts/doctor.py` | Idea only |
| `reference/irl-shield/requirements*.txt`, `reference/privastream/**/requirements*.txt` | none | Replaced by `requirements/` (Pascal-era CUDA 12.4 pins, three OpenCV packages, UTF-16 file) |

## 7. Edge cases and approvals
- **Nothing is installed or downloaded without the user's go-ahead.** `setup_wsl.sh` is written in Step 0 but first *run* in T0.1 after the user confirms Phase 0 installs.
- ORT on the RTX 5050: first CUDA run JIT-compiles for sm_120 and is slow; the cache env vars above keep it. If the CUDA EP fails to load, `doctor` reports it and the profile falls back to `gpu.ort_provider=cpu` (never a code change). `onnxruntime-blackwell` / `onnxruntime-ep-cuda` are the fallbacks to evaluate (UNVERIFIED).
- WSL without NVENC: encoder falls back to x264 automatically (`gpu.encode=auto`).
- Python 3.12 only on the machine: `uv` installs 3.11; never use system Python for the venv.
- Corporate proxy / offline: `prepare.py` supports `--offline-dir` for pre-downloaded files, still hash-checked.
- `.wslconfig` change needs `wsl --shutdown` (the user closes sessions).

## 8. Config keys used
All of `Config` (section 5.3) and `.env` (section 5.9).

## 9. Test cases (CPU; `scripts/tests/` and `contracts/tests/`)
1. `load_config("dev")` and `load_config("demo")` both validate; their key sets are identical; `delay_seconds` outside 3.0–5.0 is rejected.
2. Env override `DETECT__STRIDE=2` changes the value; `SecretStr` secrets never appear in `repr`.
3. `doctor.py --json` on a mocked environment returns the right exit codes for pass / warn / blocking cases (monkeypatched checks).
4. `fetch_tools.sh` against a local file server: refuses a tampered archive, accepts the pinned one, never executes before verification.
5. `models/prepare.py --check`: detects a corrupted file, a missing licence field, and `commercial_ok=false` entries not in the default profile.
6. Requirements lint: no forbidden package (`onnxruntime` next to `onnxruntime-gpu`, `opencv-python*`, `paddle*`) appears in any file under `requirements/`.
7. `hold_hotkey_win.py` unit test with a fake HTTP server and a stub for `user32` (runs on Linux with a shim): a hotkey event produces the right POST with the bearer token; failure path beeps and does not raise.
8. `gpu_run.sh`: two concurrent invocations serialise (second waits), verified with timestamps.
**Acceptance (T0.1):** `scripts/doctor.py` passes on the dev laptop (warnings allowed for NVENC/mirrored networking until the user applies them) and `scripts/gate_phase0.sh` step 1 succeeds.

## 10. Latency target
Setup is one-off. `doctor.py` ≤ 15 s. `models/prepare.py --check` ≤ 10 s.

## 11. VRAM / CPU target
Not applicable at runtime. Profile caps enforced by config: dev 3.5 GB, demo 7.0 GB.

## 12. Tasks
**T0.1** (Wave 0.A, lead, deps Step 0): everything in this spec. **T7.4** (lead): `docs/DEMO.md`, `docs/COST.md`. Follow-ups from builders (new pins, new models, new env keys) arrive as lead tasks, never as edits by builders.

## 13. Out of scope
Docker/compose packaging and the one-command installer (stretch), CI services, cloud deployment, Linux NVIDIA driver installation inside WSL (never), dual boot, native Windows runs, training environments (Colab/Kaggle only).
