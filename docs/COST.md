# Cost per streaming hour (T7.4)

Written 2026-11-21 by a research agent from public price pages and the repo's own rates. Refreshed the same day: live captions (Sarvam translate) and the live chat process now exist in the repo, but neither has ever been run against a real API, so no rate or price below is measured. Prices were not re-fetched in the refresh. **No live API call has ever been made** (all keys empty), so every request rate below is an assumption, not a measurement. Hosting is local (the streamer's laptop): zero cloud cost. Prices were read on 2026-11-21; re-check before quoting them to anyone.

## 1. Prices used (source, date read)

| Item | Price | Source (read 2026-11-21) |
|---|---|---|
| Sarvam speech-to-text, real-time and batch, pay as you go | INR 0.49 per minute (INR 29.40 per hour) | https://www.sarvam.ai/api-pricing |
| Sarvam translate, pay as you go | INR 2.00 per 1,000 characters | https://www.sarvam.ai/api-pricing |
| Sarvam signup credit | INR 100, no expiry | https://www.sarvam.ai/api-pricing |
| Groq `whisper-large-v3-turbo` | USD 0.04 per audio hour | https://console.groq.com/docs/models |
| Groq `openai/gpt-oss-20b` | USD 0.075 per 1M input tokens, USD 0.30 per 1M output tokens | https://console.groq.com/docs/models (a web-search snippet gave the same figures; the older launch-blog figure USD 0.10 / 0.50 is outdated) |
| YouTube Data API v3 quota | 10,000 units per day per project. `liveChatMessages.list` 5, `liveChatMessages.delete` 20, `liveChatBans.insert` 200, `liveBroadcasts.list` 1. Quota itself is not billed | https://developers.google.com/youtube/v3/determine_quota_cost |
| USD to INR | 96 (ASSUMED) | Search results gave 94.7 to 96.5, newest dated 2026-07-26 (https://currencyconvert.online/usd/inr). No November 2026 rate found: UNVERIFIED |

Caveats on the prices:
- The Sarvam page does not name Saaras v4, Mayura or sarvam-translate. It lists "Real-time" and "Batch" speech-to-text and "Translate". That these rows are the models in `docs/DECISIONS.md` D-3 and D-7 is an inference. How Sarvam rounds short requests (per second, per request, minimum length) is UNVERIFIED. Our segments are 1.5 s or shorter, so rounding could matter a lot.
- Groq billing minimum: `docs/specs/audio.md` 5.2 says audio under 10 s is billed as 10 s. I did not confirm that. It only matters on the Groq Whisper failover path.
- Twitch Helix and EventSub: no price found or checked. UNVERIFIED (believed free).
- Hosting: MediaMTX, engine, vision, API and web all run on the laptop. Internet upload for the 4.5 Mbps stream (about 2 GB per hour) is the streamer's own connection, not counted.

## 2. Groq limits that matter more than the price (CONFLICTING, UNVERIFIED for this account)

- `https://console.groq.com/docs/rate-limits` ("base limits for the Developer plan"): gpt-oss-20b 30 RPM, 1,000 requests per day, 8,000 tokens per minute, 200,000 tokens per day. whisper-large-v3-turbo 20 RPM, 2,000 requests per day, 7,200 audio seconds per hour.
- `https://console.groq.com/docs/models`: gpt-oss-20b 250K tokens per minute and 1K RPM; whisper-large-v3-turbo 400 RPM and 400K audio seconds per hour.
- These disagree. Which one applies to the user's key is unknown until the first live call. Our spec assumed the small set (30 RPM, 8,000 TPM).
- **If the small set applies, the chat LLM stage cannot serve even one typical hour**: 300 calls x about 690 tokens = about 207K tokens, over the 200K per day cap (and over 1,000 requests per day after about 3 typical hours). Messages the limiter cannot serve are finalised as HOLD_FOR_MOD after 10 s (spec chat.md 7), so the shield fails toward over-holding, not toward leaking. The demo must not depend on the LLM stage.

## 3. Request rates (ASSUMPTIONS unless marked repo)

Repo facts used: audio segments are at most 1.5 s with 0.4 s overlap and only voiced segments (at least 200 ms speech) go to the speech API (`audio/README.md`, spec audio.md 5.1); Sarvam REST bucket 55 requests per minute; Groq Whisper failover 18 requests per minute in chunks of at least 3 s; Groq PII LLM only for ambiguous spans, 25 requests per minute, at most 40 tokens in, `max_tokens` 200 (audio.md 5.7); chat LLM batches up to 20 messages with a 40 ms flush, 30 requests per minute, token estimate `len/3 + 120` per message, plus about 400 tokens for the richer system prompt (docs/STATUS.md T4.1); YouTube read interval 6.75 s for a 3 h plan, which STATUS measured as about 7,900 of 10,000 units over 3 simulated hours.

| Assumption | Low | Typical | High |
|---|---|---|---|
| Share of the hour the streamer is speaking | 30 % | 60 % | 90 % |
| Segment overlap factor (billed audio / speech audio) | 1.10 | 1.20 | 1.36 (continuous speech: 1.5 / 1.1) |
| Chat messages per hour | 600 (10 per min) | 3,000 (50 per min) | 30,000 (500 per min) |
| Share of messages that reach the LLM (borderline) | 5 % | 10 % | 20 % |
| Spoken-PII LLM calls per hour | 20 | 120 | 1,500 (near the 25 per min cap) |
| Spoken-PII call tokens (in / out) | 300 / 80 | 300 / 80 | 350 / 200 |
| Chat LLM call tokens (in / out) | 540 / 150 | 540 / 150 | 540 / 150 |
| Speech characters per second (Hinglish) | 12 | 15 | 18 |

Chat LLM calls: the 40 ms batch window almost never fills at these rates (even 100 borderline messages per minute is about 1.7 per second), so calls are about one per borderline message. Calls per hour: low 30, typical 300, high capped at 1,800 by the 30 per minute limit (the other 4,200 borderline messages fall to HOLD_FOR_MOD). This is my reading of the spec, not a measurement.
Output tokens for gpt-oss-20b include reasoning tokens; 150 per chat call and 80 to 200 per spoken-PII call are guesses (UNVERIFIED).
The share of messages reaching the LLM is UNMEASURED: no Groq key, and STATUS says the rules-only blind set held 206 of 1,513 messages (13.6 %), which is the nearest proxy for the typical 10 %.

## 4. Arithmetic (INR at 96 per USD)

**Sarvam speech-to-text** = speech seconds x overlap / 60 x 0.49.
- Low: 3,600 x 0.30 = 1,080 s x 1.10 = 1,188 s = 19.8 min x 0.49 = INR 9.70
- Typical: 2,160 s x 1.20 = 2,592 s = 43.2 min x 0.49 = INR 21.17
- High: 3,240 s x 1.36 = 4,406 s = 73.4 min x 0.49 = INR 35.98
(The brief's INR 29 per hour assumes the whole hour is sent. VAD gating lowers it unless overlap and heavy speech push it back up.)

**Groq spoken-PII LLM** = calls x (in x 0.075 + out x 0.30) / 1,000,000 USD.
- Low: 20 x (300 x 0.075 + 80 x 0.30) / 1e6 = USD 0.0009 = INR 0.09
- Typical: 120 calls: in 36,000 tokens = 0.0027, out 9,600 = 0.00288, total USD 0.0056 = INR 0.54
- High: 1,500 calls: in 525,000 = 0.0394, out 300,000 = 0.090, total USD 0.129 = INR 12.4

**Groq chat LLM** (540 in, 150 out per call)
- Low: 30 calls = USD 0.0026 = INR 0.25
- Typical: 300 calls: in 162,000 = 0.01215, out 45,000 = 0.0135, total USD 0.0257 = INR 2.46
- High: 1,800 calls: in 972,000 = 0.0729, out 270,000 = 0.081, total USD 0.154 = INR 14.8

**Groq Whisper failover** (only while Sarvam is down or rate-limited): capped at 18 requests per minute = 1,080 per hour. With the (UNVERIFIED) 10 s minimum that is 10,800 billed seconds = 3 h x USD 0.04 = USD 0.12 per hour = INR 11.5 per hour of outage, a ceiling. Not included in the totals.

**Optional live captions** (Sarvam translate, off by default; in the `demo` profile IndicTrans2 would run locally at INR 0, but its weights are not installed: they need a Hugging Face token and a decision about converting the model with `trust_remote_code`; one caption request per segment, no merging, so continuous speech approaches the 45 requests per minute bucket per STATUS T5.3): speech seconds x characters per second x 2 / 1,000.
- Low: 1,080 x 12 = 12,960 chars = INR 25.92
- Typical: 2,160 x 15 = 32,400 chars = INR 64.80
- High: 3,240 x 18 = 58,320 chars = INR 116.64
Captions would be the single largest cost line if sent through the API. Whether muted or redacted tokens are removed before translation (so they save characters) is not checked.

## 5. Totals per streaming hour

| Scenario | Speech to text | PII LLM | Chat LLM | **Total, captions off** | Captions via Sarvam | **Total, captions on** |
|---|---|---|---|---|---|---|
| Low | 9.70 | 0.09 | 0.25 | **INR 10.04 (about USD 0.10)** | 25.92 | INR 35.96 (about USD 0.37) |
| Typical | 21.17 | 0.54 | 2.46 | **INR 24.17 (about USD 0.25)** | 64.80 | INR 88.97 (about USD 0.93) |
| High | 35.98 | 12.42 | 14.77 | **INR 63.17 (about USD 0.66)** | 116.64 | INR 179.81 (about USD 1.87) |

Hosting: INR 0 (local). A cloud relay (not built, not planned in v1) would add GPU hosting; no price researched.
The speech-to-text line is about 88 % (typical) to 97 % (low) of the captions-off total, so Sarvam's rounding rule (section 1) is the biggest unknown. The signup credit of INR 100 covers roughly 4 typical hours (captions off) or about 1 typical hour with API captions.

## 6. YouTube quota (units, not money)

- Chat reads: governor interval = planned hours x 3,600 / (8,000 / 5) seconds. 3 h plan: 6.75 s, 533 calls per hour = **2,667 units per hour**. 2 h plan: 4.5 s, 4,000 units per hour. 1 h plan: 2.25 s, 8,000 units per hour. Reading at the 2 s floor would use 9,000 units per hour and exhaust the day in about 1.1 h.
- Actions come from a reserved 2,000 units (20 %): about 100 deletes or 10 bans a day. Deletes cost 20 each: low 5 per hour = 100 units, typical 20 = 400, high 100 = 2,000 (the whole action budget). A timeout costs 200, so timeouts stay off on YouTube (D-44 and `chat.youtube.allow_timeout=false`).
- One broadcast lookup costs 1 unit (retried every 10 s until live: up to 360 units per hour while waiting, so go live promptly).
- Conclusion: one project key sustains about 3 to 4 streaming hours of chat shield per day. More needs a quota extension request to Google (not researched: UNVERIFIED). The 7,900 units for 3 h is a simulation on a mock server, not a live measurement.

## 7. What is not covered

- Real Sarvam, Groq, YouTube and Twitch behaviour: no keys, nothing live. All rows above should be replaced with invoice and quota-dashboard numbers after the first live rehearsal (`docs/STATUS.md` "Cost per streaming hour" row is still empty).
- Groq cached-input discount (reported at USD 0.037 per 1M by a third-party source only: UNVERIFIED) and the batch discount: not used.
- Taxes (GST), payment fees, free-tier terms: not researched.
