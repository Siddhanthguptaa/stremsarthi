# Spec: `audio/`

Status: approved plan, section 7. Contracts: `contracts/`. Budgets: `docs/PLAN.md` section 2.5. Strike guard (music, profanity, risk) is `guard.md`.

## 1. Purpose and owner

Turn the 16 kHz audio tap into `MuteSpan`s (and matching mouth-blur `MaskRequest`s) early enough to land inside the delay window, and into redacted `CaptionEvent`s. Neither reference repo mutes audio in a usable way (IRL Shield never mutes; PrivaStream mutes only the current 3 s chunk with no lookahead), so this module is new. Only audio chunks leave the machine, to Sarvam or Groq; nothing is stored. Owner agent: `audio-engineer`.

## 2. Directories owned and file layout

Edit only `audio/` (and `guard/`, see `guard.md`). Tests in `audio/tests/`.

```
audio/
  vad.py                 # Silero VAD ONNX wrapper                                  T0.6
  segmenter.py           # speech segments with cap and overlap                     T0.6
  stt/base.py            # SpeechToText Protocol re-export, Transcript/Word types   T0.6
  stt/sarvam.py  stt/groq.py  stt/local.py  stt/race.py                             T0.6
  spikes/sarvam_groq_spike.md   # Phase 0 spike report (not under docs/)            T0.6
  orchestrator/pipeline.py window.py mute.py failsafe.py                            T3.2
  pii/rules.py numbers.py upi.py address.py llm.py merge.py data/number_words.yaml  T3.3
  captions/translate.py timing.py redact.py                                         T5.3
```
`audio/orchestrator` runs on an asyncio loop in a thread inside the engine process and talks to the engine through `AudioHost.build(config, bus)` (see `engine.md`). Transcript text travels only on a `local_only` bus topic (never bridged to ZeroMQ). **Added to `contracts/` (D-25):** `Topic.TRANSCRIPT` (`audio.transcript`, local only) and `Transcript`/`Word` in `contracts/speech.py` (`Word(text, t0, t1, approx, seg_id)`; `Transcript.latency_s`, `has_word_times`).

## 3. Inputs

`AudioChunk` (16 kHz mono int16, 20 ms multiples, `pts_start`, `arrival`) from the engine tap; `Config.audio`, `Config.captions`; env `SARVAM_API_KEY`, `GROQ_API_KEY`; `delay_seconds` D.

## 4. Outputs

`MuteSpan` (padded, source PTS) and `MaskRequest(kind=MOUTH_OF_SPEAKER, source="audio")` to the engine in-process; `SafetyEvent(modality=AUDIO, action="muted"|"failsafe")`; `CaptionEvent` (redacted); `StageTiming` for `vad`, `segment`, `stt`, `pii_rules`, `pii_llm`, `span_ready`; the local `TRANSCRIPT` topic for `guard/`. No raw transcript in logs, events or on the wire.

## 5. Behaviour

### 5.1 VAD and segmenter
- Silero VAD (ONNX, MIT), 16 kHz, 512-sample (32 ms) windows. Speech on at probability 0.5, off at 0.35 (hysteresis). `min_speech_ms` 120, `min_silence_ms` 200 closes a segment, `speech_pad_ms` 60.
- Hard cap `audio.segment_max_s = 1.5`. If no pause occurs, cut at the lowest-energy 30 ms point in `[1.0, 1.5]` s. The next segment starts `audio.overlap_s = 0.4` before the cut (carries the tail) and is flagged `is_continuation`.
- A segment is `voiced` if at least 200 ms of speech frames. Segments carry `seg_id`, `pts_start`, `pts_end`, `arrival_of_first_sample`.

