# Stream Sarthi: build runbook

**Plan with Opus 5.5 in plan mode, then build with Sonnet 5.5 running parallel subagents.** The `opusplan` model setting handles the switch automatically: Opus in plan mode, Sonnet once execution starts.

---

## 0. One-time setup

### Machine
- Windows 11 with WSL2 and Ubuntu 24.04, on both the development laptop (RTX 3050) and the demo laptop (RTX 5050)
- NVIDIA driver 570 or newer on Windows. WSL2 uses the Windows driver, so don't install a Linux NVIDIA driver inside WSL.
- Python 3.11 (install it with `uv`), Node 20+, and ffmpeg
- Docker with the NVIDIA Container Toolkit (optional)
- The latest Claude Code: run `claude update`. Setting `opusplan` from `/model` needs v2.1.265 or later.

### Folder
The `stremsarthi` folder is already set up: `reference/` holds the code copied from both projects, and `.claude/` holds the settings and subagents. Copy it into your WSL home folder, because working from `/mnt/c` is slow:
```bash
cp -r /mnt/c/Users/siddh/Desktop/sathack26/stremsarthi ~/ && cd ~/stremsarthi
cp .env.example .env          # fill in the keys you already have
git init && git add -A && git commit -m "Project brief, rules and reused code"
```
`reference/` is committed with the project. Claude reads from it but never edits it.

### Things only you can do (start now, they take time)
- Turn on live streaming on your YouTube channel. The first time can take up to a day to activate.
- Create a Google Cloud OAuth client with the YouTube Data API enabled, for chat moderation.
- Create a Twitch developer app and get your Twitch stream key.
- Get a Groq API key and a Sarvam API key.
- Install a phone streaming app: Moblin (iPhone) or Larix Broadcaster (Android).

---

## 1. Plan with Opus 5.5

Start Claude Code in plan mode with `opusplan`:
```bash
claude --model opusplan --permission-mode plan
```
You can also start Claude Code normally and press Shift+Tab until the mode shows Plan.

Paste **Prompt 1**. Review the plan, ask for changes until you're happy with it, then approve. When you approve, Claude Code switches to Sonnet 5.5, writes the plan files and stops.

### Prompt 1: plan
```
Read CLAUDE.md, STREAM_SARTHI.md and reference/REUSE_MAP.md. You are the lead architect for Stream Sarthi. I need a plan that lets
parallel Sonnet subagents (defined in .claude/agents/) build the full platform described
in CLAUDE.md as fast as possible, reusing reference/privastream (Apache-2.0) and
reference/irl-shield (MIT).

STUDY FIRST. Use Explore subagents in parallel: one per reference repo, plus one for
dependencies and hardware fit (development on an RTX 3050 with 4–6 GB, demo on an
RTX 5050 with 8 GB, one cu128 stack for both, video on the CPU first, audio and chat
through APIs). For each repo, map every
module: what it does, entry points, models and their weights' licences, what runs on the
GPU, VRAM use, dependencies, and known limitations (for example, IRL Shield's output only
works with Twitch). Decide file by file what to reuse as-is, adapt, or rewrite.

THEN PRODUCE THE PLAN WITH THESE SECTIONS:
1. Reuse inventory: a table of source path, target path, reuse/adapt/rewrite, reason,
   and licence.
2. Architecture: processes, threads and queues; how frames, audio and chat flow; the
   delay buffer and A/V sync design; fail-safe; hold; where each model runs.
   Include a CPU budget, a GPU memory budget (3.5 GB or less in the dev profile, 7 GB or
   less in the demo profile) and a latency budget for each stage, including API round
   trips (720p at 30 fps, end-to-end delay of 5 seconds or less).
3. Decisions, each with its reason: the event transport between modules; which APIs to
   use for speech-to-text, spoken PII, chat and captions (Sarvam, Groq), with their
   latency, cost, rate limits and local fallbacks; the dev and demo config profiles; OCR models for Indian scripts; the
   detector framework; and which Ultralytics or InsightFace pieces to keep for now.
4. Contracts: exact schemas and interfaces for Frame, AudioChunk, Detection, MaskRequest,
   MuteSpan, ChatMessage, ChatVerdict, SafetyEvent, RiskSignal and Config, plus the
   detector plugin interface. These are written first.
5. Phases with gates. Phase 0: environment, contracts, eval harness skeleton. Phase 1:
   an end-to-end pipe (ingest → delay buffer → passthrough → YouTube) with one detector
   (a SPECIMEN Aadhaar card blurred live); the gate is a delay under 5 seconds, measured
   live. Then the feature phases. The final phase is hardening: red-team suites, eval
   numbers and a demo script. Every gate must be a runnable script.
6. Task graph: every task has an id, an owner agent, the directories it owns, what it
   depends on, its spec, and its acceptance test. Group tasks into waves that run in
   parallel with no shared files. Aim for 4–6 parallel tasks per wave.
7. Specs, one per module: inputs, outputs, behaviour, edge cases, test cases, latency
   target and VRAM target.
8. The top 10 risks, each with a fallback.
9. What I must provide: keys, accounts, and phone setup.

Don't write code yet. When I approve, your first execution step is to write
docs/PLAN.md, docs/specs/<module>.md for every module, the contracts/ code,
docs/STATUS.md (every task pending), docs/THIRD_PARTY.md and docs/DECISIONS.md. Then
commit, stop, and tell me it's ready.
```

