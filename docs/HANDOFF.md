# Handoff: how to resume the build (read this first in a new session)

Written 2026-11-21 by the lead, when the repo moved to the Windows disk (decision D-42). You are the **lead**. Read `CLAUDE.md`, `docs/PLAN.md` (approved plan: sections 5 and 6 are the gates and the task graph), `docs/STATUS.md` (task states and measured numbers), `docs/DECISIONS.md` (D-1 to D-42: every deviation and why), `docs/AGENT_BRIEF.md` (the rules every builder agent gets) and `docs/specs/<module>.md`.

## Where things are
- Repo: `C:\Users\siddh\stremsarthi` (Windows disk). Start Claude Code **in this folder**. WSL sees it as `/mnt/c/Users/siddh/stremsarthi`.
- Python venv: inside WSL at `~/.venvs/stremsarthi`. npm builds run in `~/webbuild` through `scripts/web.sh`.
- Run anything in WSL with: `MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/siddh/stremsarthi/scripts/ss.sh <command>` (cwd, venv, PATH and a 3.5 GB memory cap are set by the wrapper). Details in `docs/AGENT_BRIEF.md`.
- The old copy at `\\wsl$\Ubuntu-24.04\home\siddhanth\stremsarthi` is an archive: do not edit it.
- WSL config: `C:\Users\siddh\.wslconfig` has `memory=6GB`, `swap=8GB`, `vmIdleTimeout=-1`. `wsl --update` failed once (another Windows installer was running): retry later, optional.
- Keys (`.env`) are all EMPTY. No live Sarvam/Groq/YouTube/Twitch run has happened. Live gates need the user's keys.

## Lead rules (from CLAUDE.md and what we learned)
- Only the lead commits (git runs inside WSL via the wrapper: `... ss.sh git ...`, identity passed with `-c user.name=... -c user.email=...` from the Windows global config; no attribution lines in commit messages). Commit after each wave with the numbers in `docs/STATUS.md`.
- Builders edit only their own directories; the lead alone changes `contracts/` (log each change in `docs/DECISIONS.md`; bump `CONTRACT_VERSION`; update `contracts/tests/test_enum_snapshot.py` for enum changes).
- After every task report: verify, then update `docs/STATUS.md` (state + numbers), `docs/THIRD_PARTY.md` (reused files), `docs/DECISIONS.md` (new decisions), apply the lead-side requests in the report.
- **Never use Python `write_text` on Windows for shell scripts** (it writes CRLF and breaks bash). Use `write_bytes(s.encode())` or the Write/Edit tools.
- Never run the whole test suite in one process (a runaway test once used 7 GB and froze WSL). Use `bash scripts/mem_check.sh` or per-directory runs; each command is memory-capped.
- Keep at most 5 to 6 builder agents running at once; use the custom agent types (`pipeline-engineer`, `vision-engineer`, `audio-engineer`, `chat-engineer`, `product-engineer`, `eval-engineer`, `reviewer`). Every prompt starts: "First read docs/AGENT_BRIEF.md, then CLAUDE.md, the spec, ...".

## State of the tasks (see `docs/STATUS.md` for numbers)
**Done and accepted:** T0.1 to T0.7, T1.1 (engine compositor/session/bus), T1.3 (cards detector), T1.5 (chat connector spike), T1.6 (control API and hold page), T2.3 (tracking), T2.5 (enrolment, toggles, event store).

**Interrupted (files exist on disk, unverified: open and finish them, do not start over):**
| Task | Owner agent | What exists | Finish |
|---|---|---|---|
| T1.2 relay encoder/egress | pipeline-engineer | partial `relay/encoder.py`, `egress.py`, `destinations.py`, `tls_forwarder.py` | encoder process, N destinations, TLS forwarder, local RTMPS sink test, ffprobe validation, encode CPU numbers. A stray `auto.crt/auto.key` came from its test: certs must be generated in a temp dir |
| T1.4 Phase 1 gate tooling | eval-engineer | `eval/fault.py`, `tap.py`, `cardcheck.py`, `holdbus.py`, `phase1.py`, `phase1_report.py`, `stack.py`, `refengine.py`, `delay.py`, `scripts/gate_lib.sh` | tests, `scripts/gate_phase1.sh`, fault hooks list for engine/vision/relay owners; then run G1 |
| T2.1 video datasets | eval-engineer | generators under `eval/data_gen/video/`, `eval/fonts/` | finish generators, manifest, `python -m eval.run gen-video`, tests |
| T2.4 text lane | vision-engineer | `vision/detectors/text/`, `vision/ocr/multi.py`, `scripts.py`, `vision/patterns/*` (check) | detector, multi-script OCR enrichment, patterns, acceptance (phone/PIN/UPI/email >= 90 %, signboard >= 85 %) |
| T2.6 faces | vision-engineer | `vision/detectors/faces/` (async embedding pool in progress) | detector, `Enroller`, per-track votes, acceptance (>= 95 % masked / >= 95 % allowlisted visible) |
| T2.7 UPI QR | vision-engineer | `vision/detectors/upi_qr/` (scan.py being reworked) | decode + undecodable-QR masking, acceptance (>= 98 % recall, FAST p95 <= 25 ms) |
| T3.2 audio orchestrator | audio-engineer | `audio/orchestrator/` (check) | pipeline, STT race, spans, fail-safe, virtual-clock tests |
| T3.4 engine mute and mouth | pipeline-engineer | edits in `engine/audio_mute.py`, `mouth.py`, session | sample-accurate mute, bleep, retro audio hold, mouth resolution, tests |

**Not started:** T2.8 plates, T2.9 OTP screens and game IDs, T2.10 gate G2, T3.1 audio datasets (TTS), T3.3 spoken-PII rules, T3.5 alerts UI, all of Phase 4 (chat shield: T4.1 to T4.6), Phase 5 (guard, captions: T5.1 to T5.5), Phase 6 (product polish: T6.1 to T6.5), Phase 7 (red team, soak, final gate, demo script, cost).

## Immediate next steps
1. `... ss.sh bash scripts/mem_check.sh contracts/tests engine vision models audio chat app/api` to check the state (one process per directory).
2. Relaunch the interrupted tasks above (<= 5 at a time; start with T1.2 and T1.4 because they unblock Gate G1).
3. Run G0 (`scripts/gate_phase0.sh`) and then G1 (`scripts/gate_phase1.sh`); fix failures through the owners; commit; continue with Phase 2 to 7 per `docs/PLAN.md`.
4. Ask the user for the keys listed in `docs/PLAN.md` section 9 before any live gate, and for the Windows admin steps (mirrored networking, Hyper-V firewall rule) before the phone-ingest test.
