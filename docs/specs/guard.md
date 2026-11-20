# Spec: `guard/`

Status: approved plan, section 7. Contracts: `contracts/`. Budgets: `docs/PLAN.md` section 2.5.

## 1. Purpose and owner

The strike guard warns the streamer before something gets the stream struck, muted or demonetised: music playing, a fingerprint match against a demo library, profanity in speech, risky text on screen. It emits `RiskSignal`s and a risk meter; it does not block or mute anything in v1. It never claims Content ID parity. Owner agent: `audio-engineer`.

## 2. Directories owned and file layout

Edit only `guard/` (and `audio/`, see `audio.md`). Tests in `guard/tests/`.

```
guard/
  music/detector.py       # YAMNet gate                                           T5.1
  music/fingerprint.py    # Chromaprint matching                                  T5.1
  music/library.py  music/build_library.py
  music/library/fingerprints.json   # fingerprints and ids only, no audio          T5.1
  profanity/lexicon.py  matcher.py  data/lexicon.yaml                              T5.2
  risk/aggregator.py  sources.py  meter.py                                         T5.2
```
Demo song audio lives in `eval/data/private/music/` (gitignored, user supplied; short snippets, never committed). The repo holds fingerprints and ids only.

**Added to `contracts/events.py` (D-25):** the risk meter is not a `RiskSignal`; see `RiskMeter(wall_ts, level: Literal["green","yellow","orange","red"], score: float, top_sources: tuple[str, ...])` to `contracts/events.py` and `Topic.RISK_METER` (`guard.meter`); the API fans it out. Guard must not be imported by `app/api`.

## 3. Inputs

16 kHz audio tap (in-process); the `local_only` transcript stream from `audio/` (words with `t0/t1`, post-redaction ids); `Detection` stream (category `RISKY_TEXT`, from `vision/`); `Config.guard`.

## 4. Outputs

`RiskSignal(source, category, score, ttl_s, label)`; `RiskMeter` at 2 Hz; `SafetyEvent(modality=GUARD, action="warned")` on level increases; `StageTiming`. Labels are codes (`music`, `fp:demo_03`, `profanity:L2`), never transcript text.

## 5. Behaviour

### 5.1 Music detection (`music/detector.py`)
- YAMNet (Apache-2.0), tflite or ONNX, CPU, 16 kHz mono, 0.96 s windows with 0.48 s hop. Resolve class indices by name from `yamnet_class_map.csv` (`Music`; speech class used only for diagnostics); indices are not hard-coded (UNVERIFIED).
- `music_score = P(Music)`, smoothed by an EMA over 5 windows (alpha 0.4). State machine: ON when `score >= 0.5` for 3 consecutive windows (about 1.4 s), OFF when `score < 0.3` for 3 windows. Music under speech is the normal case: do not suppress when speech is also present.
- While ON emit `RiskSignal(source="music", category=MUSIC, score=min(1, score), ttl_s=10, label="music")` every second.

### 5.2 Fingerprint match (`music/fingerprint.py`)
- While music is ON keep an 8 s rolling buffer. Compute a Chromaprint raw fingerprint (algorithm 2, 32-bit items, about 8 items per second) with `ffmpeg -f chromaprint -algorithm 2 -fp_format raw` (Ubuntu ffmpeg is built with `--enable-chromaprint`; verified on the dev laptop) or `pyacoustid`; audfprint (MIT) is the fallback if chromaprint is unavailable.
- Match: for each library track slide the query (about 64 items) over the track's items; per offset compute `BER = popcount(query xor ref) / 32` averaged over the overlap, using a numpy lookup table. Match if best mean BER at most 0.35 over at least 5 s of overlap AND the second-best track is at least 0.05 worse (thresholds UNVERIFIED, tune on eval). Re-run every 2 s while ON; cache the verdict per track for 30 s.
- On match emit `RiskSignal(source="fingerprint", category=COPYRIGHT_MATCH, score=0.95, ttl_s=60, label="fp:<id>")`.
- Library builder: `python -m guard.music.build_library --src eval/data/private/music --out guard/music/library/fingerprints.json` stores track id (`demo_01`..), duration, fingerprint array. About 5 tracks x 4 minutes is about 10,000 items total; a full scan is a few milliseconds.
- Limits to document: Chromaprint tolerates noise and compression but not large pitch or tempo changes, covers or live versions. This is a demo library matcher.

### 5.3 Profanity (`profanity/`)
- Input: transcript words. Normalise (lowercase, NFKC, collapse repeated letters beyond 2, map common leetspeak, strip vowels-elision variants) and match against `lexicon.yaml`: Hinglish in Latin letters, Devanagari, English, with three severities (L1 mild, L2 strong, L3 slur) and a `benign_allow` list (words that contain a profane substring but are harmless, for example place names) to prevent the Scunthorpe problem. Match on token n-grams (up to 3), whole-token only.
- Content of the lexicon is written by `T5.2` from public word lists plus the team's review; the spec does not reproduce slurs.
- Emit `RiskSignal(source="profanity", category=PROFANITY, score=0.3|0.6, ttl_s=30)` for L1/L2, and `source="slur", score=0.9, ttl_s=120` for L3. Rate limit: one signal per 2 s per severity.

### 5.4 Risky on-screen text (`risk/sources.py`)
Each `Detection(category=RISKY_TEXT)` becomes `RiskSignal(source="risky_text", score=0.5 * confidence, ttl_s=10, label="risky_text")`. The blocklist is `guard.risky_text_blocklist` and the matching happens in `vision/detectors/text` (guard never sees OCR text).