### 5.2 STT race (`stt/race.py`)
1. Per segment, Sarvam `saaras:v4` (mode `codemix`, language unknown or hinted from `audio.languages`) is primary. Default transport is REST per segment (30 s limit, 60 requests/min) with a token bucket at 55/min so each segment maps to exactly one transcript. WebSocket (finals only, no timestamps, 20 concurrent) is allowed only if the Phase 0 spike shows lower latency and preserved ordering (UNVERIFIED); config `audio.stt.sarvam.transport = rest|ws`.
2. Hedge: if Sarvam has not answered by `audio.race_hedge_s = 0.8`, also start the local faster-whisper (base int8 CPU; small on GPU in `demo`). First valid result wins; late results are discarded.
3. On Sarvam error or 429, switch to Groq `whisper-large-v3-turbo` for 30 s. Groq limit is 20 requests/min, so the Groq path merges consecutive segments into chunks of at least 3.0 s (or until a pause) and stays at 18 requests/min; billing note: anything under 10 s is billed as 10 s.
4. A result is valid if the call succeeded, even with empty text (silence or noise). Timeouts: Sarvam 1.8 s, Groq 1.5 s, local 1.5 s.
5. Word timestamps: used when the engine returns them (Groq verbose, local `word_timestamps=True`); otherwise words are interpolated uniformly over the segment and marked `approx=True` (mute then covers the segment bounds). The Phase 0 spike must report whether Sarvam REST offers timestamps (UNVERIFIED).

### 5.3 Deadlines
- Soft deadline per segment: `arrival_of_first_sample + D - 0.3` (3.7 s at D=4.0). Hard acceptance by the engine: `... + D - 0.1`.
- Budget: segment close at most 1.5 s, STT p95 at most 1.0 s (Sarvam, UNVERIFIED), rules 0.02 s, LLM at most 0.7 s: total at most 3.3 s.
- If a voiced segment has no verdict by the soft deadline, emit `MuteSpan(category=SPOKEN_UNVERIFIED, granularity="failsafe")` over the whole segment (pad 0.1 s) immediately and treat it as final. A verdict arriving earlier than the soft deadline always wins. Music and non-voiced audio pass.

### 5.4 Transcript window (`orchestrator/window.py`)
A deque of `Word` for the last 8 s. Overlap merge: drop leading words of a new segment whose normalised text equals the previous segment's tail and whose `t1 <= last.t1 + 0.05`, matching up to 4 tokens by longest suffix-prefix overlap. A "number run" continues across segments if the gap between number tokens is under 1.0 s. A "watch state" (see 5.6) persists across segments.

### 5.5 Normalisation and number words (`pii/numbers.py`)
Lowercase, NFKC, strip punctuation, map script digits to ASCII: Devanagari `०१२३४५६७८९`, Bengali `০১২৩৪৫৬৭৮৯`, Tamil `௦௧௨௩௪௫௬௭௮௯`, Telugu `౦౧౨౩౪౫౬౭౮౯`, Kannada `೦೧೨೩೪೫೬೭೮೯`. Join single-letter tokens (`o k s b i` to `oksbi`). Spelling data lives in `pii/data/number_words.yaml` (extendable from ASR variants; spellings below are UNVERIFIED against real ASR output).

| digit | English | Hindi roman | Hindi Devanagari | Kannada roman / script | Tamil roman / script | Telugu roman / script | Bengali roman / script |
|---|---|---|---|---|---|---|---|
| 0 | zero, oh | shunya, zero | शून्य, ज़ीरो | sonne / ಸೊನ್ನೆ | poojyam / பூஜ்யம் | sunna / సున్నా | shunyo / শূন্য |
| 1 | one | ek | एक | ondu / ಒಂದು | onru / ஒன்று | okati / ఒకటి | ek / এক |
| 2 | two | do | दो | eradu / ಎರಡು | irandu / இரண்டு | rendu / రెండు | dui / দুই |
| 3 | three | teen | तीन | mooru / ಮೂರು | moondru / மூன்று | moodu / మూడు | tin / তিন |
| 4 | four | char, chaar | चार | naalku / ನಾಲ್ಕು | naangu / நான்கு | naalugu / నాలుగు | char / চার |
| 5 | five | paanch, panch | पांच, पाँच | aidu / ಐದು | ainthu / ஐந்து | aidu / ఐదు | panch / পাঁচ |
| 6 | six | chhe, chhah, che | छह, छः, छे | aaru / ಆರು | aaru / ஆறு | aaru / ఆరు | choy / ছয় |
| 7 | seven | saat, sat | सात | elu / ಏಳು | ezhu / ஏழு | edu / ఏడు | shat / সাত |
| 8 | eight | aath, ath | आठ | entu / ಎಂಟು | ettu / எட்டு | enimidi / ఎనిమిది | aat / আট |
| 9 | nine | nau | नौ | ombattu / ಒಂಬತ್ತು | onbathu / ஒன்பது | tommidi / తొమ్మిది | noy / নয় |

