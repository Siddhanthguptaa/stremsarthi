# chat: connectors, classifier, actions, commands, pipeline, mod queue (T1.5, T4.2-T4.5)

```
python -m pytest chat -q         # CPU, no network, about 15 s (live Groq check skips without a key)
ruff check chat && ruff format --check chat
```
Layout: `common/` (Secret, fixed-origin HTTP, token bucket, `ChatPublisher`), `youtube/`, `twitch/`, `classify/`
(own README), `commands/`, `moderation/`, `pipeline/`, `modqueue/`, `main.py`, `testing/` (fakes, rig), `tests/`.

## Live setup you must create
- **YouTube** (Google Cloud): enable YouTube Data API v3; OAuth consent screen External with each streamer as a
  Test user (Testing status: refresh tokens last 7 days; the sensitive `youtube.force-ssl` scope needs
  verification before real use); OAuth client type Desktop app. `.env`: `YOUTUBE_OAUTH_CLIENT_ID/SECRET`.
  Quota 10,000 units/day (list 5, delete 20, ban 200). Login prints a URL: open it in the Windows browser.
- **Twitch** (dev.twitch.tv): Public client (device code), redirect `http://localhost` (unused). `.env`:
  `TWITCH_CLIENT_ID`. Scopes `user:read:chat`, `moderator:manage:chat_messages`, `moderator:manage:banned_users`;
  the authorising account must be the streamer or a moderator.

## Pipeline (T4.5)
`ChatPipeline.submit(msg)` (the connectors' `on_message`) -> `HoldCommands.handle` first (silent, sync) ->
bounded queue (1,000, drop oldest + `chat_overload`) -> `ChatClassifier` -> `ActionsRouter` ->
overlay / `ModQueue` / `ActionDispatcher.apply` / `SafetyEvent`. Late `on_update` verdicts take the same route.
- Overlay contract (`pipeline/overlay_feed.py`): `chat.allowed` carries text only for released messages
  (ALLOW, staff messages, LLM downgrade, moderator approve); `hidden_count` = ids whose latest verdict is not ALLOW.
- Staff `staff_dox_flag` verdicts are shown AND queued. Provisional `pending_llm` hides are not queued.
- `ModQueue`: RAM only, 500 items, 1,800 s TTL, resolved items evicted first. `ModAction`: approve, delete,
  timeout, dismiss; the platform result goes back as `platform_result` on a follow-up verdict.
- `ConfigChange` `chat.*` keys apply live. `main.build(config, bus)` and `main.make_youtube_actions` (sets
  `allow_timeout` from config). Live OAuth + ZeroMQ process wiring (`python -m chat.main`) is not built yet.

## Behaviour notes
- Dispatcher: TIMEOUT off/failed -> DELETE -> HOLD_FOR_MOD (`<reason>:dg_<code>`), never to ALLOW.
- `!hold` broadcaster/moderator, `!unhold` broadcaster only; dedupe by id; 1 command per actor per `rate_limit_s`.
- A Twitch disconnect only reports `twitch_disconnected`; it never asserts a hold. EventSub is a small direct
  aiohttp client (twitchAPI 4.5.0 has no keepalive hook). Anonymous IRC (`anonymous_read`) has no actions.
- Message text, author names, tokens and private terms are never logged (`common/logsafe.py`, tested).
- Adapted from IRL Shield (MIT): `twitch/auth.py`, `storage.py`, `events.py`, `common/http.py`, `secret.py`.