### 5.5 Risk aggregator (`risk/aggregator.py`)
Each active signal `i` contributes `c_i(t) = s_i * 2^(-(t - t_i) / h_i)`, with `h_i = ttl_i / 3` and the signal dropped when `t - t_i > ttl_i` (about 12.5 % left at expiry). Per-source weight `w`: fingerprint 1.0, slur 0.9, risky_text 0.6, profanity 0.5, music 0.35. The meter score is a bounded noisy-OR:

`score = 1 - product_i (1 - w_src(i) * c_i)`

Levels: green below 0.25, yellow 0.25-0.50, orange 0.50-0.75, red at least 0.75, with 0.05 hysteresis on the way down (a level is left only when the score falls 0.05 below its lower bound). Updated at 2 Hz and on every new signal. Warnings: on a level increase emit `SafetyEvent(action="warned", category=<top contributor's category>, detail_code=<top source>)`; per-source cooldown 20 s. Worked examples (for tests): music only with `P(Music) = 0.8` gives 0.35 * 0.8 = 0.28 (yellow); a fingerprint match gives 1.0 * 0.95 = 0.95 (red); one L2 profanity hit gives 0.5 * 0.6 = 0.30 (yellow), and with `h = 10 s` it falls below 0.25 at about 2.6 s but, with the 0.05 hysteresis, returns to green only when it is below 0.20, at about 5.9 s (test 8).

### 5.6 Timing
Detection runs on pre-delay audio, so warnings reach the streamer about 4 s before viewers hear the content. Music presence (yellow) must appear within 2 s of music starting (YAMNet 3 windows about 1.4 s plus aggregation); a fingerprint match needs at least 5 s of music and arrives later; the gate measures both.

## 6. Reuse

No code from `reference/` applies: PrivaStream and IRL Shield have no music, fingerprint or risk code. Building blocks come from public libraries: YAMNet (Apache-2.0), Chromaprint (LGPL-2.1, used through ffmpeg or `pyacoustid`, MIT wrapper), audfprint (MIT) fallback. Dejavu is not used (needs a SQL database, looks unmaintained). Log third-party model files and the profanity word-list sources in `docs/THIRD_PARTY.md`.

## 7. Edge cases

Music under loud speech; very short music snippets (under 5 s: no fingerprint, `music` signal only); library track played at different tempo or pitch (no match); a profane word inside a benign word; profanity in a language not in the lexicon (no signal); transcript delay (signals are timestamped by `pts`, not arrival); fingerprint of silence or noise (reject items with near-zero entropy); repeated signals stacking (noisy-OR is bounded); clock mismatch between sources (use stream time).

## 8. Config keys used

`guard.music_on_threshold` 0.5, `guard.music_off_threshold` 0.3, `guard.fp_max_ber` 0.35, `guard.fp_margin` 0.05, `guard.profanity_enabled`, `guard.risky_text_blocklist`, `guard.warn_cooldown_s` 20, `guard.meter_hz` 2, `guard.weights.*`. (All are `GuardConfig` fields.)

## 9. Test cases (CPU only; clips from `eval/data_gen/guard`)

1. YAMNet gate: on 20 music clips and 20 speech clips, music recall at least 90 % and false positives at most 5 %; music mixed under speech at 0 dB SNR still turns ON.
2. State machine: ON needs 3 consecutive windows; flicker below that does not toggle; OFF needs 3 low windows.
3. Fingerprint: the demo library matches 5 s noisy snippets (SNR 10 dB, MP3 128 kbps) at least 90 % of the time, with the correct id; unrelated music gives no match; a 15 % tempo change does not match (documented limit).
4. Library builder is deterministic (same input gives same JSON) and the JSON contains no audio samples.
5. Profanity: Hinglish Latin, Devanagari and English test lists reach recall at least 85 %; the `benign_allow` list produces zero hits; repeated-letter and leetspeak variants match.
6. Risky text: a `Detection(RISKY_TEXT, conf 0.8)` yields a signal of 0.4 with ttl 10 s; guard code never receives text.
7. Aggregator maths: single music signal gives yellow; fingerprint gives red; two L2 hits within 3 s give a higher score than one; a signal is dropped after ttl; score never exceeds 1.
8. Hysteresis and cooldown: after an L2 hit (0.30) the level returns to green only when the score is below 0.20; the same source cannot warn twice within 20 s.
9. Meter publishes at 2 Hz (within 10 %) and immediately on a new signal.
10. Privacy: no transcript text and no audio in `RiskSignal`, `SafetyEvent` or logs.
11. **Acceptance T5.1/T5.2/T5.5 (gate G5):** music recall at least 90 % with false positives at most 5 %; fingerprint at least 90 % on noisy 5 s snippets; Hinglish profanity recall at least 85 %; meter reacts to music within 2 s.

## 10. Latency target

Music ON at most 2 s after music starts; profanity signal within 0.5 s of the transcript word; fingerprint match at most 7 s after music starts; meter update at most 0.5 s.

## 11. VRAM and CPU target

VRAM 0 (CPU only). CPU: YAMNet at most 5 ms per 0.96 s window (est.), fingerprint at most 10 ms per scan, total under 0.1 core.

## 12. Tasks

| Task | Wave | Depends | Scope |
|---|---|---|---|
| T5.1 | 5 | G3 | `music/` (YAMNet gate, fingerprint, library builder) |
| T5.2 | 5 | G3 | `profanity/`, `risk/` aggregator and sources |
| T5.5 | 5 | G3 | eval data and `gate_phase5.sh` (owner: eval-engineer) |

## 13. Out of scope

Automatic muting or removal of music, Content ID parity, cover or live-version matching, detecting video copyright, NSFW or weapon detection, fine-tuned toxicity models, storing audio or transcripts, platform strike prediction beyond the meter.