Other tokens: `das` / दस (10), `sau` / सौ (100), `hazaar`, `hazar`, `हज़ार`, `हजार` (1000), `lakh` / लाख; `double`, `dabal`, `डबल` repeat the next digit twice; `triple`, `tripal`, `ट्रिपल` three times; `plus nine one` or `plus ninety one` prefix a country code 91. Compound words (`sau`, `hazaar`) count as number tokens so a run stays contiguous but are not expanded into digits.

### 5.6 PII rules (`pii/rules.py`, `upi.py`, `address.py`)
Expand a number run into digits (after `double`/`triple`), then classify by digit count `n`:
- `n = 10` starting with 6-9 (optionally prefixed by 91 or 0): `SPOKEN_PHONE`, confidence 0.9.
- `n >= 10` otherwise (account, Aadhaar 12, card 16): `SPOKEN_OTHER_PII`, 0.8.
- `n` in 6-8: PIN or OTP; mute when a cue is within 6 s before the run (0.8), else mark **ambiguous** (0.5).
- `n` in 4-5: ignored unless a cue is active (watch state), then `SPOKEN_OTHER_PII` 0.6.
Cue words open a 6 s **watch window** (carried across segments): `mera number`, `number hai`, `call me`, `whatsapp number`, `mobile number`, `phone number`, `contact`, `otp`, `password`, `pin`, `pincode`, `pin code`, `passcode`, `verification code`, `upi`, `gpay`, `phonepe`, `paytm`, `address`, `ghar ka pata`, `pata`, plus Devanagari equivalents (`नंबर`, `ओटीपी`, `पासवर्ड`, `पता`, `पिन`). Inside a watch window numbers, IDs and addresses get +0.15 confidence.
- **Spoken UPI** (`SPOKEN_UPI`): a spelled or spoken name followed by `at`, `@`, `at the rate`, `at the rate of`, `एट द रेट`, `एट` and a handle (`oksbi`, `okhdfcbank`, `okicici`, `okaxis`, `ybl`, `ibl`, `axl`, `paytm`, `apl`, `upi`, `sbi`, spoken as `ok s b i`, `y b l`, `pay t m`). The span runs from up to 8 tokens before `at` (stopping at a word gap of 0.6 s or more) to the end of the handle. Confidence 0.9 for known handles, 0.6 otherwise.
- **Address** (`SPOKEN_ADDRESS`, 0.6, `audio.address_mute`): cue words (`flat number`, `house number`, `ghar number`, `plot`, `gali`, `mohalla`, `road`, `nagar`, `sector`, `landmark`, `near`, `ke paas`, `pincode`) cover the following up to 12 tokens or until a pause of 0.8 s or more.
- Final category per span from the highest-confidence rule; ambiguous = confidence in [0.4, 0.7) or rule conflict.

### 5.7 LLM fallback (`pii/llm.py`)
Only for ambiguous cases. Groq `openai/gpt-oss-20b`, temperature 0, `max_tokens` 200, timeout `audio.llm_timeout_s = 0.7`, token bucket 25 requests/min burst 3 (limit is 30). Prompt contract: system prompt is fixed (`pii_llm_v1`); the user message is JSON `{"tokens": ["...", ...], "cue_active": bool}` with at most 40 tokens, and the model must answer with JSON matching:
`{"spans": [{"start_token": int, "end_token": int, "category": "phone"|"upi"|"address"|"other_pii"|"none", "confidence": number}]}`.
Token indices are validated against the input length; anything unparsable is a failure. Structured-output support on this model is UNVERIFIED (fall back to plain JSON and strict parsing). **Never log prompts, responses or tokens**; log only counts, latency and categories. On timeout, 429 or parse failure: an ambiguous run that is a number run of 6 or more digits or inside a watch window is treated as PII (mute); otherwise not.

