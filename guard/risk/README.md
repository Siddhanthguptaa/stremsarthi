# guard/risk (T5.2)

Profanity on the transcript stream, risky on-screen text signals, and the risk aggregator that drives the `RiskMeter`.
CPU only, no network, no text stored beyond a 6 s RAM window of normalised tokens. Nothing logs transcript text.

| File | What |
|---|---|
| `lexicon_data.py` | Own Hinglish (Latin and Devanagari), English, Kannada, Tamil, Telugu, Bengali lexicon (L1/L2/L3) and `BENIGN_ALLOW` |
| `profanity.py` | `normalize_token`, `ProfanityMatcher` (whole token, n-grams up to 3, leetspeak, elongation, vowel elision, split words), `ProfanityDetector` (stream adapter, overlap dedupe, 1 signal per 2 s per severity) |
| `sources.py` | `risky_text_signal` (0.5 x confidence, ttl 10 s), `SignalSources` (bus event to `RiskSignal`) |
| `meter.py` | Levels green/yellow/orange/red at 0.25/0.50/0.75 with 0.05 downward hysteresis |
| `aggregator.py` | Decayed signals (half-life ttl/3), bounded noisy-OR, per-source weights, warning `SafetyEvent`s with per-source cooldown, 2 Hz publish plus immediate on new signal |
| `service.py` | `RiskService` / `build(config, bus)`: subscribes transcript, detection, risk; publishes risk, meter, safety event, timing |

Time is stream time (PTS) passed in by the caller, so tests run on a virtual clock.
Music and fingerprint signals (T5.1) arrive as `RiskSignal`s on `guard.risk`; they are refresh-type (one slot per source and label), profanity and slur signals accumulate.

Tests: `python -m pytest guard/tests/test_guard_risk_profanity.py guard/tests/test_guard_risk_aggregator.py`
