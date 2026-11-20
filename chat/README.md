# chat: platform connectors (T1.5 spike)

YouTube Live Chat and Twitch connectors. Classifier, commands, pipeline and mod queue come in T4.2-T4.5.
Everything runs offline against in-process fakes (`chat/testing/`).

```
python -m pytest chat -q         # 126 tests, CPU, no network, about 6 s
ruff check chat && ruff format --check chat
```
Layout: `common/` (Secret, fixed-origin HTTP, token bucket, backoff, Connector base, `ChatPublisher` for
`chat.allowed` / `chat.verdict` / `safety.event`), `youtube/`, `twitch/`, `testing/` (fakes), `tests/`.

## What you must create before live use
**YouTube** (Google Cloud console)
1. Enable "YouTube Data API v3".
2. OAuth consent screen: External, add each streamer as a Test user. In "Testing" status refresh tokens
   expire after 7 days; publish the app (and pass Google verification for the sensitive
   `youtube.force-ssl` scope) before real streamers use it.
3. Credentials: OAuth client ID, type **Desktop app**. `.env`: `YOUTUBE_OAUTH_CLIENT_ID`,
   `YOUTUBE_OAUTH_CLIENT_SECRET` (a desktop secret is not confidential).
4. Scope: `https://www.googleapis.com/auth/youtube.force-ssl`. Quota: 10,000 units/day (list 5, delete 20, ban 200).
5. Login prints a URL: open it in the Windows browser; the redirect lands on `127.0.0.1:<random port>`.

**Twitch** (dev.twitch.tv console)
1. Register an application, client type **Public** (device-code grant, no secret). The form needs a
   redirect URL: enter `http://localhost` (unused). `.env`: `TWITCH_CLIENT_ID`.
2. Scopes: `user:read:chat`, `moderator:manage:chat_messages`, `moderator:manage:banned_users`. The account
   that authorises must be the streamer or a moderator of the channel.
3. Login shows a code and `https://www.twitch.tv/activate`; approve it in any browser.

## Behaviour notes
- Quota governor: 80 % of the day for reads, 20 % for actions; 10 % of the action budget is never spent, so
  actions stop after 90 deletes or 9 bans (`yt_quota_low`, read-only). Read interval = remaining time /
  remaining affordable reads (3 h plan: 6.75 s). After a process restart use `QuotaGovernor.after_restart()`.
- YouTube timeouts are off unless `allow_timeout` (a ban costs 200 units). Prefer delete.
- A Twitch disconnect only reports `twitch_disconnected`; it never asserts a hold.
- EventSub is a small direct aiohttp client: twitchAPI 4.5.0 has no disconnect/keepalive hook (`twitch/transport.py`).
- Message text, author names and tokens are never logged (`common/logsafe.py`, tested with caplog).
- Adapted from IRL Shield (MIT): `twitch/auth.py`, `storage.py`, `events.py`, `common/http.py`, `common/secret.py`
  (licence in `twitch/LICENSE.irl-shield`).
