# Status

Updated by the lead after each wave. Source of truth for scope: `docs/PLAN.md`. Specs: `docs/specs/`. Decisions: `docs/DECISIONS.md`.

**Current position:** Step 0 complete (plan, specs, contracts, decisions, third-party list, status). Nothing else has started. Next: task T0.1 (environment), then Wave 0.B.

Status values: `pending`, `in progress`, `blocked`, `done`. A task is **done** only when its tests pass, its acceptance test passes, it meets its latency and VRAM budget, the reviewer approved it, and its numbers are recorded below.

## Step 0

| Item | Status | Notes |
|---|---|---|
| `docs/PLAN.md` | done | approved 2026-11-20 ("Proceed") |
| `docs/specs/*.md` (11 files) | done | engine, relay, vision, audio, guard, chat, app-api, app-web, eval, ops, contracts |
| `contracts/` + fakes + tests | done | `pytest contracts/tests`: 109 passed, 1 skipped (the numpy-backed frame fake: numpy is broken in the Windows Python used for Step 0); `ruff check contracts` clean |
| `docs/DECISIONS.md`, `docs/THIRD_PARTY.md`, `docs/STATUS.md` | done | |
| `config/profiles/dev.yaml`, `demo.yaml`, minimal `pyproject.toml` | done | T0.1 extends `pyproject.toml` with dependencies |

## Tasks

| ID | Wave | Owner | Task | Depends | Status |
|---|---|---|---|---|---|
| T0.1 | 0.A | lead | Environment: `pyproject.toml`, `requirements/`, setup and doctor scripts, MediaMTX fetch, models dir | Step 0 | pending |
| T0.2 | 0.B | eval-engineer | Harness skeleton, synthetic sender, SPECIMEN generators v0, `gate_phase0.sh` | T0.1 | pending |
| T0.3 | 0.B | pipeline-engineer | `relay/`: MediaMTX supervisor, loopback reader | T0.1 | pending |
| T0.4 | 0.B | pipeline-engineer | `engine/`: deadline engine port, hold control, fail-safe | contracts | pending |
| T0.5 | 0.B | vision-engineer | `vision/` core: detector SDK, registry, lanes, ORT helper, model prepare | contracts | pending |
| T0.6 | 0.B | audio-engineer | `audio/` VAD, segmenter, STT adapters, API spike report | contracts | pending |
| T0.7 | 0.B | product-engineer | `app/api` skeleton, `app/web` scaffold | contracts | pending |
| T1.1 | 1 | pipeline-engineer | `engine/` compositor, motion projection, retro hold, bus wiring, timing logs | T0.3-T0.5 | pending |
| T1.2 | 1 | pipeline-engineer | `relay/` encoder process, egress, YouTube/Twitch destinations | T0.3 | pending |
| T1.3 | 1 | vision-engineer | `vision/detectors/cards/`, `vision/host.py` | T0.5 | pending |
| T1.4 | 1 | eval-engineer | Phase 1 gate: delay, card visibility, kill-detector, hold tests | T0.2 | pending |
| T1.5 | 1 | chat-engineer | `chat/` YouTube and Twitch spike, quota governor | T0.1 | pending |
| T1.6 | 1 | product-engineer | Control API (`/hold`, status, config), WebSocket fan-out, minimal page | T0.7 | pending |
| T2.1 | 2.A | eval-engineer | Video datasets and augmentations | G1 | pending |
| T2.2 | 2.A | pipeline-engineer | N lanes, per-category styles and fail-safe | G1 | pending |
| T2.3 | 2.A | vision-engineer | Tracking, hysteresis, MaskPlanner | G1 | pending |
| T2.4 | 2.A | vision-engineer | Text lane: patterns, signboard mask-all | G1 | pending |
| T2.5 | 2.A | product-engineer | Category toggles, face enrolment, `SafetyEvent` store | G1 | pending |
| T2.6 | 2.B | vision-engineer | Faces and allowlist | T2.3 | pending |
| T2.7 | 2.B | vision-engineer | UPI QR | T2.3 | pending |
| T2.8 | 2.B | vision-engineer | Plates | T2.4 | pending |
| T2.9 | 2.B | vision-engineer | OTP screens, game IDs | T2.4 | pending |
| T2.10 | 2.B | eval-engineer | Per-category reports, `gate_phase2.sh` | T2.1 | pending |
| T3.1 | 3 | eval-engineer | Audio datasets (TTS-composed, ground-truth times), `gate_phase3.sh` | G2 | pending |
| T3.2 | 3 | audio-engineer | Orchestrator: segmenter, STT race, MuteSpans, fail-safe | T0.6 | pending |
| T3.3 | 3 | audio-engineer | Spoken-PII rules and Groq fallback | T0.6 | pending |
| T3.4 | 3 | pipeline-engineer | Audio mute, mouth blur, retro audio hold | T2.2 | pending |
| T3.5 | 3 | product-engineer | Alert feed, audio toggles | T2.5 | pending |
| T4.1 | 4 | eval-engineer | Chat datasets, mock platforms, `gate_phase4.sh` | G3 | pending |
| T4.2 | 4 | chat-engineer | Classifier cascade | contracts | pending |
| T4.3 | 4 | chat-engineer | YouTube connector and actions | T1.5 | pending |
| T4.4 | 4 | chat-engineer | Twitch connector, actions, commands | T1.5 | pending |
| T4.5 | 4 | chat-engineer | Pipeline orchestrator, mod queue, overlay feed | T4.2 | pending |
| T4.6 | 4 | product-engineer | Moderator console, streamer chat overlay | T4.5 | pending |
| T5.1 | 5 | audio-engineer | `guard/music/`: YAMNet, fingerprint library | G3 | pending |
| T5.2 | 5 | audio-engineer | `guard/risk/`, profanity, risk meter | G3 | pending |
| T5.3 | 5 | audio-engineer | Captions: Sarvam and IndicTrans2, `CaptionEvent` timing | T3.2 | pending |
| T5.4 | 5 | pipeline-engineer | Caption renderer (raqm, Noto fonts) | T3.4 | pending |
| T5.5 | 5 | eval-engineer | Guard datasets, `gate_phase5.sh` | G3 | pending |
| T6.1 | 6 | product-engineer | Go-live orchestration, safety report, risk stream | G5 | pending |
| T6.2 | 6 | product-engineer | Streamer web routes and shared layout | G5 | pending |
| T6.3 | 6 | product-engineer | Moderator and report routes | T6.2 | pending |
| T6.4 | 6 | pipeline-engineer | SRT tuning for 4G, ingest-drop hold, Moblin pairing | G5 | pending |
| T6.5 | 6 | eval-engineer | Playwright flows, `gate_phase6.sh` | T6.1-T6.3 | pending |
| T7.1 | 7.A | eval-engineer | Video red team | G6 | pending |
| T7.2 | 7.A | eval-engineer | Audio and chat red team | G6 | pending |
| T7.3 | 7.A | pipeline-engineer | Profiling, soak, `netem` 4G run | G6 | pending |
| T7.4 | 7.A | lead | `docs/DEMO.md`, `docs/COST.md` | G6 | pending |
| T7.5 | 7.B | vision / audio / chat engineers | Fix misses found by the red team | T7.1, T7.2 | pending |
| T7.6 | 7.B | eval-engineer | `gate_final.sh`, final report | T7.5 | pending |

