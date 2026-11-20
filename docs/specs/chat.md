# Spec: chat (chat shield)

## 1. Purpose and owner
Read YouTube Live Chat and Twitch chat, classify every message (Hinglish and Indian-language abuse, doxxing, scam links, fake UPI payment links), hide flagged messages from the streamer's overlay, queue them for moderators, and take platform actions (delete, timeout) where the platform's API and quota allow. Also turns `!hold` / `!unhold` into `HoldCommand` events.
Owner: **chat-engineer**. Runs as its own process (P4), never on the frame path. A chat crash must not stop the stream.

## 2. Directories owned and file layout
Only `chat/` and its tests. One subpackage per task so tasks never share a file.
```
chat/
  __init__.py
  common/            # T1.5 (spike) then frozen: Secret type, http client, retry/backoff, clock, token bucket
    secret.py        # adapt reference/irl-shield/shield/twitch/storage.py Secret (refuses pickling)
    http.py          # fixed-origin allowlisted client (adapt shield/twitch/http.py: no redirects, 64 KiB cap)
    bucket.py        # TokenBucket(rate, capacity) async + sync
  classify/          # T4.2
    normalize.py     # text normalisation (see 5.1)
    rules.py         # lexicon matcher
    lexicons/        # *.tsv (format in 5.1)
    scam.py  doxxing.py  llm.py  cascade.py
    tests/
  youtube/           # T1.5 spike, T4.3 full
    auth.py  broadcast.py (liveChatId lookup)  poller.py  governor.py  actions.py
    tests/
  twitch/            # T1.5 spike, T4.4 full
    auth.py  storage.py  transport.py  events.py  actions.py
    tests/
  commands/          # T4.4
    hold.py  roles.py
    tests/
  pipeline/          # T4.5
    orchestrator.py  overlay_feed.py  actions_router.py
    tests/
  modqueue/          # T4.5
    queue.py  store.py
    tests/
  main.py            # T4.5: process entry (`python -m chat.main`), wires bus, connectors, pipeline
```

## 3. Inputs
- Platform chat via connectors (section 5.2 and 5.3), normalised to `ChatMessage`.
- Bus topics subscribed (names defined in `docs/specs/contracts.md`): `control.config` (`ConfigChange`), `chat.modaction` (`ModAction` from the API), `engine.status`.
- `Config.chat` (section 8) and secrets from `.env`: `YOUTUBE_OAUTH_CLIENT_ID/SECRET`, `TWITCH_CLIENT_ID` (and `TWITCH_CLIENT_SECRET` if a confidential app is used), `GROQ_API_KEY`.

## 4. Outputs
- `ChatVerdict` on `chat.verdict` for every message (including ALLOW).
- `ChatMessage` on `chat.allowed` **only** when the final verdict is ALLOW (this is the only text the streamer overlay ever sees).
- `ModQueueItem` (message + verdict) on `chat.modqueue` for HOLD_FOR_MOD / HIDE_FROM_STREAMER / DELETE / TIMEOUT. Moderator role only (enforced by the API, see `app-api.md`).
- `HoldCommand(source=CHAT, active, actor=<hash>)` on `control.hold`.
- `SafetyEvent(modality=CHAT, category=CHAT_*, action in hidden|deleted|timed_out|held, detail_code=<reason code>)` on `safety.event`. Never any message text.
- Platform actions (delete message, timeout) with result written back as `platform_result` on a follow-up `ChatVerdict`.

## 5. Behaviour

