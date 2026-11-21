# app/api: Stream Sarthi control plane (FastAPI)

Not on the frame path. Serves REST and a read-only WebSocket, relays bus events to the browser.
Imports only `contracts`. Spec: `docs/specs/app-api.md`.

## Run
    SECRET_KEY=<random> python -m app.api.main [--lan <private-ip>] [--port N]
Prints `http://127.0.0.1:3000/#token=<streamer token>` to the console only. Binds 127.0.0.1 (never
0.0.0.0); `--lan` needs a private IP and forces tokens. Uvicorn access log is off.

## Settings (env or `.env`, read through `settings.py`)
`SECRET_KEY` (required), `REQUIRE_AUTH` (default true), `API_HOST`, `API_PORT`, `WEB_PORT`,
`API_ALLOWED_HOSTS`, `API_ALLOWED_ORIGINS` (comma lists), `API_LAN_HOST`, `BUS_BACKEND` (`zmq`|`inprocess`),
`BUS_SUB_ENDPOINT` (default `ipc:///tmp/streamsarthi-bus`, the broker's XPUB side),
`BUS_PUB_ENDPOINT` (default `ipc:///tmp/streamsarthi-bus-in`, the broker's XSUB side).

## Files
- `main.py` app factory + entry; `settings.py`; `security.py` (Host/Origin, headers, CSP, body limits)
- `auth.py` HMAC tokens (adapted from PrivaStream, Apache-2.0, `LICENSE.privastream`), roles
- `deps.py` principal and role checks; `errors.py` `{error, detail}` bodies
- `bus.py` `BusClient`: `ZmqBusClient` (pyzmq SUB + PUB) and `InProcessBusClient` (tests)
- `ws.py` `/api/ws` hub: auth frame, subscribe, topic ACL by role, bounded queues, ping, replay
- `routes/health.py`, `routes/auth_routes.py` (whoami, mod-invite, hotkey-token)
- `routes/control.py` hold (`POST|GET /api/hold`) and `GET /api/status`; `routes/config.py` (`GET|PATCH /api/config`,
  `config_store.py`: public view, validated atomic patches); `routes/session.py` + `sessions.py` (start/stop, state view)
- `ratelimit.py` sliding window for hold *releases* (activations are never limited)

## Tests (CPU only)
    scripts/ss.sh python -m pytest app/api -q
    scripts/ss.sh ruff check app/api && scripts/ss.sh ruff format --check app/api
Tests use FastAPI `TestClient` and `InProcessBus`; `test_api_bus.py` also runs a real ZeroMQ round trip over
a temporary `ipc://` XSUB/XPUB proxy; `test_api_zmq_hold.py` drives a hold click through the real ZeroMQ client. The TestClient WebSocket defaults `Host` to `testserver`: tests pass
`host: 127.0.0.1:8000` explicitly.

## Hold sources
`{active, source?:"app"|"api", release_all?}`. Hotkey tokens always map to `hotkey` (token room is fixed so the 24 h
token file survives an API restart); the web app sends `app`; others are `api`. A release clears only its own source;
`release_all` (streamer only) releases every user source. 202 means published but no engine is confirming.

## Enrolment, allowlist and event store (T2.5)
- `POST /api/enrol` (multipart `label` + `frames` parts, or JSON `{label, frames:[base64 JPEG]}`; 5 to 12 frames, at least
  `Config.enrol.min_frames`) -> `201 {face_id, label, created_at, expires_at}`; `GET /api/enrol` -> `{faces, max_faces, ttl_s,
  persisted, min_frames, max_frames}`; `DELETE /api/enrol/{face_id}`. Streamer only. 5 requests/min per caller, 2 in flight.
- The `FaceEnroller` comes from `Config.enrol.impl` (loaded with importlib per request; missing -> 503 `enroller_unavailable`).
  Frames are parsed from the body into memory, decoded to BGR arrays in a worker thread and dropped after the request.
- `allowlist.py` `AllowlistStore`: RAM only, TTL `enrol.ttl_s`, cap `enrol.max_faces`. Opt-in `enrol.persist` writes one Fernet
  token to `ALLOWLIST_PATH` (default `~/.streamsarthi/allowlist.enc`, mode 0600) and needs `ALLOWLIST_KEY` from the environment.
- `enrol_service.py` publishes `AllowlistUpdate` on `control.allowlist` and sends `snapshot` + `add`s when
  `engine.status.allowlist_version` differs from the store's version.
- `events.py` `SafetyEventStore`: ring of 5,000 keyed by `event_id` (upsert); `GET /api/events` (filters `modality`, `category`,
  `since`, `until`, `session_id`; `cursor`+`limit` paging or `order=desc`) and `GET /api/events/summary`. Streamer: all;
  moderator: chat and system only.

## Moderator console and chat overlay (T4.6)
- `chat_state.py` `ChatState` is the API's own copy of chat state, built from `chat.verdict`, `chat.modqueue` and `chat.allowed`
  on the bus (the chat process owns the real queue). `hidden_count`: a message is hidden while its latest verdict is not ALLOW.
  Pending queue items (text, RAM only, cap `chat.modqueue.max_items`, TTL `ttl_s`, swept every 30 s) lose their text when
  resolved; a text-free stub stays. A ring of 50 already-allowed messages serves a refreshed overlay.
- WebSocket: a client subscribing to a chat topic first gets `{"type":"replay","topic","count","hidden_count"}` then `count`
  frames of current state (moderator: pending queue; streamer: recent allowed). `chat.verdict` frames carry
  `"meta":{"hidden_count":N}`. The streamer never gets `chat.modqueue`.
- `GET /api/mod/queue` (moderator only; `{as_of_seq, items[ModQueueItem], resolved[stub], pending_count, hidden_count}`),
  `POST /api/mod/action` (moderator only; 404 `not_found`, 409 `not_pending` / `staff_message` / `busy`, 429, 503) publishes
  `ModAction` on `chat.modaction`. Staff messages can be approved or dismissed, never deleted or timed out.
  `GET /api/chat/overlay` (streamer): hidden count and allowed messages only.

## Go-live orchestration, risk and safety report (T6.1)
- `orchestrator.py` spawns `python -m engine.main --profile <p>` (which builds relay, vision, audio and guard through
  `module:build`) and, when `chat.enabled`, `python -m chat.main --profile <p>`; stdout/stderr are discarded (chat output is
  scanned only for the fixed status codes `groq_key_missing`, `yt_*`, `twitch_*`). Engine death is reported, never
  restarted; chat restarts only on exit 3 (bus), max 3 times. Stop: SIGTERM, `SESSION_STOP_TIMEOUT_S` grace, then kill.
  An engine already heartbeating (run by hand) is attached to, not spawned. `SESSION_SPAWN=false` never spawns.
  Env: `SESSION_ENGINE_CMD`, `SESSION_CHAT_CMD` (`{profile}` replaced), `SESSIONS_DIR`, `SESSION_TTL_S`, `INGEST_BIND_HOST`.
- Keys: `PUT|DELETE /api/destinations/{youtube|twitch}/key` (RAM only, passed to the engine env), `GET /api/destinations`.
  No endpoint returns a key. `GET /api/session` fills `ingest` URLs and `processes`; `GET /api/session/health` = readiness.
- `GET /api/pairing`, `/api/pairing/{rtmp|srt}.png` (streamer only, 409 before a session): relay helper with local fallback.
- `GET /api/risk` -> `{level: ok|watch|warn|danger, meter_level, score, signals[]}`; WS carries `guard.meter`/`guard.risk`.
- `GET /api/report[/{id}[/printable|/export?format=json|html]]`, `DELETE /api/session/{id}/data`. `recorder.py` writes
  `events.jsonl` (SafetyEvents only); `report.py` runs the privacy guard on every report.

## Rehearsal start and the end-to-end smoke
`POST /api/session/start {"destinations": [], "rehearsal": true}` starts a session with no platform destination and
needs no stream key: the spawned engine publishes only the local `shielded` tap. A normal start still needs one
enabled destination with a key. The publish URLs (`GET /api/session`, `GET /api/pairing`) use the configured
`INGEST_PORT_RTMP` / `INGEST_PORT_SRT`. `scripts/e2e_smoke.sh` runs the whole chain (API spawns the real engine,
synthetic SPECIMEN sender, egress tap, hold, config toggle, stop, report) with no keys; exit 0/1/2.

## Demo preview (`demo_preview.py`, `routes/demo.py`)
- `POST /api/demo/preview-token` (streamer) -> `{token, expires_in_s: 300}`: HMAC token, role `preview` (not in `ROLES`,
  room `<room>:preview`), accepted only by the two stream routes. `GET /api/demo/status` (streamer) -> `{raw_enabled,
  protected_available, raw_available, clients:{raw,protected}}`.
- `GET /api/demo/preview/{raw|protected}.mjpg?t=<token>`: `multipart/x-mixed-replace` (boundary `frame`), <= 8 fps, <= 640 px,
  no-store/no-cache. One `ffmpeg` per stream (own process group), started by the first client, SIGKILLed with the last
  client, when the session leaves starting/waiting_ingest/live, or at API shutdown; up to `DEMO_MAX_CLIENTS` (default 4, 1..8) clients per stream; a client address that already holds a slot may overshoot by one (token refresh); a connection that stops reading for 2.5 s is evicted (keepalive part every 1 s on an idle stream).
  Errors: 401 bad token, 409 `no_session`, 503 `stream_unavailable` (not live, no picture in `DEMO_FIRST_FRAME_TIMEOUT_S`,
  ffmpeg missing); cap reached: 503 `preview_clients_full` + `Retry-After: 2`. Status also returns `max_clients`.
- `protected` reads the anonymous loopback `rtmp://<ingest host>:<rtmp port>/shielded`. `raw` reads the anonymous loopback
  `rtmp://127.0.0.1:<rtmp port>/camera`, which the relay allows only when the engine environment has `DEMO_RAW_PREVIEW=1`:
  the orchestrator sets it (environment, never argv) only when the setting is on and the API is not on `--lan`, and strips it
  from the inherited environment otherwise. RAW is refused unless `DEMO_RAW_PREVIEW=1` (default off) AND not `--lan` AND the
  client is 127.0.0.1/::1 (403 `raw_preview_disabled` / `raw_preview_loopback_only`). Frames stay in RAM, ffmpeg stderr is
  discarded, tokens and URLs are never logged.
- Settings: `DEMO_RAW_PREVIEW`, `DEMO_MAX_CLIENTS`, `DEMO_FFMPEG_CMD` (default `ffmpeg`), `DEMO_FIRST_FRAME_TIMEOUT_S` (8).
- Tests: `tests/test_api_demo_preview.py` with `tests/fake_ffmpeg.py` (POSIX only: process groups).
