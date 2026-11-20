# Spec: contracts (shared schemas, interfaces, bus, fakes)

The code in `contracts/` is authoritative. This file describes it. Decisions D-18 to D-30 in `docs/DECISIONS.md` record how it differs from the sketch in `docs/PLAN.md` section 4.

## 1. Purpose and owner
The only way modules talk to each other. It defines every shared type, the detector/STT/classifier/bus/runtime interfaces, the wire format, the `Config` model, and the test doubles that let every module be built and tested in isolation.
Owner: **lead only**. No builder edits `contracts/`. A needed change is reported back with the field, the reason and the affected modules; the lead changes it and logs it in `docs/DECISIONS.md`.

## 2. Layout
```
contracts/
  __init__.py        re-exports the public names; CONTRACT_VERSION = "1.0.0"
  CONTRACT_VERSION   semver text file, same value
  py.typed
  enums.py           Category, Modality, MaskStyle, MaskKind, Lane, Device, ChatAction, HoldSource, Topic,
                     LOCAL_ONLY_TOPICS, NEVER_DROP_TOPICS
  geometry.py        Quad (finite 4-point polygon; bbox, padded, clamped, iou), CANVAS_W/H = 1280x720
  media.py           Frame, AudioChunk  (hot path, numpy arrays typed Any, never on the wire)
  detection.py       Detection, MaskRequest, MuteSpan, LaneResult
  speech.py          Word, Transcript, TranscriptWindow, PiiSpan  (in-process only)
  chat.py            ChatMessage, ChatVerdict, ModQueueItem, ModAction
  events.py          SafetyEvent, RiskSignal, RiskMeter, HoldCommand, SessionCommand, CaptionEvent,
                     StageTiming, ConfigChange, AllowlistUpdate, EnrolledFaceMeta, EngineStatus
  privacy.py         classification of every str field (free text / code / id) + validators
  protocols.py       Detector, DetectorConfig, DetectContext, Resources, AllowlistView, OcrResult, OcrProvider,
                     FaceEnroller, SpeechToText, PiiSpanDetector, ChatClassifier, EventBus
  runtime.py         FrameSource, FrameSink, RelayEndpoints, LaneHost, AudioHost, Builder
  config.py          Config (pydantic-settings), load_config(profile)
  wire.py            to_wire / from_wire / dumps / loads, WIRE_TYPES, TOPIC_TYPES, WIRE_TOPICS, ContractError
  samples.py         one valid sample of every wire type; `python -m contracts.samples <dir>` writes JSON
  fakes/             InProcessBus, FakeFrameSource, FakeDetector, FakeSTT, FakeChatClassifier,
                     FakeAllowlist, FakeResources, FakeEnroller
  tests/
```
`contracts` imports without numpy, torch or onnxruntime (arrays are typed `Any`).

## 3. Conventions
Python 3.11. Data types are `@dataclass(frozen=True, slots=True)` and validate themselves in `__post_init__` (confidence within 0..1, `pts_end >= pts_start`, finite numbers, REGION masks need quads, `allow=True` only for faces, TIMEOUT verdicts need `timeout_seconds`, and so on). `Config` is pydantic v2. Pixel coordinates are on the canonical 1280x720 canvas. Times are seconds on the **source PTS clock** unless a field is named `wall_*`, `completed_at`, `arrival` or `received_at`.

## 4. Topics
Stable strings. `LOCAL_ONLY_TOPICS` never leave the engine process. `NEVER_DROP_TOPICS` (`control.hold`) are never dropped by a bounded subscriber queue.

| Topic | Payload | Notes |
|---|---|---|
| `video.detection` | `Detection` | local only |
| `video.mask` | `MaskRequest` | local only |
| `audio.mute` | `MuteSpan` | local only |
| `audio.transcript` | `Transcript` | local only: raw transcript text never crosses a process |
| `safety.event` | `SafetyEvent` | |
| `guard.risk` / `guard.meter` | `RiskSignal` / `RiskMeter` | |
| `caption.event` | `CaptionEvent` | |
| `chat.allowed` | `ChatMessage` | message released to the streamer overlay |
| `chat.verdict` | `ChatVerdict` | no text |
| `chat.modqueue` | `ModQueueItem` | text included, moderator role only |
| `chat.modaction` | `ModAction` | |
| `control.hold` | `HoldCommand` | never dropped |
| `control.config` | `ConfigChange` | dotted-key diff |
| `control.allowlist` | `AllowlistUpdate` | biometric-derived: local IPC only, redacted in `repr` |
| `control.session` | `SessionCommand` | go live / stop |
| `engine.status` | `EngineStatus` | |
| `telemetry.timing` | `StageTiming` | |

## 5. Wire format (`wire.py`)
- Envelope `{"t": "<ClassName>", "v": 1, "d": {...}}`. Strict decode: unknown type, version, field, enum value or `Literal` value raises `ContractError` (a `ValueError`). Invalid field values surface as `ContractError` too.
- `StrEnum` encodes as its string, `Quad` as `{"pts": [[x,y] x4]}`, tuples as lists (decoded back to tuples), `Mapping` fields as JSON objects.
- Only classes in `WIRE_TYPES` serialise. `Frame`, `AudioChunk`, `LaneResult`, `Transcript`, `Word`, `TranscriptWindow`, `PiiSpan` and `OcrResult` raise `TypeError`.
- Max message size 256 KB (`MAX_WIRE_BYTES`). A 512-float `AllowlistUpdate` is about 5 KB.
- The ZeroMQ bridge (engine) sends `[topic bytes, dumps(event)]` for `WIRE_TOPICS` only and may add a `seq` envelope outside the contract.

