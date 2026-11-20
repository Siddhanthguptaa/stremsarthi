# Builder brief (read this first, every task)

You are a builder subagent for Stream Sarthi. The lead gives you a task id. You then read, in this order: `CLAUDE.md`, this file, `docs/specs/<your module>.md` (the section for your task), the contracts you use in `contracts/` (read the code, not just the spec), and the plan sections the spec points to (`docs/PLAN.md`). Everything you need is there; do not ask the lead questions unless a gate-level decision is missing.

## Hard rules
1. **Edit only the directories listed for your task.** If you need a change elsewhere (contracts, another module, `pyproject.toml`, `requirements/`, `config/`), do not make it: report it with the exact change and reason. Never edit `reference/`. Never edit `contracts/` (lead only).
2. **No git commands.** The lead commits.
3. **Privacy:** never real Aadhaar/PAN/bank data or anyone's personal data; synthetic data only, visibly marked SPECIMEN. Never store or log images or raw PII. Secrets live in `.env`: never read, print or log it (the settings deny reading it; the code loads it through `contracts.load_config`). API keys may be empty: build against mocks and recorded fixtures, make live API checks optional (skip when the key is absent), and say so in your report.
4. **Reuse first.** Copy from `reference/` into your directories as the spec says, keep the upstream licence header and licence files next to copied code, and list every copied file in your report (source path, licence, what you changed). Never copy from Offleak (AGPL).
5. **Modules talk only through `contracts/`.** Use the fakes in `contracts/fakes/` to test against other modules, never import another module's code (except the documented `module:build` loading done by the engine).
6. **Every video component has a CPU path** that runs in real time at its configured rate, plus a CPU-only test. GPU is optional acceleration. GPU commands must be wrapped: `flock /tmp/streamsarthi-gpu.lock <command>`. One GPU job at a time.
7. **Every network call is async, has a timeout and a local fallback** and never blocks the frame path.
8. Python 3.11, type hints, `ruff` clean (`ruff check` and `ruff format` on your directories). Tests are pytest, CPU-only, in `<your dir>/tests/` (add an `__init__.py`-free layout; the repo uses `--import-mode=importlib`, so name test files uniquely, e.g. `test_engine_deadline.py`). Add a `README.md` of at most 40 lines to each top-level package you create describing how to run its tests and entry points.
9. A task is done only when: its tests pass, its acceptance test passes, it meets its latency and VRAM/CPU budget, and your report has the numbers. Be honest: if something fails or was skipped, say so with the output. Do not pad results.

## How to run commands (important)
The repo lives on the **Windows disk**: `C:\Users\siddh\stremsarthi`. Edit files with Read/Write/Edit using that Windows path. (It used to live inside WSL and be reached through the `\\wsl$` share, which kept dropping and broke every tool; do not use `\\wsl$` paths any more.) You run Python and tools **inside WSL** (Ubuntu 24.04), where the same folder is `/mnt/c/Users/siddh/stremsarthi`, through the wrapper, from the Bash tool (Git Bash on Windows):

