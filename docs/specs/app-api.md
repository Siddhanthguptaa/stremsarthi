# Spec: app/api (FastAPI backend)

## 1. Purpose and owner
The control plane between the browser/phone UI and the rest of the system. Serves REST + WebSocket, relays bus events to clients, applies config changes, owns face enrolment and the allowlist store, manages the stream session lifecycle, and builds the safety report. It is **not** on the frame path.
Owner: **product-engineer**. Process P5. If it dies, the engine keeps streaming and hold still works from the hotkey helper and chat.

## 2. Directories owned and file layout
`app/api/` and its tests only.
```
app/api/
  main.py            # create_app(), uvicorn entry (python -m app.api.main), lifespan hooks
  settings.py        # ApiSettings (pydantic-settings): host, port, allowed_hosts, allowed_origins, secret key
  security.py        # Host/Origin middleware, security headers, CSP, WS origin check
  auth.py            # REUSE reference/privastream/src/web/backend/auth.py (HMAC tokens)
  deps.py            # FastAPI dependencies: current_principal(), require_role()
  bus.py             # BusClient (ZeroMQ SUB/PUSH) + InProcessBus swap for tests
  ws.py              # /api/ws hub: auth frame, subscribe, ACL, per-client queues
  sessions.py        # SessionManager (adapt privastream app.py RoomManager), state machine, engine subprocess
  store/
    events.py        # SafetyEvent ring (5,000) + per-session events.jsonl writer
    allowlist.py     # AllowlistStore (RAM; optional encrypted file)
  routes/
    health.py  session.py  control.py  config.py  enrol.py  events.py
    mod.py  report.py  pairing.py  auth_routes.py
  report.py          # build_report(session_id) -> dict (schema in section 5.9)
  pairing.py         # adapt reference/irl-shield/shield/dashboard/pairing.py
  tests/
```

## 3. Inputs
- HTTP/WebSocket from the web app, the Windows hotkey helper and (optionally) a phone browser.
- Bus topics subscribed (see `contracts.md`): `safety.event`, `guard.risk`, `chat.allowed`, `chat.verdict`, `chat.modqueue`, `caption.event`, `control.hold`, `engine.status`.
- `.env`: `SECRET_KEY` (required), `REQUIRE_AUTH` (default **true** here; PrivaStream defaulted to false), stream keys (read by the engine, **never returned by any endpoint**; the API only reports `configured: true/false`).

## 4. Outputs
- Bus publishes: `HoldCommand` (`control.hold`), `ConfigChange` (`control.config`), `AllowlistUpdate` (`control.allowlist`), `ModAction` (`chat.modaction`), session control (`control.session`).
- REST/WS responses as below. Files: `~/.streamsarthi/sessions/<session_id>/events.jsonl` (SafetyEvents only), optional `~/.streamsarthi/allowlist.enc`.

## 5. Behaviour

### 5.1 Auth (reuse `auth.py`)
- `issue_room_token(room_id, role, ttl_seconds)` and `verify_room_token(token, room_id)` are used unchanged; `room_id` = `session_id`; roles: `streamer`, `moderator`, `hotkey` (hold/unhold only), `viewer-readonly` (report only).
- Local single-user flow (IRL Shield pattern): on startup the API prints `http://127.0.0.1:<web_port>/#token=<streamer token>` to the console only (never logged to files). The web app reads the fragment, strips it from the URL, keeps the token in memory + `sessionStorage`. TTL 6 h.
- `POST /api/auth/mod-invite` (streamer) → moderator token (TTL 6 h, one session). `POST /api/auth/hotkey-token` (streamer) → `hotkey` role token (TTL 24 h) written by the Windows helper.
- Header `Authorization: Bearer <token>` on REST; WebSocket authenticates with a first frame (see 5.3), never a URL query.
- Token and secret never appear in logs. Uvicorn access log off.