## Gates

| Gate | Script | Status | Last run |
|---|---|---|---|
| G0 | `scripts/gate_phase0.sh` | pending | |
| G1 | `scripts/gate_phase1.sh [--live-youtube]` | pending | |
| G2 | `scripts/gate_phase2.sh` | pending | |
| G3 | `scripts/gate_phase3.sh` | pending | |
| G4 | `scripts/gate_phase4.sh` | pending | |
| G5 | `scripts/gate_phase5.sh` | pending | |
| G6 | `scripts/gate_phase6.sh` | pending | |
| Final | `scripts/gate_final.sh` | pending | |

## Numbers (filled in as gates run; hardware noted for every number)

| Metric | Target | Dev laptop (RTX 3050 6 GB) | Demo laptop (RTX 5050 8 GB) |
|---|---|---|---|
| Capture to egress delay, p95 | <= 5.0 s | | |
| Output fps | >= 29 | | |
| CPU cores used | <= 5 | | |
| VRAM | dev <= 3.5 GB, demo <= 7 GB | | |
| Video recall by category | see PLAN section 5, G2 | | |
| Spoken-PII mute coverage | >= 90 % | | |
| Audio pipeline p95 | <= 3.3 s | | |
| Chat classify p95 | <= 300 ms | | |
| Chat precision / recall | see PLAN section 5, G4 | | |
| Cost per streaming hour | | | |

## Open items

- Sarvam REST word timestamps (D-4, T0.6 spike).
- ORT 1.26 on the RTX 5050 (JIT for sm_120): test in T0.1 and every wave on the demo laptop.
- YouTube `streamList` Python client (D-13).
- User actions pending (PLAN section 9): API keys, YouTube live enablement, OAuth clients, `.wslconfig` mirrored networking, Hyper-V firewall rule, `apt` packages, Hugging Face token, demo laptop access, Bollywood demo clips, hackathon rules on pre-built code and AI disclosure.