### 5.1 Classifier cascade (`chat/classify/cascade.py`), fastest first
`ChatClassifier.classify(batch) -> verdicts`. Per message:
1. **Command check** (before everything): `!hold`, `!unhold` go to `commands/`, not the classifier.
2. **Normalise** (`normalize.py`, target ≤ 0.5 ms): NFKC, lowercase, strip zero-width/combining tricks (U+200B–200F, U+2060, U+FEFF), map homoglyphs and leet (`0→o 1→i 3→e 4→a 5→s 7→t @→a $→s`), collapse runs of ≥3 identical letters to 2, remove single separators between single letters (`m.c`, `m c`), keep an unmodified copy for link/UPI regexes. Devanagari text is kept as is and also transliterated to a rough Roman form (small table) so one lexicon can match both.
3. **Rules** (`rules.py`, ≤ 5 ms): lexicon files `chat/classify/lexicons/*.tsv`, columns `term<TAB>category<TAB>severity(1-3)<TAB>lang<TAB>match(word|substr|regex)`. Files: `abuse_hinglish_latin.tsv`, `abuse_devanagari.tsv`, `abuse_regional.tsv` (kn, ta, te, bn), `threat.tsv`, `sexual_harassment.tsv`, `benign_allow.tsv` (false-positive guard: words that contain abusive substrings). Sources must be permissive: the Hinglish hate-speech bot (Apache-2.0) and ToxVidLM (MIT) lists, plus hand-curated terms. Record every source in `docs/THIRD_PARTY.md`. Score = max severity weight (1→0.5, 2→0.75, 3→0.95), +0.1 per extra distinct hit (cap 1.0). A streamer-directed pattern (second person, `tu`, `tera`, `behen`, `@streamer`) adds +0.05.
4. **Scam** (`scam.py`, ≤ 2 ms), run on the *unnormalised* text:
   - UPI deep link: `(?i)\bupi://pay\?[^\s]*` and `(?i)pay\?pa=[A-Za-z0-9._\-]{2,64}@[A-Za-z]{2,32}`.
   - Bare UPI ID: `(?i)\b[a-z0-9._\-]{2,64}@(?:ok(?:sbi|hdfcbank|icici|axis)|ybl|ibl|axl|paytm|apl|upi|sbi|hdfcbank|icici|axisbank|okbizaxis|fbl|pthdfc|ptyes)\b` (handle list in `scam_handles.txt`, extendable).
   - Payment-link hosts: `phonepe.com/…pay`, `paytm.me`, `gpay`-style shorteners, plus generic shorteners (`bit.ly`, `tinyurl.com`, `t.ly`, `cutt.ly`, `rb.gy`, `is.gd`) when combined with a payment/giveaway word.
   - Fake giveaway: giveaway/prize/winner/`free (fire|diamond|uc|robux)`/`claim` within 40 chars of `link|click|upi|pay|send|dm|whatsapp|telegram`.
   - Output: CHAT_FAKE_UPI (UPI deep link or UPI ID + money word) at 0.98, CHAT_SCAM_LINK (shortener/giveaway) at 0.9.
5. **Doxxing** (`doxxing.py`, ≤ 3 ms):
   - Streamer private terms from `Config.chat.streamer_private.terms` (real name, city, school, address fragments) matched on normalised text; held in RAM only, never logged.
   - Indian mobile `(?:(?:\+|00)?91[\s\-]?)?[6-9]\d{4}[\s\-]?\d{5}` after stripping separators (also spoken-digit spelled form `nine eight…` and Devanagari digits).
   - PIN `\b[1-9]\d{2}\s?\d{3}\b` only when an address cue is within 30 chars: `house|flat|plot|h\.?no|near|road|nagar|colony|gali|mohalla|sector|pin(code)?|ghar|address|rehta|rehti`.
   - Verhoeff-valid 12-digit number (Aadhaar-like) and PAN `[A-Z]{5}\d{4}[A-Z]`.
   - Intent phrases (`doxx`, `leak (his|her|their) address`, `ghar ka address`).
   - Output CHAT_DOXXING 0.9 (0.97 if streamer private term or full phone).
6. **Decision.** Take the highest-severity candidate. If score ≥ `rules_hide_threshold` (0.9) → final, `stage="rules"`. If score < 0.3 and nothing matched → ALLOW immediately (`stage="rules"`). Otherwise (0.3 ≤ score < 0.9) → **provisional** verdict now (HIDE_FROM_STREAMER, `reason="pending_llm"`) and send to the LLM stage; the LLM result later replaces it (upgrade to HOLD_FOR_MOD/DELETE, or downgrade to ALLOW and re-emit on `chat.allowed`).
7. **LLM stage** (`llm.py`), Groq `openai/gpt-oss-20b`, JSON mode, temperature 0, `reasoning_effort` low:
   - Batch up to 20 messages; flush when full or 40 ms after the first queued message.
   - Prompt carries only the message texts by index, never author names or IDs. System prompt: "Classify each chat message for a live stream of an Indian streamer. Messages may be Hinglish (Hindi in Latin letters), Devanagari, Kannada, Tamil, Telugu, Bengali or English. Return JSON only."
   - Response schema: `{"results":[{"i":int,"labels":["abuse"|"doxxing"|"scam"|"fake_upi"|"clean"],"confidence":0..1,"target":"streamer"|"viewer"|"none"}]}`; any parse failure or missing index → keep the provisional verdict and retry once.
   - **Limits:** token bucket 30 requests/min (rate 0.5/s, capacity 30) and a token-per-minute governor of 8,000 tokens/min (UNVERIFIED limits, confirm at T1.5); estimate tokens as `len(chars)/3 + 120` per message. When a bucket is empty the message keeps the provisional verdict for up to 10 s, then is finalised as HOLD_FOR_MOD (fail toward privacy of the streamer, never toward silently allowing).
   - Per-call timeout 250 ms (`llm_timeout_ms`). Hard cap p95 300 ms applies to the *provisional* verdict; the final LLM verdict may arrive later.
