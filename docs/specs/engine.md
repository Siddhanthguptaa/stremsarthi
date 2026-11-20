# Spec: `engine/`

Status: approved plan, section 7. Contracts: `contracts/` (lead-owned). Budgets: `docs/PLAN.md` section 2.5.

## 1. Purpose and owner

The engine is the always-on process that owns time. It keeps the A/V delay buffer, decides at each frame's deadline whether the frame is safe (`LIVE`), uncertain (`PROTECT`) or held (`HOLD`), paints masks, applies audio mutes, burns captions, and hands finished frames and audio to the encoder. It also hosts the event bus broker. If anything upstream is late, the engine fails toward privacy.

Owner agent: `pipeline-engineer`. Two instances may run in one wave when one owns `engine/` and the other `relay/`.

## 2. Directories owned and file layout

Edit only `engine/` (and `scripts/run_*`). Tests in `engine/tests/`.

| File | Role | Origin |
|---|---|---|
| `engine/deadline.py` | `DeadlineEngine`: ring, coverage, `submit`, `submit_result`, `poll` | adapt `reference/irl-shield/shield/engine.py` |
| `engine/control.py` | `Control`: per-source holds, `HOLD > PROTECT > LIVE`, retro window | reuse `shield/control.py` |
| `engine/motion.py` | global-motion worker and deadline-gated projection | reuse `shield/motion.py` |
| `engine/compositor.py` | paints `MaskRequest`s onto a frame; PROTECT and HOLD output | adapt `shield/render.py` |
| `engine/blur.py` | blur, pixelate, solid, polygon crop | adapt `privastream/.../detection/blur_utils.py` |
| `engine/slate.py` | pre-rendered slates (HOLD, PROTECT, TRANSPORT, FAILURE), fence | adapt `shield/presentation/renderer.py` |
| `engine/session.py` | `Session`: A/V rings, tick loop, emit | adapt `relay/live.py` (`RelaySession`) |
| `engine/audio_mute.py` | sample-accurate mute, fades, bleep | new |
| `engine/mouth.py` | `MOUTH_OF_SPEAKER` resolution | new |
| `engine/captions.py` | caption burn-in (Pillow + raqm) | new (T5.4) |
| `engine/backpressure.py` | `RequestTracker`, stale-drop, `clamp_region` | reuse `privastream/src/web/backend/video_api_utils.py` |
| `engine/bus.py` | `LocalBus` (in-process) and `ZmqBroker` (XSUB/XPUB thread, `ipc://`) | new |
| `engine/timing.py` | `StageTiming` JSONL writer | new |
| `engine/main.py` | assembler: builds modules from dotted paths in config | new |
| `engine/assets/` | fonts: Atkinson Hyperlegible (OFL, keep `OFL.txt`), Noto Sans Devanagari/Kannada/Tamil/Telugu/Bengali (OFL) | copy |

The engine never imports `relay`, `vision`, `audio`, `guard` or `chat`. `main.py` resolves them from `config.runtime.modules` (`"vision": "vision.host:build"`, `"relay": "relay.runtime:build"`, `"audio": "audio.orchestrator:build"`) via `importlib` and the Protocols in `contracts/runtime.py`.

## 3. Inputs

- `FrameSource` (from relay): iterator of `Frame` (1280x720 BGR) and a 48 kHz mono int16 audio iterator tagged with absolute sample index.
- From `LaneHost` (vision): `LaneResult` per lane run (see 4). From `AudioHost`: `MuteSpan`, `MaskRequest(kind=MOUTH_OF_SPEAKER)`, `CaptionEvent`.
- Bus: `HoldCommand`, `ConfigChange`.
- `Config`: see section 9.

**Added to `contracts/detection.py` (D-25):** `LaneResult(lane: Lane, frame_seq: int, pts: float, completed_at: float, ok: bool, requests: tuple[MaskRequest, ...], aux_detections: tuple[Detection, ...])`. An empty `requests` with `ok=True` is a valid "ran and found nothing" heartbeat; the fail-safe depends on it. `aux_detections` carries allowlisted faces (needed for mouth quads) and nothing else.