```bash
MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh <command> [args...]
# examples
MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh python -m pytest engine/tests -q
MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh ruff check engine
MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh flock /tmp/streamsarthi-gpu.lock python -m eval.harness.something
```
The wrapper sets the venv (Python 3.11, kept in WSL at `~/.venvs/stremsarthi` for speed), `PYTHONPATH` to the repo root (so `import engine`, `import vision`, `import contracts` work), a clean PATH (ffmpeg, node, uv), the working directory, and a memory cap.
- Do **not** put `$VARS` or Windows paths inside the command: `wsl.exe` expands them and breaks. Redirect output **inside** WSL (`bash -c 'cmd > /tmp/x.log 2>&1'`), never with a Git Bash redirect to a `/mnt/c` path. For anything complicated, write a script file inside the repo (for example under `.tmp/`, which is gitignored; use a UNIQUE file name per agent) and run `bash .tmp/yourname_x.sh` through the wrapper.
- Long-running servers: start them with `run_in_background` and stop them when done. Never leave processes running when you finish.
- **Web (`app/web`)**: npm cannot create symlinks on the Windows disk, so run npm through `scripts/web.sh`, which syncs the sources into a WSL-native build directory (`~/webbuild`, where `node_modules` and `.next` live) and runs npm there: `... ss.sh bash scripts/web.sh run build`, `... bash scripts/web.sh test`, `... bash scripts/web.sh run lint`. Edit sources in `app/web` in the repo, never in `~/webbuild`. After `npm install <pkg>` through `web.sh`, copy the updated manifests back: `cp ~/webbuild/package.json ~/webbuild/package-lock.json app/web/`.
- Do not install Python packages. If a package you need is missing, report it (the lead owns `requirements/`). The venv has: numpy, scipy, opencv-contrib-python-headless, onnxruntime(-gpu), rapidocr, pillow, pydantic, av, soundfile, zxing-cpp, pyzmq, fastapi/uvicorn/websockets, aiohttp/httpx, groq, sarvamai, twitchAPI, google-api-python-client, faster-whisper (no-deps install), qrcode, python-multipart, pytest/ruff/hypothesis. Node 22 is at `~/.local/node` (on the wrapper's PATH).
- `ffmpeg` 6.1 (libx264, h264_nvenc, libsrt) is on PATH. MediaMTX is under `.tmp/tools/mediamtx-v*/` (run `bash scripts/fetch_tools.sh` through the wrapper if missing).
- Internet access works for downloading model files and public fonts: verify SHA-256, never execute downloaded code, extract into a fresh empty directory, and record the source URL and licence.
- No `sudo`/`apt` in WSL (no password). libraqm is available through the Pillow wheel: check `PIL.features.check("raqm")` and have a fallback.
- File I/O on `/mnt/c` is slower than the native WSL disk: keep test data small, avoid thousands of tiny files, and put big scratch data under `/tmp` (WSL native), not in the repo.

## Hardware and budgets
Dev laptop: i5-13450HX (6P+4E, 16 threads), RTX 3050 **6 GB** (sm_86), Windows 11 + WSL2. Dev profile GPU cap 3.5 GB; demo (RTX 5050, 8 GB) cap 7 GB via `config/profiles/demo.yaml` only. Video target 720p30, detect every 3rd frame, end-to-end delay <= 5 s (capture to egress socket, D-1). See `docs/PLAN.md` section 2.5 for stage budgets.

## Report format (your final message)
- Task id and one-line outcome.
- Files created/changed (grouped), files reused from `reference/` (source, licence, changes).
- Test results (command and counts), acceptance test result, measured numbers (latency, fps, CPU, VRAM, precision/recall where relevant) with the hardware.
- Things you could not do or verify; contract changes you need (exact field and reason); follow-up tasks for other owners; anything the user must provide.
Keep it under 400 words. No raw dumps.

## WSL can still hiccup (read this)
Many agents call `wsl.exe` at once and the WSL service sometimes answers `Wsl/Service/E_UNEXPECTED` or the `\wsl$` share briefly disappears. This is transient. Rules:
- If a `wsl.exe` call fails with `E_UNEXPECTED` or "Catastrophic failure", wait 5-10 seconds and retry, up to 6 times, before reporting a blocker.
- Prefer ONE longer command over many tiny ones (batch several shell steps into one `bash -c` or a script file) so fewer `wsl.exe` processes start.
- Never run more than one heavy test session at a time yourself; use `-x -q` and targeted test paths, not the whole suite, while iterating.
- If the tools refuse with "The working directory no longer leads where it did...", the share is down: wait and retry the same call; do not give up on the first refusal.

## Memory limit (important)
The WSL VM has about 7.8 GB shared by every agent. `scripts/ss.sh` now runs each command in a cgroup with a 3.5 GB hard limit (exit code 137 plus a message means YOUR command hit it: that is a bug in the test or code, usually an unbounded buffer/ring or a model loaded many times). Do not raise the limit to make a test pass: fix the leak, or run a smaller test set. Never run the whole repository's tests in one process; run your own directory only. A runaway process used to crash the WSL share for everyone.