### 5.2 REST endpoints (all JSON; errors `{ "error": code, "detail": str }`)
| Method + path | Role | Request | Response |
|---|---|---|---|
| `GET /api/health` | none | – | `{status:"ok", bus:bool, engine: EngineStatus\|null, version}` |
| `GET /api/session` | streamer | – | `{session_id\|null, state, started_at, delay_seconds, profile, hold:{active,sources[]}, ingest:{srt_url, rtmp_url}}` |
| `POST /api/session/start` | streamer | `{destinations:[{kind:"youtube"\|"twitch", enabled}], delay_seconds?:3.0-5.0, profile?}` | `{session_id, state:"starting", checks:{youtube_key:bool, twitch_key:bool, groq_key:bool, sarvam_key:bool, gpu:bool}}`; 422 if no destination enabled or required key missing |
| `POST /api/session/stop` | streamer | – | `{state:"stopping"}`; triggers report build |
| `DELETE /api/session/{id}/data` | streamer | – | `{deleted:true}` removes `events.jsonl` |
| `GET /api/hold` | streamer, moderator, hotkey | – | `{active, sources:[HoldSource]}` |
| `POST /api/hold` | streamer, moderator, hotkey | `{active:bool}` | `{active, sources}`; publishes `HoldCommand(source=API\|APP\|HOTKEY, active, actor=<token role hash>)`; **never blocked by rate limits, validation or session state** (works even when idle) |
| `GET /api/config` | streamer | – | `Config` with all `SecretStr` as `"***"` |
| `PATCH /api/config` | streamer | partial `Config` (JSON merge) | new `Config`; publishes `ConfigChange`; live-changeable: `categories.*`, `captions.*`, `audio.languages`, `hold.*`, `chat.*`; `delay_seconds`, `gpu.*`, `detect.*` while live → 409 `requires_restart` |
| `POST /api/enrol/preview` | streamer | multipart one JPEG ≤ 500 KB | `{faces:int, quality:"ok"\|"too_small"\|"blurry"\|"multiple"}` (no image returned) |
| `POST /api/enrol` | streamer | multipart `label` + 5–30 JPEG frames | `{face_id, label, created_at, expires_at\|null}` |
| `GET /api/enrol` | streamer | – | `[{face_id, label, created_at, expires_at}]` (no embeddings) |
| `DELETE /api/enrol/{face_id}` | streamer | – | `{deleted:true}`; publishes `AllowlistUpdate(op=remove)` |
| `GET /api/events?since=&limit=200&modality=` | streamer, moderator (chat only) | – | `[SafetyEvent wire]` |
| `GET /api/risk` | streamer | – | `{level:"ok"\|"watch"\|"warn"\|"danger", score, signals:[RiskSignal wire]}` |
| `GET /api/mod/queue` | moderator | – | `[ModQueueItem wire]` (text included; moderators only) |
| `POST /api/mod/action` | moderator | `{message_id, platform, action:"approve"\|"delete"\|"timeout"\|"dismiss", timeout_seconds?}` | `{accepted:true}`; publishes `ModAction` |
| `GET /api/pairing` | streamer | – | `{rtmp:{url,key?}, srt:{url}, moblin_url}` (stream key never included; ingest credentials are per-session random) |
| `GET /api/pairing/{rtmp\|srt}.png` | streamer | – | PNG QR of the Moblin import URL |
| `GET /api/report/{session_id}` | streamer, viewer-readonly | – | report JSON (5.9) |
| `GET /api/report/{session_id}/export?format=json\|html` | same | – | file download; HTML has a strict CSP and no scripts |

### 5.3 WebSocket `/api/ws`
- Client sends within 3 s: `{"type":"auth","token":"…"}`; else close code 4401. Then `{"type":"subscribe","topics":[…]}`.
- Server frames: `{"topic":str,"seq":int,"ts":iso8601,"data":<wire object>}`; `{"type":"ping"}` every 15 s (client replies `pong`, 2 misses → close).
- **Topic ACL by role:**
  - `streamer`: `safety.event`, `guard.risk`, `chat.allowed`, `chat.verdict` **with text removed** (category/action only, plus `hidden_count`), `caption.event`, `engine.status`, `control.hold`. **Never** `chat.modqueue` (the streamer must not see abuse text).
  - `moderator`: `chat.modqueue`, `chat.verdict`, `safety.event` (modality chat/system), `control.hold`, `engine.status`.
  - `hotkey`: none (REST hold only).
- Per-client bounded queue of 500. Telemetry topics drop-oldest; `control.hold` and `engine.status` are never dropped; a client that falls 5 s behind is closed with 4408.
- Server→engine control is REST-only; WebSocket is read-only apart from auth/subscribe/pong.

### 5.4 Security middleware (`security.py`)
- **Host** header must be in `allowed_hosts`: `127.0.0.1`, `localhost`, `[::1]`, plus configured LAN/Tailscale hosts. Else 421.
- **Origin** (REST with a body and WebSocket upgrade) must exactly match an entry in `allowed_origins` (default `http://127.0.0.1:3000`, `http://localhost:3000`). Else 403. No wildcard CORS; CORS allows only those origins and the `Authorization` header.
- Bind `127.0.0.1` by default. `--lan` binds a configured private IP and requires a token on every call. Never `0.0.0.0`.
- Headers on every response: `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`.
- CSP (HTML export and any HTML the API serves): `default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`. The report HTML embeds no script.
- Body limits: 2 MB general, 12 MB for `/api/enrol` (30 JPEGs). Concurrency guard on `/api/enrol` (max 2 in flight, 5/min per principal; adapt `RequestTracker` from `reference/privastream/src/web/backend/video_api_utils.py:38-70`).