8. **Action mapping** (`Config.chat.actions`, defaults): abuse → HIDE_FROM_STREAMER + HOLD_FOR_MOD; severity-3 abuse/threat → DELETE (if platform allows); doxxing → HIDE + DELETE + HOLD_FOR_MOD; scam/fake UPI → HIDE + DELETE; repeat offender (≥3 flagged in 5 min) → TIMEOUT 600 s (disabled on YouTube by default because of the 200-unit ban cost).

### 5.2 YouTube connector (`chat/youtube/`)
- OAuth: installed-app loopback + PKCE on `127.0.0.1:<random port>`; user opens the printed URL in the Windows browser (works under WSL mirrored networking). Scope `https://www.googleapis.com/auth/youtube.force-ssl` (read + delete + bans). Google's device-code flow for this scope is UNVERIFIED and not relied on. Tokens in RAM; optional OS keyring with `--remember`.
- `broadcast.py`: `liveBroadcasts.list?mine=true&broadcastStatus=active` (1 unit) → `snippet.liveChatId`. Retry every 10 s until live.
- `poller.py`: `liveChatMessages.list(liveChatId, part=snippet,authorDetails, maxResults=200, pageToken)` (5 units). Always sleep `max(pollingIntervalMillis, governor_interval)`. Moderator flags from `authorDetails.isChatModerator` / `isChatOwner`.
- **Quota governor** (`governor.py`), daily quota 10,000 units (resets midnight Pacific):
  - Reserve `action_budget_fraction` = 0.2 → 2,000 units for actions, 8,000 for reads.
  - Costs: list 5, delete 20, ban/timeout 200 (`liveChatBans.insert`), broadcast lookup 1.
  - Read interval = `planned_stream_hours × 3600 / (remaining_read_units / 5)`. Example: 3 h → 10,800 s / 1,600 calls = 6.75 s; 2 h → 4.5 s. Recompute after every call from the real counter; floor 2 s, honour the server value if larger.
  - Action budget allows about 100 deletes or 10 bans a day. When < 10 % of the action budget remains, switch to hide-on-our-side only and emit a `SafetyEvent(detail_code="yt_quota_low")`.
  - Persist the counter in memory only (restart assumes the worst: subtract 25 % margin on start).
- `actions.py`: `liveChatMessages.delete(id)` and `liveChatBans.insert(type=temporary, banDurationSeconds)`. On 403/quotaExceeded → `platform_result="failed"`, stop actions, keep hiding locally.
- gRPC `streamList` is an **optional upgrade** (UNVERIFIED that a clean Python client exists). Not part of v1 unless T1.5 proves it.

### 5.3 Twitch connector (`chat/twitch/`)
- Auth: device-code grant as a public client (adapt `reference/irl-shield/shield/twitch/auth.py:114-212`), hourly validate, refresh rotation in RAM. **Scopes:** `user:read:chat`, `moderator:manage:chat_messages`, `moderator:manage:banned_users`. IRL Shield's `moderator:read:shield_mode` and `channel:read:stream_key` are dropped.
- `transport.py`: EventSub over WebSocket (`wss://eventsub.wss.twitch.tv/ws`) using twitchAPI 4.5.0 public API only (no monkeypatching private hooks); handle session welcome, keepalive timeout, `session_reconnect`. Subscribe `channel.chat.message` (version 1, condition `broadcaster_user_id`, `user_id`). Dedup + generation fencing from `shield/twitch/events.py`.
- `actions.py`: `DELETE /helix/moderation/chat?broadcaster_id&moderator_id&message_id`, `POST /helix/moderation/bans` with `data.duration` 1–1,209,600 s. `moderator_id` is the token user. Helix rate limit 800 points/min (UNVERIFIED).
- A dropped EventSub connection **does not** assert a hold (IRL Shield did). It emits `SafetyEvent(detail_code="twitch_disconnected")` only.
- Dev without OAuth: anonymous IRC read-only mode (`justinfan` nick) behind `chat.twitch.anonymous_read=true`, no actions.