---

## 2. Build with Sonnet 5.5 and parallel subagents

After approving the plan, stay in the same session. It's now running on Sonnet. To cut down on permission prompts, press Shift+Tab to switch to accept-edits mode. Common commands are already allowed in `.claude/settings.json`.

Paste **Prompt 2**.

### Prompt 2: build
```
Build Stream Sarthi by executing docs/PLAN.md. You are the orchestrator.

1. Don't write module code yourself. Send each task to the subagent named as its owner
   in the plan (definitions are in .claude/agents/). Give each subagent its task id, its
   spec file, the contracts it uses, the directories it owns, and its acceptance test.
2. Run every task in a wave at the same time, as background subagents. Never put two
   tasks that touch the same files in the same wave.
3. After each wave:
   a. Run the reviewer subagent on the changes.
   b. Run the full CPU test suite.
   c. Run the phase's gate script. GPU checks go one at a time, under the GPU lock.
   d. Send any failure back to the task's owner, with the failure output.
4. When a wave passes: commit once for the wave, with the message
   "<phase>.<wave>: <summary>". Then update docs/STATUS.md with what was done and the
   eval and latency numbers.
5. Move on to the next wave and phase without asking me. Stop and ask me only when:
   a gate fails twice after fixes; a contract change would affect more than one module;
   a credential is missing; or an action can't be undone.
6. If a subagent reports a blocker outside its own directories, create a follow-up task
   for the right owner. Don't let the subagent edit outside its directories.

Start with Phase 0.
```

---

## 3. Resume in a new session
```bash
claude --model sonnet
```
### Prompt 3: resume
```
Read CLAUDE.md, docs/PLAN.md and docs/STATUS.md, and Prompt 2 in PROMPTS.md. Check the
git log and run the test suite to confirm the real state of the build. Then continue
from the next incomplete wave, following the Prompt 2 rules.
```

---

## 4. When a gate fails twice: re-plan with Opus
In the session, run `/model opusplan` and switch to plan mode with Shift+Tab.
### Prompt 4: re-plan
```
Gate <name> has failed twice. Read docs/STATUS.md, the latest report in eval/reports/
and the failing output. Find the root cause and propose a revised approach for the
affected tasks only, including any contract or budget changes. When I approve, update
docs/PLAN.md, the affected specs and docs/DECISIONS.md, then continue the build using
the Prompt 2 rules.
```

---

## Optional: agent teams instead of subagents
Agent teams let builders message each other about interfaces directly. They're experimental and use more tokens. To try them, add this to `.claude/settings.json`:
```json
"env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" }
```
Then, in Prompt 2, ask for "teammates using the <agent-name> agent type, on Sonnet". Subagents are the default here because the plan already gives each builder separate directories and fixed contracts, so the builders have little need to coordinate with each other.

---

## Agents in this kit (all run on Sonnet 5.5)
| Agent | Owns | Builds |
|---|---|---|
| pipeline-engineer | engine/, relay/ | Ingest, delay buffer, A/V sync, fail-safe, hold, compositor, encoding, YouTube and Twitch outputs |
| vision-engineer | vision/ | Faces with allowlist, Aadhaar and PAN, UPI QR, Indian plates, OCR for Indian scripts, OTP screens, game IDs, tracking |
| audio-engineer | audio/, guard/ | Speech-to-text, spoken PII in Hinglish, mute spans, mouth blur, captions, strike guard |
| chat-engineer | chat/ | YouTube and Twitch chat, abuse, scam and doxxing detection, actions, chat commands |
| product-engineer | app/api/, app/web/ | FastAPI backend, streamer app, moderator console, safety report |
| eval-engineer | eval/, scripts/gate_* | Synthetic SPECIMEN data, accuracy and latency harness, gate scripts, red-team suites |
| reviewer | read-only | Reviews every wave before the commit |