### 5.8 Mute spans (`orchestrator/mute.py`)
`pts_start = first_word.t0 - audio.pad_s`, `pts_end = last_word.t1 + audio.pad_s` (pad 0.2 s). For `approx` words use the covering segment bounds. Merge spans closer than 0.3 s; minimum span 0.3 s; clamp to at least 0. If a PII run touches the end of a segment and its last token is a number word, extend the end by 0.5 s and keep the watch state so leading digits of the next segment are muted. Each span also yields `MaskRequest(kind=MOUTH_OF_SPEAKER, style=BLUR, pts_start/pts_end = span, source="audio", frame_pts = pts_start)`. `mute_mode` is `silence` (default) or `bleep`.

### 5.9 Captions (`captions/`)
- Source: STT text per segment; target language `captions.target_lang`; enabled by `captions.enabled`.
- **Redaction first** (`redact.py`): every token covered by any `MuteSpan` (including failsafe spans) is replaced by `▮▮▮` before translation and display, so PII is never burned into video. Failsafe-muted segments show `▮▮▮` entirely.
- Translate: Sarvam Mayura (code-mixed, 11 languages, at most 1,000 characters) or sarvam-translate (22 languages, at most 2,000 characters), at ₹2 per 1,000 characters; in `demo` IndicTrans2 dist-200M locally (HF gated, needs a token, GPU about 0.9 GB). Timeout 0.8 s.
- Timing (`timing.py`): lines of at most 42 characters, 2 lines, shown at least 1.2 s and at most 4 s; `CaptionEvent(pts_start = first word t0, pts_end = last word t1 + 0.3)`. If translation is not ready by `pts_start + D - 0.3`, `captions.on_late` decides: `skip` (default) or `show_source`.

## 6. Reuse

| Source (`reference/privastream/`) | Target | Change |
|---|---|---|
| `src/models/audio/audio_redaction_server_faster_whisper.py` | `stt/local.py` | keep the faster-whisper call (`vad_filter`, `word_timestamps`, int8 on CPU) and interval merge; redo the char-to-word mapping (it drifts) and add padding; drop Flask and `dslim/bert-base-NER` |
| `training/src/whisper_processor.py` | `stt/local.py` | backend shim and absolute-offset pattern; peak-normalise and DC-removal step |
| `training/src/pii_detector.py` (`_detect_deterministic_pii`, keyword-value regex, overlap merge) | `pii/rules.py`, `pii/merge.py` | keep Luhn, keyword capture, merging; discard its fake linear time estimate and DeBERTa |
| `training/src/pipeline_types.py` | `stt/base.py` | dataclass shape only |
| `src/web/frontend/public/pcm-downsampler-worklet.js` | `app/web` (not here) | n/a |
Do not copy the mediasoup sliding-window code (no lookahead, wall-clock sync); the lessons are encoded above (source PTS stamping, symmetric overlap with lookahead, padding, fail closed). No DeBERTa, no training.

## 7. Edge cases

Number split across a segment boundary; "double/triple"; mixed Hindi and English digits; ASR returns digits as words or numerals; API timeout, 429 or outage (local only, then failsafe); language switch mid-sentence; crosstalk; music under speech (VAD false negatives); the streamer reading a number slowly with gaps over 1.0 s (watch window covers); speech inside the delay window pressed by hold (engine zeroes audio, no span needed); very long monologue (segments chain); clock drift between `arrival` and PTS (use PTS only for spans); empty transcript on a voiced segment (valid result).

## 8. Config keys used

`audio.segment_max_s` 1.5, `audio.overlap_s` 0.4, `audio.pad_s` 0.2, `audio.stt_order` (default `[sarvam, local]`, Groq as failover), `audio.llm_timeout_s` 0.7, `audio.failsafe_mute_voiced` true, `audio.languages`, `captions.*`, `delay_seconds`. Added to `Config` (D-25): `audio.vad.on_threshold` 0.5, `audio.vad.off_threshold` 0.35, `audio.vad.min_speech_ms` 120, `audio.vad.min_silence_ms` 200, `audio.race_hedge_s` 0.8, `audio.stt.sarvam.transport`, `audio.mute_mode`, `audio.address_mute`, `audio.watch_window_s` 6, `audio.llm_rpm` 25, `audio.groq_rpm` 18, `captions.on_late`, `captions.target_lang`.