### 5.4 Commands (`chat/commands/`)
- Exact tokens `!hold` and `!unhold` (case-insensitive, trimmed, no arguments). Roles from platform data, never from message text.
- `!hold`: broadcaster or moderator. `!unhold`: **broadcaster only** by default (`chat.commands.unhold_roles=["broadcaster"]`; IRL Shield also restricts resume to the broadcaster). Others are ignored with `SafetyEvent(detail_code="cmd_denied")`.
- Per-actor rate limit 1 command / 2 s; dedupe by `message_id`; the command message itself is verdict ALLOW-silent (not shown in overlay, not queued).
- Emits `HoldCommand(source=CHAT, active=True/False, actor=sha256(platform+author_id)[:16])`.

### 5.5 Pipeline and mod queue (`chat/pipeline/`, `chat/modqueue/`)
- Orchestrator: connector → command check → cascade → publish verdict → route (overlay / queue / actions). Bounded queues (1,000 messages), drop-oldest on overload with a `SafetyEvent(detail_code="chat_overload")`; hold commands bypass the queue.
- Mod queue: RAM only, max 500 items, TTL 1,800 s, **no disk persistence**. Item = `ModQueueItem(message, verdict, state)`. Actions from API `ModAction`: `approve` (release; if previously hidden, publish on `chat.allowed`), `delete`, `timeout` (seconds), `dismiss`. Result goes back as `platform_result`.
- Overlay feed: the API fans out `chat.allowed` to the streamer and the hidden count only (`hidden_count` is derived from verdicts, no text).

### 5.6 Privacy rule (enforced by test)
Raw `ChatMessage.text` of any message whose verdict is not ALLOW is **never** written to logs, JSONL, exceptions, metrics or the safety report. Logs carry `message_id` hash, category, action, confidence, stage, latency only. A log-scan test (case 14) fails the build otherwise. Streamer private terms and phone patterns are never logged either.

## 6. Reuse
| Source | Target | Change |
|---|---|---|
| `reference/irl-shield/shield/twitch/auth.py`, `http.py`, `storage.py` | `chat/twitch/auth.py`, `chat/common/http.py`, `chat/twitch/storage.py`, `chat/common/secret.py` | Scopes, no `/dev/tty` prompts (use a callback), keep `Secret` |
| `reference/irl-shield/shield/twitch/events.py` | `chat/twitch/events.py` | Dedup/fencing kept; remove the connection-loss HOLD; map to `ChatMessage` |
| `reference/irl-shield/shield/twitch/transport.py` | `chat/twitch/transport.py` | REWRITE on the public twitchAPI interface |
| `reference/irl-shield/tests/m3/*` (mock Twitch pattern) | `eval/mock_platforms/` (eval-engineer) and `chat/*/tests/` | Port as fixtures |
| Hinglish hate-speech bot (Apache-2.0), ToxVidLM (MIT) | `chat/classify/lexicons/` | Term lists only, attributed |

## 7. Edge cases
Duplicate message ids; edited/deleted-by-user messages; emoji-only; mixed scripts in one message; very long messages (truncate to 500 chars before the LLM); Super Chat / membership events (treated as messages, amount ignored); messages from the broadcaster and moderators (never hidden, still scanned for dox of others → mod queue only); 429/quota errors; OAuth expiry mid-stream; EventSub reconnect; chat paused or slow mode; a command from a non-mod; clock skew; platform lacks an action (`platform_result="unsupported"`); LLM returns a label we do not know (treated as `clean` with a warning); bucket empty during a raid (burst of 500 messages in 10 s).

## 8. Config keys used (`Config.chat`)
`enabled`, `platforms: ["youtube","twitch"]`, `youtube.{planned_stream_hours=3, daily_quota_units=10000, action_budget_fraction=0.2, min_poll_s=2, allow_timeout=false, auto_delete=true}`, `twitch.{channel_login, anonymous_read=false, auto_delete=true, auto_timeout=true}`, `classify.{rules_hide_threshold=0.9, allow_below=0.3, llm_enabled=true, llm_model="openai/gpt-oss-20b", llm_batch_max=20, llm_flush_ms=40, llm_timeout_ms=250, llm_rpm=30, llm_tpm=8000, llm_pending_max_s=10}`, `actions.{abuse,severe_abuse,doxxing,scam}`, `streamer_private.{terms:[], phones:[]}`, `commands.{enabled=true, hold_roles=["broadcaster","moderator"], unhold_roles=["broadcaster"], rate_limit_s=2}`, `modqueue.{max_items=500, ttl_s=1800}`.

