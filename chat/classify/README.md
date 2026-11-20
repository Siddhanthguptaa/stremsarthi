# chat/classify: classifier cascade (T4.2)

Commands, normalise, rules, scam / fake UPI, doxxing, then a Groq LLM for the unclear cases
(`docs/specs/chat.md` 5.1). Output is one `ChatVerdict` per message. CPU only, no model files, RAM only.

```
python -m pytest chat/classify/tests -q          # 200 tests (1 live check skipped without a key), no network, about 10 s
python -m chat.classify.bench                     # precision / recall / latency on the SPECIMEN set (rules only)
ruff check chat && ruff format --check chat
```

## Use
```python
clf = ChatClassifier(config.chat, llm=GroqTransport(Secret(key)), on_update=cb)  # llm=None: rules only
verdicts = await clf.classify(batch)  # fast verdicts now; borderline ones are provisional
await clf.classify_final(batch)  # waits for the LLM too (eval, tests)
clf.update_config(new_chat_config)  # ConfigChange: thresholds, actions, private terms
```
`on_update(message, verdict)` delivers the final verdict of a provisional message (upgrade, or downgrade to
ALLOW so the pipeline re-emits it on `chat.allowed`). The caller (T4.5) owns that routing.

## Files
`normalize.py` text views and Devanagari transliteration. `rules.py` + `lexicons/*.tsv` abuse lexicons
(hand-curated, no third-party list copied). `scam.py` + `scam_handles.txt`. `doxxing.py` phones, PIN with
address cue, Verhoeff, PAN, intent, streamer private terms. `llm.py` batching, rpm/tpm buckets, retry once,
`GroqTransport`. `cascade.py` decision and action mapping. `bench.py` eval harness.

## Behaviour to know
- No API key: `llm=None` (or `llm_enabled=false`). Borderline messages become `HOLD_FOR_MOD`
  (`llm_unavailable_hold`), never silently allowed. Live Groq test is skipped without `GROQ_API_KEY`.
- LLM unreachable for `llm_pending_max_s`: `HOLD_FOR_MOD` (`llm_timeout_hold`).
- Broadcaster/moderator messages are never hidden. A doxxing hit comes back as an ALLOW verdict that carries
  `CHAT_DOXXING` and reason `staff_dox_flag` (the pipeline queues it for moderators).
- `!hold` / `!unhold` return ALLOW with reason `command`; `commands/` handles them before this stage.
- Severe abuse uses `Config.chat.actions.severe_abuse` (default `timeout`, 600 s); 3 flags in 5 min from one
  author also give TIMEOUT. The platform layer downgrades it where timeouts are off.
- Nothing logs text, private terms or numbers: log lines carry codes only (`chat/common/logsafe.py`).