## 9. Test cases (CPU only; clips from `eval/data_gen/audio`, TTS-composed with known timestamps)

1. VAD and segmenter: a 5 s utterance without pauses yields segments of at most 1.5 s with 0.4 s overlap and a cut at an energy minimum; silence yields none.
2. Normalisation: every digit in all five scripts maps to ASCII; `o k s b i` joins to `oksbi`.
3. Number table: each row of the table in 5.5 (romanised and script) maps to the right digit; `double 9` gives `99`, `triple 8` gives `888`; `plus nine one` is a country-code prefix.
4. Rules: a spoken 10-digit mobile, a 12-digit Aadhaar, a 16-digit card, a 6-digit OTP with and without a cue, a 4-digit number with and without a watch window, each with the expected category and confidence.
5. Split number: digits spanning two segments are merged by the window and muted as one span.
6. Spoken UPI: `rahul nine eight at ok s b i`, `at the rate of ybl`, and the Hindi `एट द रेट` forms; span covers name and handle.
7. Address cues bound at 12 tokens or an 0.8 s pause.
8. LLM: injected stub returns valid, invalid, slow (above 0.7 s) and 429; fallbacks follow 5.7; a log capture contains no prompt or token text.
9. STT race (stubs): Sarvam fast wins; Sarvam slow triggers the hedge at 0.8 s; Sarvam 429 switches to Groq with chunks of at least 3.0 s and at most 18 requests/min; all engines failing leads to a failsafe span at the soft deadline.
10. Deadlines: with stubbed 1.0 s STT every span is ready before `D - 0.3`; with a 5 s stall a failsafe span covers the voiced segment and none covers unvoiced audio.
11. Span maths: padding 0.2 s each side, merge under 0.3 s, minimum 0.3 s, tail extension 0.5 s on a trailing number word, clamp at 0.
12. Captions: a muted span's tokens appear as `▮▮▮` in the caption text; a failsafe segment is fully redacted; late translation follows `on_late`.
13. Privacy: no raw transcript in logs, `SafetyEvent`s or on the ZeroMQ wire (assert the `TRANSCRIPT` topic is `local_only`).
14. **Acceptance T0.6:** spike report (`audio/spikes/sarvam_groq_spike.md`) with Sarvam timestamps yes/no, p50/p95 STT latency, Groq limits; stubs when keys are missing.
15. **Acceptance T3.2/T3.3 (gate G3):** mute coverage at least 90 % of PII words (95 % of digit sequences) inside the delay window; over-mute at most 15 % of non-PII speech seconds; pipeline p95 at most 3.3 s; API-down run mutes 100 % of voiced unverified speech and covers at least 70 % of PII locally; mouth blur on at least 95 % of muted-span frames with an allowlisted face visible.
16. **Acceptance T5.3:** captions arrive before display in all five scripts.

## 10. Latency target

Pipeline from speech start to a ready span at most 3.3 s p95 (segment 1.5 + STT 1.0 + rules 0.02 + LLM 0.7); STT hedge at 0.8 s; translation at most 0.8 s.

## 11. VRAM and CPU target

`dev`: VRAM 0 (APIs plus CPU fallback). `demo`: at most 1.3 GB (faster-whisper small about 0.4-0.6 GB, IndicTrans2 about 0.9 GB). CPU about 0.2 core (0.6 while the local STT fallback runs).

## 12. Tasks

| Task | Wave | Depends | Scope |
|---|---|---|---|
| T0.6 | 0.B | contracts | VAD, segmenter, STT adapters, spike |
| T3.2 | 3 | T0.6 | orchestrator, race, window, spans, failsafe |
| T3.3 | 3 | T0.6 | `pii/` rules, numbers, UPI, address, LLM |
| T5.3 | 5 | T3.2 | captions |
| T7.5 | 7.B | T7.2 | fix misses |

## 13. Out of scope

Training or fine-tuning (no DeBERTa in v1), speaker diarisation, real-time voice changing, audio storage, music and profanity (guard), muting inside the engine (engine applies spans), streaming Sarvam WebSocket unless the spike proves it.