## 6. Privacy rules enforced in code
- Every `str` field of a data contract is classified in `privacy.py` as free text, code or id. `test_no_free_text.py` fails on an unclassified field.
- Free text exists only on `ChatMessage` (`text`, `author_display`), `CaptionEvent.text`, `Word.text`, `Transcript.text`, `AllowlistUpdate.label`, `EnrolledFaceMeta.label` and `OcrResult.texts`. These classes redact them in `repr`. Never log, store or put them in `Detection.extra`.
- Codes (`reason`, `detail_code`, `label`, `source`, `lang`, `Detection.extra` strings, ...) must match `^[A-Za-z0-9_:.\-/]{0,64}$`. Ids match `^[A-Za-z0-9_:.\-+]{1,128}$`. Best effort: it blocks sentences, not a lone digit string.
- `Config` secrets are `SecretStr`. Streamer private terms and the risky-text blocklist are excluded from `repr`.

## 7. Runtime wiring (`runtime.py`)
The engine never imports `relay`, `vision`, `audio`, `guard` or `chat`. `engine/main.py` loads each from `Config.runtime.modules` (`"module:build"`) and calls `build(config, bus)`:
- `relay.runtime:build` returns `RelayEndpoints(source: FrameSource, sink: FrameSink)`.
- `vision.host:build` returns a `LaneHost` (`start`, non-blocking `submit(frame)`, `poll() -> list[LaneResult]`, `close`, `active_lanes`).
- `audio.orchestrator:build` returns an `AudioHost` (`start`, non-blocking `feed(AudioChunk)`, `close`).
`LaneResult(ok=True, requests=())` is the "ran and found nothing" heartbeat that the engine's fail-safe depends on.

## 8. Config (`config.py`)
Priority: init kwargs > process env > `.env` > `config/profiles/<profile>.yaml` > defaults. `load_config(profile)` reads `dev` or `demo`; env names are the plain field names (`STREAM_DELAY_SECONDS`, `GROQ_API_KEY`, ...) with `__` for nesting. Validation: `delay_seconds` 3.0-5.0 (`delay_warning()` above 4.3), GPU cap 3500 MB in `dev` and 7000 MB in `demo`, `ort_provider=cuda` needs `gpu.enabled`, local IndicTrans2 captions are `demo` only, and the retro-hold window is clamped by `effective_retro_seconds`. Sections: `detect`, `engine`, `runtime`, `relay`, `vision`, `ocr`, `signboard`, `gpu`, `categories`, `audio`, `hold`, `chat`, `guard`, `captions`, `destinations`, ports and secrets. Defaults match the numbers in the module specs.

## 9. Fakes
All pure Python, CPU only, no network. `InProcessBus` validates payload types per topic, uses bounded drop-oldest queues and never drops `control.hold`. `FakeFrameSource` yields 1280x720 frames with a light stand-in "card" at a known quad (numpy imported lazily). `FakeDetector` is scriptable with latency and failure hooks. `FakeSTT` returns scripted `Transcript`s with or without word times. `FakeResources` has no onnxruntime import. `FakeEnroller` is deterministic and needs at least 5 images.

## 10. Test cases (`contracts/tests/`, CPU)
Wire round trip for every wire type; hot-path types refuse to serialise; strict decode failures; size limit; enum snapshot (values append-only); no-free-text lint (and that it catches a stray field); per-type validation; supporting contracts (`LaneResult`, mod queue, allowlist, config change, risk meter, engine status, speech, OCR); runtime protocols are structural; frozen dataclasses; config defaults, ranges, profile loading, env-over-YAML, secret masking; fakes satisfy their protocols and the bus drops oldest but never a hold.
**Acceptance (Step 0):** `pytest -q contracts` green and `ruff check contracts` clean.

## 11. Change control
1. Only the lead edits `contracts/`. Builders report the needed change.
2. Every change gets a `docs/DECISIONS.md` entry. Enum changes update `test_enum_snapshot.py` in the same commit.
3. Additive change (new optional field, enum member, type) is a minor bump of `CONTRACT_VERSION`. A breaking change (rename, remove, type change, tighter validation) is a major bump with a migration note, and the lead asks the user first if more than one module is affected.
4. `EngineStatus.contract_version` carries the engine's version. The API and chat refuse to start against a different major version.
5. The reviewer agent flags any diff under `contracts/` that has no matching DECISIONS entry.

## 12. Performance and budgets
`to_wire`/`from_wire` of a `Detection` is well under 1 ms. No heavy imports. No VRAM.

## 13. Tasks
Step 0 (lead) delivered the package, fakes and tests. Every other task lists "contracts" as a dependency and imports only from `contracts` and its own package.

## 14. Out of scope
Business logic of any module, serialisation formats other than JSON, schema registries, per-field migration tooling, runtime plugin loading beyond `vision/detectors/<id>` autodiscovery, the `enrol.impl` import path and the `runtime.modules` build paths.