## 9. Test cases (CPU, no network)
1. Rules: 30 Hinglish-Latin, 10 Devanagari, 10 regional abusive strings each reach the expected category; the `benign_allow` set (e.g. words containing an abusive substring) is ALLOW.
2. Normalisation: `m.c`, `m c`, `m*c`-style obfuscation, leet, zero-width insertions, repeated letters all match the plain form.
3. Scam: fixture set of 40 positive (UPI deep links, UPI IDs with money words, shortener + giveaway) and 40 negative (normal URLs, `@mentions`, emails) → recall ≥ 95 %, no false positive on emails.
4. Fake UPI payment link (`upi://pay?pa=…`) → CHAT_FAKE_UPI, DELETE+HIDE, confidence ≥ 0.98.
5. Doxxing: phone in 6 formats incl. spaced and Devanagari digits, PIN with and without address cue (no cue → no flag), Verhoeff-valid 12-digit, streamer private term → flagged. Random 10-digit non-mobile → not flagged.
6. Cascade routing: clear-clean → ALLOW in ≤ 5 ms; borderline → provisional HIDE then LLM upgrade/downgrade (fake Groq client).
7. Groq contract: malformed JSON, missing index, unknown label, timeout, 429 → provisional kept, retried once, finalised to HOLD_FOR_MOD after 10 s.
8. Buckets: 31 batches in a minute → the 31st waits/queues; TPM governor blocks an oversize batch.
9. YouTube governor maths: 3 h plan, 10,000 units → interval 6.75 s (±0.1); after 100 deletes the action budget is exhausted and actions switch to hide-only; low-quota event emitted.
10. YouTube poller against `eval/mock_platforms/youtube_mock.py`: honours `pollingIntervalMillis` and `nextPageToken`, no duplicate messages, resumes after a 503.
11. Twitch transport against `twitch_mock.py`: welcome → subscribe → messages → keepalive timeout → reconnect without duplicate delivery; no HOLD emitted on disconnect.
12. Commands: `!hold` from viewer ignored; from moderator accepted; `!unhold` from moderator rejected (default) and accepted for broadcaster; rate-limit; dedupe.
13. Mod queue: TTL expiry, cap eviction (oldest), approve re-publishes on `chat.allowed`, actions call the platform fakes.
14. Privacy: run 200 flagged messages through the full pipeline with log capture; assert none of their text, no streamer private term and no phone number appears in logs, events or exceptions.
15. Latency probe: 1,000 mixed messages, rules/provisional path p95 ≤ 300 ms (target ≤ 20 ms on CPU), LLM path p95 ≤ 250 ms with the fake client.
16. Overload: 5,000 messages in 10 s → no unbounded growth (queue ≤ 1,000), hold command still processed in < 100 ms.
**Acceptance (T4.x, Gate 4):** `scripts/gate_phase4.sh` runs the eval chat set (2,000 messages) through the mock platforms and enforces: abuse recall ≥ 85 % / precision ≥ 90 %, scam recall ≥ 95 %, fake UPI ≥ 98 %, doxxing ≥ 85 %, clean false-positive ≤ 3 %, classify p95 ≤ 300 ms.

## 10. Latency target
Rules/provisional verdict p95 ≤ 300 ms (aim ≤ 20 ms). LLM final verdict ≤ 250 ms call. Receipt to hidden-on-overlay ≤ 1 s. YouTube adds the poll interval (about 5–7 s) before a message is even seen.

## 11. VRAM / CPU target
VRAM 0. CPU ≤ 0.3 core steady, ≤ 1 core during a burst. RAM ≤ 100 MB.

## 12. Tasks
- **T1.5** (Wave 1, deps T0.1): `youtube/`, `twitch/` spike: OAuth flows, governor, mock servers; confirm Groq/Sarvam limits and YouTube gRPC question.
- **T4.2** (deps contracts): `classify/`.
- **T4.3** (deps T1.5): `youtube/`.
- **T4.4** (deps T1.5): `twitch/`, `commands/`.
- **T4.5** (deps T4.2): `pipeline/`, `modqueue/`, `main.py`.
- Eval data and mock platforms: **T4.1** (eval-engineer).

## 13. Out of scope
MuRIL fine-tuning (later, Colab/Kaggle); gRPC `streamList` unless proven; persisting any chat text; bans on YouTube by default; chat translation; viewer-facing replies/bot messages; sentiment analytics.