### 5.5 Enrolment flow
1. UI posts 5–30 JPEG frames (browser downscales to ≤ 640 px width) + `label`.
2. The API loads the `FaceEnroller` implementation named by `enrol.impl` (default `vision.detectors.faces.enroll:Enroller`, a contract Protocol, see `contracts.md`) and calls `enrol(images, label)`. It runs YuNet → keeps the **largest face per frame** → SFace embedding → **average → L2-normalise** (PrivaStream algorithm, `video_filter_api.py:860-975`, but with the same model for enrolment and matching). Frames with 0 faces are skipped; < 5 usable frames → 422 `too_few_faces`.
3. Images are processed in memory and discarded. No image, crop or thumbnail is stored anywhere.
4. `AllowlistStore.add(face_id, label, embedding)`; the API publishes `AllowlistUpdate(op=add, face_id, label, embedding[list[float]])` on `control.allowlist` (local IPC only). The engine republishes `engine.status` with `allowlist_version`; the API re-sends a full snapshot when the engine reports an older version (engine restart).
5. **Storage decision:** default is **RAM only with TTL 12 h** (`enrol.ttl_s=43200`, adapt `set_embedding_expiry` / `cleanup_expired_embeddings`, `video_api_utils.py:193-212`). Optional `enrol.persist=true` stores embeddings in `~/.streamsarthi/allowlist.enc`, AES-256-GCM via `cryptography`, key from the OS keyring or `ALLOWLIST_KEY`; file mode 0600. Reason: embeddings are biometric-derived data (DPDP Act); persisting is opt-in and encrypted. Deleting a face or `DELETE /api/session/{id}/data` also deletes persisted records.
6. Max 8 enrolled faces.

### 5.6 Session lifecycle (`sessions.py`)
States: `idle → starting → waiting_ingest → live → stopping → ended`, plus `engine_lost` (5 missed `engine.status` heartbeats at 1 Hz; UI alert; hold remains available) and a `holding` flag derived from `control.hold`.
- `start`: validate keys/destinations, generate per-session random ingest credentials, spawn `scripts/run_engine` as an asyncio subprocess with the profile and a session id, wait for `engine.status.state=="ready"` (timeout 30 s → `failed`).
- `live` when ingest is connected and egress reports connected.
- `stop`: SIGTERM, wait up to 10 s for graceful flush, then kill; build the report; keep it in RAM for the session TTL (6 h; adapt `RoomManager` in `reference/privastream/src/web/backend/app.py:48-150`, `secrets.token_urlsafe(9)` ids).
- Only one active session.

### 5.7 Event store (`store/events.py`)
In-RAM ring of 5,000 `SafetyEvent`s; each also appended to `events.jsonl` for the session (fields exactly as the contract: category, timestamp, confidence, bbox; never image/text). `StageTiming` is not stored here.

### 5.8 Config handling
`PATCH /api/config` validates through the `Config` pydantic model (contracts), rejects unknown keys, diffs, publishes only the diff. A rejected patch never half-applies.

### 5.9 Safety report schema (`report.py`, `schema_version: 1`)
```
{ schema_version:1, session_id, started_at, ended_at, duration_s, profile, delay_seconds,
  totals:{ by_category:{<cat>:{count, mean_confidence}}, by_action:{<action>:int}, by_modality:{<m>:int} },
  timeline:[{event_id, wall_ts, pts, modality, category, action, confidence, bbox|null, duration_ms|null, source, detail_code}],
  holds:[{start_ts, end_ts, sources:[HoldSource]}],
  failsafe:{protect_seconds, audio_failsafe_seconds},
  latency:{delay_p50_s, delay_p95_s, fps_mean},
  risk:{max_level, warnings:[{ts, source, label, score}]},
  chat:{by_action:{…}, by_category:{…}, platform_actions:{done, failed, unsupported}},
  notes:[str] }
```
No images, no free text except fixed `detail_code`/`label` codes. A schema test (case 12) rejects base64 blobs, strings > 64 chars in non-note fields, or anything matching PII regexes.

## 6. Reuse
| Source | Target | Change |
|---|---|---|
| `reference/privastream/src/web/backend/auth.py` | `app/api/auth.py` | Keep functions; default `REQUIRE_AUTH` true; add `hotkey`/`viewer-readonly` roles in a thin wrapper, do not edit the signing code |
| `reference/privastream/src/web/backend/app.py` `RoomManager` (48-150) | `app/api/sessions.py` | Rename to SessionManager, add the state machine, single active session |
| `reference/privastream/src/web/backend/video_api_utils.py` (`RequestTracker`, `cleanup_expired_embeddings`, `set_embedding_expiry`) | `app/api/routes/enrol.py`, `store/allowlist.py` | Concurrency guard and TTL |
| `reference/privastream/src/web/backend/video_filter_api.py:860-975` | algorithm only, implemented in `vision/` | Same model for enrol and match (SFace) |
| `reference/irl-shield/shield/dashboard/server.py` | `app/api/security.py` | Bearer token, Host/Origin exact match, CSP, no access log (FastAPI middleware) |
| `reference/irl-shield/shield/dashboard/pairing.py`, `snapshot.py` | `app/api/pairing.py`, route snapshots | `moblin://` import URL + `qrcode`; allowlisted-fields pattern; drop the Tailscale-only bind rule |