## 4. Outputs

- To `FrameSink` (relay encoder): composited `Frame`-sized BGR plus 1600 samples of processed audio per frame, with PTS.
- Bus (`SafetyEvent`): `masked` (one per track start; duration updated at end, never per frame), `muted`, `held`, `failsafe`. `StageTiming` via `timing.py`.
- Metrics: `emit_lag = now - deadline` per frame; counters of LIVE/PROTECT/HOLD frames.

## 5. Behaviour

### 5.1 Clock and ring
1. `origin = first_frame.arrival - first_frame.pts`. Deadline of frame i = `origin + pts_i + D`. `D = config.delay_seconds` (default 4.0).
2. Frame ring size `ceil(fps * (D + 1)) + 8` (about 160 frames, about 440 MB). Evict oldest. RAM only; nothing touches disk.
3. Audio ring holds `(D + 3) * 48000` samples indexed by absolute sample number from the same PTS (`sample = round(pts * 48000)`). Missing samples read as silence.
4. A frame arriving after its deadline is discarded. A gap in input is filled with the last frame's pixels but forced `PROTECT` (reason `ingest-gap`). The engine starts in `PROTECT`.

### 5.2 Coverage and fail-safe (per frame at its deadline)
A frame is `LIVE` only if, for every active lane L (set from the enabled detectors' lanes):
- there is a `LaneResult` with `ok=True`, `completed_at <= deadline`, and `frame_seq` in the window `[i - 6, i + 30]` (`engine.window.before/after`);
- its age `|i - frame_seq|` is within the lane max age (FAST 6 frames, ACCURATE 12, SLOW 60);
- the motion path from `frame_seq` to `i` exists with every step finished before the deadline (`motion.py`).
Otherwise `Control` sets `PROTECT` with a reason code (`missing-fast`, `missing-accurate`, `unresolved-motion-fast`, ...). A lane that raised, or whose detector reported `ok=False`, counts as missing. Per-category `mask_when_unsure` is applied by vision; the engine only knows lanes.

The engine selects masks for frame i from the newest valid result per lane, plus any request whose `[pts_start, pts_end]` covers `pts_i`. Request quads are projected from `frame_pts` to `pts_i` through the motion path.

### 5.3 Output states
- `LIVE`: paint requests, pass audio (mutes applied).
- `PROTECT`: for the first 1.0 s fill with the frame's mean colour (1x1 resize up), then a slate "Checking privacy coverage" (English and Hindi). Audio passes with mutes applied.
- `HOLD`: slate immediately; audio fully silent.
- Fence: after any non-LIVE frame, output stays slated until a frame newer than the capture high-water mark at the time of the state change arrives (stops stale buffered frames leaking when hold is released).
- Hold is re-checked after `poll`, in the egress writer, and at the encoder hand-off.

### 5.4 Hold and retroactive hold
- `Control` keeps `dict[HoldSource, bool]`; releasing one source never clears another. Sources: `hotkey, api, chat, app, auto_transport, auto_egress`.
- On `HoldCommand(active=True)` from `hotkey|api|app|chat` at wall time `t`, every frame still in the ring with `arrival >= t - retro` is marked held, and the matching audio PTS ranges are zeroed. `retro = min(config.hold.retro_seconds, D - 0.5)` (default 3.0). Auto sources do not retro.
- Effective latency from command to slate: at most one frame (33 ms) for unemitted frames. Emit `SafetyEvent(category=HOLD, action="held", duration_ms=...)` on release.

### 5.5 Painting (`compositor.py`, `blur.py`)
- Styles: `BLUR` (Gaussian, kernel `odd(max(15, 0.25 * min(w, h)))`, at least 51 for card/QR categories), `PIXELATE` (block `max(8, min(w, h) // 8)`), `SOLID` (RGB 32,32,32).
- Polygon mask via `fillConvexPoly` on a ROI crop only (never full-frame per quad), 3 px feather.
- `FULL_FRAME` requests, or total masked area above 60 % of the frame, collapse to one solid fill (cheaper and safer).
- Budget: at most 30 ms per frame for up to 40 quads.

### 5.6 Audio mute (`audio_mute.py`)
- Zero samples in `[round(start*48000), round(end*48000))` for each `MuteSpan` with a 10 ms linear fade-out/fade-in applied **inside** the span edges (spans are already padded, so no leak). `mode="bleep"` writes a 1 kHz sine at -18 dBFS.
- A span is accepted until `span_start_arrival + D - 0.1`; later spans are applied if the samples have not been emitted yet, otherwise dropped and logged as `late-span`.

### 5.7 Mouth blur (`mouth.py`)
For each `MaskRequest(kind=MOUTH_OF_SPEAKER)` window, take `aux_detections` with `allow=True` and `aux_quads["mouth"]` from the newest face result, project to the frame, and paint BLUR. If no face is detected, do nothing (all non-allowlisted faces are masked by vision already).

### 5.8 Captions (`captions.py`, T5.4)
Pillow with libraqm (`ImageFont.Layout.RAQM`) for Indic shaping; `CaptionEvent` text is drawn on the delayed timeline (show when `pts_i` in `[pts_start, pts_end]`), bottom third, semi-opaque box, 2 lines of at most 42 chars. If raqm is absent, fall back to basic layout and log once. Toggle via `captions.enabled`. The engine renders text exactly as given; **redaction of PII tokens is the audio module's job** (see `audio.md`).

### 5.9 Bus (`bus.py`)
`LocalBus` for the hot path (typed, bounded queues, drop-oldest with a counter). `ZmqBroker` runs an XSUB/XPUB proxy on `ipc:///tmp/streamsarthi-bus`; every non-`local_only` topic is bridged as JSON via contracts' `to_wire`. Topics marked `local_only` (for example the transcript stream) are never bridged.

## 6. Reuse

| Source | What to change |
|---|---|
| `reference/irl-shield/shield/engine.py` | Remove the fixed `{'fast','accurate'}` lane pair; lanes come from config with max ages. Add audio ring, mute spans, retro hold. Keep the deadline rule and `poll` max 4 frames |
| `shield/control.py` | Use as is; add `HoldSource` enum values and retro bookkeeping outside the class |
| `shield/motion.py` | Use as is; add a hook for per-track local projection later |
| `shield/render.py` | Keep quad rasteriser and PROTECT fill; add `BLUR`/`PIXELATE` |
| `shield/presentation/renderer.py`, `tally.py` | Keep slate cache and fence. New Hindi/English copy. Keep OFL font file next to it |
| `relay/live.py` | Keep PCM ring tagged by sample index and the HOLD re-check; add mute application |
| `privastream/.../blur_utils.py` | Crop polygon mask to ROI; use `detection/blur_utils.py`, not `utils/blur_utils.py` |
| `privastream/src/web/backend/video_api_utils.py` | Reuse `RequestTracker` and stale-drop as the bus back-pressure policy |
Never use `shield/app/appearance.py` (reads `engine.control._holds`); expose a public `Control.snapshot()`. Record every copied file in `docs/THIRD_PARTY.md`.

## 7. Edge cases

Input gap; late frame; fps change (30 assumed but not hard-coded: derive from PTS); non-16:9 input (letterboxed by relay); a lane that crashes (mark stale so PROTECT); clock jump or PTS reset (restart session, PROTECT); two clients pressing hold simultaneously; hold released while queued frames exist (fence); mute span for samples already emitted; very long run in PROTECT (slate, not mean colour); audio shorter than video (silence fill); encoder process death (transport HOLD, restart with backoff); zero lanes enabled (engine refuses LIVE and stays PROTECT, to prevent a silent unprotected pass-through).

## 8. Test cases (CPU only, synthetic frames from `contracts/fakes`)

1. Ported `reference/irl-shield/tests/test_deadline_engine.py`, `test_control.py`, `test_motion*.py`, `test_render.py` pass against the new API.
2. A result completing after the deadline leaves the frame `PROTECT` (reason `missing-*`).
3. An `ok=True` empty `LaneResult` keeps frames `LIVE`; no result for a lane makes them `PROTECT`.
4. Frame arriving after its deadline is discarded and counted.
5. Ingest gap frame is `PROTECT` with reason `ingest-gap`; engine starts in `PROTECT`.
6. Two holds (`hotkey`, `chat`), release one: still HOLD; release both: slate until the fence frame, then LIVE.
7. Retro hold at `t` with `retro=3.0` marks ring frames with `arrival >= t-3.0` and zeroes the same audio range; `retro` is clamped to `D-0.5`; auto sources do not retro.
8. Mute span: samples in range are 0, fade lengths 10 ms inside the span, samples outside unchanged (bit-exact); bleep mode has a 1 kHz peak at -18 dBFS.
9. A/V sync: a synthetic stream with a beep and an optical stamp at the same PTS emerges with offset under 20 ms.
10. Painting: a SPECIMEN card quad with `BLUR` makes the OCR of the output crop unreadable (no 12-digit read); outside the quad, pixels are bit-identical.
11. Masked area above 60 % collapses to a solid fill.
12. Mouth resolution paints only the `mouth` quad of an `allow=True` face during the request window.
13. Bus: `local_only` topic never appears on the ZeroMQ subscriber; `LocalBus` drops oldest and counts drops.
14. Zero enabled lanes: output stays `PROTECT`.
15. **Acceptance T1.1:** `FakeFrameSource` (SPECIMEN card) plus `FakeDetector` through `Session` to a null sink: card quad masked in every card-visible frame after warm-up, timing JSONL written, no frame emitted before its deadline.
16. **Acceptance T3.4:** mute applied sample-accurately for a `MuteSpan` arriving `D-0.2` s before its start.
17. **Acceptance T5.4:** captions in Devanagari, Kannada, Tamil, Telugu, Bengali render with shaping (raqm test: glyph run differs from non-shaped fallback for a conjunct string).
18. Gate G1/G2/G3 as in `docs/PLAN.md` section 5.

## 9. Config keys used

`delay_seconds` (3.0-5.0), `fps`, `canvas`, `hold.retro_seconds`, `hold.slate_text_hi/en`, `categories.<cat>.style|padding_px|enabled`, `detect.*` (read only), `audio.failsafe_mute_voiced`, `audio.mute_mode` (`silence|bleep`), `captions.enabled`. Added to `Config` (D-25): `engine.window.before` (6), `engine.window.after` (30), `engine.lane_max_age.{fast:6, accurate:12, slow:60}`, `engine.protect_fill_s` (1.0), `engine.max_mask_area_ratio` (0.6), `runtime.modules.*`, `engine.timing_log_path`.

## 10. Latency target

Compositor at most 30 ms per frame; poll tick at most 5 ms; engine delay (capture to egress socket) p95 at most 5.0 s at D=4.0 (about 4.5-4.8 s). Hold to slate at most 1 frame.

## 11. VRAM and CPU target

VRAM: none (CPU compositor). CPU: compositor about 0.3 core, Python control about 1 core (shared); whole-machine budget in plan section 2.5. RAM: about 440 MB ring.

## 12. Tasks

| Task | Wave | Depends | Scope |
|---|---|---|---|
| T0.4 | 0.B | contracts | deadline engine port, `control.py`, fail-safe, ported tests |
| T1.1 | 1 | T0.3, T0.4, T0.5 | compositor, motion projection, retro hold, bus wiring, timing logs, `main.py` |
| T2.2 | 2.A | G1 | N lanes with max ages, per-category style, per-category fail-safe |
| T3.4 | 3 | T2.2 | `audio_mute.py`, `mouth.py`, retro audio, audio fail-safe |
| T5.4 | 5 | T3.4 | `captions.py` |
| T7.3 | 7.A | G6 | profiling, soak and regression benchmarks (shared with relay) |

## 13. Out of scope

Detectors and tracking (vision), STT and PII (audio), encoding and ingest (relay), archive or seal, skins and chat votes, any disk write of frames or audio, WebRTC.
