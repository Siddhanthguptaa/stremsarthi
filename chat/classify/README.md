# chat/classify: classifier cascade (T4.2)

Commands, normalise, rules, scam / fake UPI, doxxing, then a Groq LLM for the unclear cases
(`docs/specs/chat.md` 5.1). Output is one `ChatVerdict` per message. CPU only, no model files, RAM only.

```
python -m pytest chat/classify/tests -q          # about 330 tests (1 live check skipped without a key), no network, about 10 s
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
`normalize.py` text views, phonetic `fold_token` and Devanagari transliteration. `links.py` link and UPI-ID
refanging (`(at)`, `[at]`, `name at ybl`, `ät`, spaced letters), link extraction and judging. `rules.py` + `lexicons/*.tsv`
(hand-curated, no third-party list copied; optional 6th column `frame` / `mention` / `game` / `topic` / `cricket` = the
word counts only in that context). `context.py` quoted or reported abuse, friendly banter, game talk. `scam.py` +
`scam_handles.txt` (UPI IDs, QR lures, scam talk). `doxxing.py` phones, PIN and written addresses, Verhoeff, PAN, intent,
indirect requests, streamer private terms (typo tolerant). `suspect.py` threat shape and weak signals for the LLM. `llm.py`
batching, rpm/tpm buckets, retry once, prompt, `GroqTransport`. `cascade.py` decision and action mapping. `bench.py`.

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

## Policy: what counts as what (taxonomy and actions)
One abuse category (`CHAT_ABUSE`) covers all of these; the lexicon `kind` only drives the reason code.
- **Insults and slurs aimed at a person**: severity 3 slur (family or sexual abuse) = final, `severe_abuse` action; severity
  1-2 or a word that is often harmless (`frame` words such as `saala`, `kutta`) = hold for a moderator, and only when aimed
  at someone (you, a vocative, a comparison). Profanity used as praise (`bc kya clutch tha`) is still held (Gate 4 labels it
  abuse; the blind set calls it banter: `context.ALLOW_PROFANE_BANTER` flips it). Jocular mild words in a friendly frame
  (`abe saale kya khela`, `kamine ye to meri kill thi`) are allowed.
- **Threats**: a violence verb or outcome + first-person future (`maar dunga`, `haddi pasli ek kar dunga`, `ghar aa raha hu`),
  body part + break, `zinda nahi chhodunga`, wishes of death or harm, `swat`, doxx-you, in Hinglish, Devanagari, Telugu,
  Kannada, Tamil, Bengali and English. Explicit violence = severity 3 (final, deleted or timed out); vague or conditional
  (`watch your back`, `teri khair nahi`) = severity 2 (hold). Game talk (`clutch`, `round`, `enemy`) with no real-life cue
  (`ghar`, `family`, `irl`, `address`) is not a threat. A shape check (someone addressed + harm word + future) adds a 0.8 hold.
- **Sexual harassment**: comments, propositions, pricing or demands aimed at a person (`kitne mein chalegi`, `show ...`,
  `raat ko akeli aa`, `send nudes`) = hold; sexual violence (`rape kar dunga`) = severity 3. News or policy talk about rape
  (`case`, `victim`, `law`) is not flagged. Ordinary compliments are not harassment.
- **Identity-based abuse** (caste, religion, region, nationality, gender, sexuality, disability): a group NAME is never
  flagged. Flagged: (1) slurs aimed at people, (2) a sweeping put-down of a group (group + `log` + dehumanising predicate =
  severity 3; demeaning predicate with "all/sab/log hote hain" = 2), calls to expel or harm a group, `go back to ...`,
  caste taunts, sexist put-downs (`ladkiyan rasoi me`), sexuality or colour insults. Negated or quoted statements
  (`not all muslims are terrorists`) are not flagged. Criticism of ideologies, parties or beliefs is out of scope.
- **Quoted or reported abuse** (`he called me ...`, `usne bola "..."`, `do not say ...`, `ban whoever wrote ...`) is not abuse:
  with a Groq stage it goes to the LLM, without one it is allowed (`rules_reported_allow`). Abuse laundered through such a
  frame is the cost; the LLM prompt covers it.
- **Doxxing**: private terms (also with one typo, split or run together), mobile numbers, PIN with an address cue, written
  addresses (house number + street word), Aadhaar-like numbers, PAN, leak intent, and indirect requests for a third person's
  whereabouts or details (`who knows where he lives`, `iska ghar kahan hai`) = 0.8, hold. Asking the streamer where they
  live is not flagged. Moderator-posted private data is flagged but never hidden.
- **Fake UPI**: deep links, IDs next to a money word, any obfuscated ID (`name at ybl`, `name[at]okaxis`), QR or "scan this to
  receive money" lures. **Scam talk** (a warning or moderator notice: `mat click karo`, `scam alert`, `ban the bots`) is not an
  offer: allowed without Groq, to the LLM with it. A bare ID with no money word stays 0.6 (hold).

## Local fallback (no Groq key) and what rules alone can reach
Candidates have three outcomes: score >= 0.9 final; 0.3 to 0.9 `HOLD_FOR_MOD` without an LLM (`llm_unavailable_hold`) and
`HIDE` then the LLM's verdict with one; and **lenient** candidates (reported speech, scam talk, `suspect.py` weak shapes: aimed
message + violence / sex / insult / group + put-down wording, regional-script message aimed at someone, digits + contact
cue, third person + residence word, payment words + amount) which go to the LLM and are ALLOWED locally. So the weak shapes
never add a false positive without a key, and without a key their recall is simply not available. Rules can name known
words and common structures. They cannot judge an insult with no listed word (`tu kisi kaam ka nahi`), a hint (`i know where
you live` is caught, `main ghar ke bahar hu` is not), sarcasm, or a threat in a language we have no lexicon for.