## 7. Edge cases
Engine not running (hold still publishes and returns 202 with `engine:false`); two clients press hold (idempotent, sources tracked per source); hold released by a different source than it was set by (only clears its own source); token expired mid-WebSocket (close 4401, client re-auths); WebSocket reconnect (client resubscribes, server replays last `engine.status` and current hold state); camera denied (UI concern); enrol with two faces in a frame (largest only, warn `multiple`); zero-byte upload; engine restart (allowlist resync); report requested while live (partial, `ended_at=null`); disk full on events.jsonl (drop file writes, keep RAM, emit warning); clock change.

## 8. Config keys used
`api.{host="127.0.0.1", port=8000, allowed_hosts, allowed_origins, lan_host=null}`, `enrol.{impl, ttl_s=43200, persist=false, max_faces=8, min_frames=5}`, `session.{ttl_s=21600, engine_cmd, start_timeout_s=30, stop_timeout_s=10}`, `captions.*`, `categories.*` (patched), `destinations` (kinds/enabled only; keys stay in `.env`).

## 9. Test cases (CPU; `fastapi.testclient`, `InProcessBus`, fake enroller)
1. Token round trip: valid, tampered signature, expired, wrong `room_id` → 200/401/401/401.
2. `REQUIRE_AUTH` default true: unauthenticated `GET /api/config` → 401; `GET /api/health` → 200.
3. Host/Origin: bad Host → 421; bad Origin on POST → 403; WebSocket upgrade with bad Origin → rejected; allowed origin passes.
4. `POST /api/hold {active:true}` publishes exactly one `HoldCommand` and returns in ≤ 20 ms; works with `idle` session and with the engine missing; `hotkey` token can hold but gets 403 on `/api/config`.
5. Config: patch with unknown key → 422 and no event; patch `categories.faces.enabled=false` publishes a diff; `delay_seconds` while live → 409; secrets masked as `***` in `GET`.
6. Enrol: 10 synthetic frames (fake enroller returns fixed vector) → one `AllowlistUpdate`, response has no embedding; 3 frames → 422; 9th face → 422; delete publishes remove; TTL expiry removes the face; persisted file is not readable without the key and has mode 0600.
7. WebSocket: no auth frame in 3 s → 4401; streamer subscribing to `chat.modqueue` is refused; moderator receives it; `chat.verdict` to a streamer contains no `text`.
8. Backpressure: slow client with 1,000 telemetry events → oldest dropped, `control.hold` never dropped, client closed after 5 s lag.
9. Session state machine: valid transitions only; 5 missed heartbeats → `engine_lost`; stop builds a report.
10. Start validation: no destination → 422; missing key → 422 with the key names only, never values.
11. Pairing: QR PNG decodes to the Moblin URL; the URL contains no stream key.
12. Report schema: build from 500 synthetic events; validates; injecting a 200-char string or a base64 PNG into a field fails the schema test; HTML export has the CSP header and no `<script>`.
13. Log scan: run the whole suite with log capture; assert no token, secret or stream key string appears.
**Acceptance:** T0.7 health + WS echo; T1.6 hold/status/config + minimal page; T2.5 toggles/enrol/events store; T3.5 alerts feed; T4.6 mod endpoints; T6.1 go-live orchestration + report. Gate 6 runs the Playwright flows against the real API with a `FakeEngine` publisher.

## 10. Latency target
`POST /api/hold` to bus publish ≤ 20 ms; bus event to WebSocket client ≤ 100 ms p95; REST p95 ≤ 100 ms (enrol excluded: ≤ 3 s for 30 frames on CPU).

## 11. VRAM / CPU target
VRAM 0. CPU ≤ 0.3 core idle-to-live (enrol bursts ≤ 1 core for 3 s). RAM ≤ 300 MB.

## 12. Tasks
**T0.7** (skeleton: app factory, health, bus client, WS echo; deps contracts) → **T1.6** (hold/status/config, minimal page) → **T2.5** (config patch, enrol, event store) → **T3.5** (alerts feed backend) → **T4.6** (mod queue endpoints, chat ACL) → **T6.1** (go-live orchestration, report, risk stream) → **T6.5** (e2e support from eval).

## 13. Out of scope
Multi-user accounts, cloud hosting, WebRTC, storing any video/audio/image, payment/billing, a database server (files and RAM only), serving the Next.js app (it runs on its own port).
