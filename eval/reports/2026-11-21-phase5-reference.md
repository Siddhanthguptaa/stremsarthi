# phase5-reference report (2026-11-21)

Result: **PASS**

## Run

- commands: ["(check)", "python -m eval.phase5 --data /tmp/ss_guard_data --out /tmp/ss_p5_finalref --leg data", "python -m eval.phase5 --data /tmp/ss_guard_data --out /tmp/ss_p5_finalref --leg music --reference", "python -m eval.phase5 --data /tmp/ss_guard_data --out /tmp/ss_p5_finalref --leg fingerprint --reference", "python -m eval.phase5 --data /tmp/ss_guard_data --out /tmp/ss_p5_finalref --leg profanity --reference", "python -m eval.phase5 --data /tmp/ss_guard_data --out /tmp/ss_p5_finalref --leg meter --reference", "python -m eval.phase5 --data /tmp/ss_guard_data --out /tmp/ss_p5_finalref --leg captions_render --reference", "python -m eval.phase5 --data /tmp/ss_guard_data --out /tmp/ss_p5_finalref --leg captions_timing --reference"]
- dataset: {"clips_by_kind": {"fp_negative": 10, "fp_positive": 40, "fp_tempo": 8, "music_only": 20, "music_under_speech": 10, "speech_only": 20}, "library_tracks": 5, "utterances_by_group": {"clean": 26, "clean_trap": 22, "english": 30, "hinglish_deva": 50, "hinglish_latn": 90, "regional": 12, "variants": 24}}
- fingerprint_backend: reference
- git_rev: 8a1e6e353205
- gpu: not used (CPU only)
- hardware: {"cpu": "13th Gen Intel(R) Core(TM) i5-13450HX", "gpu": "NVIDIA GeForce RTX 3050 6GB Laptop GPU, 581.86"}
- music_backend: reference
- profile: dev
- seed: 1234
- stand_ins: reference stand-ins (harness validation, not product numbers)
- tts_engine: flite

## Checks

| check | value | target | result |
|---|---|---|---|
| data: problems found | 0 | == 0 | PASS |
| music detect recall (music only + under speech) | 1 | >= 0.9 | PASS |
| music detect false positives on speech-only clips | 0 | <= 0.05 | PASS |
| fingerprint match, correct id, noisy 5 s snippets | 1 | >= 0.9 | PASS |
| fingerprint wrong id on library snippets | 0 | <= 0 | PASS |
| fingerprint false match on unrelated music | 0 | <= 0.05 | PASS (spec test 3; eval-chosen bound) |
| Hinglish profanity recall (Latin + Devanagari) | 1 | >= 0.85 | PASS |
| profanity false positives on clean speech | 0 | <= 0.03 | PASS (eval-chosen bound (as chat)) |
| profanity false positives on harmless look-alikes | 0 | <= 0 | PASS (spec test 5: benign_allow gives zero hits) |
| profanity signal within 0.6 s of the word | 1 | >= 0.95 | PASS |
| profanity processing p95 | 4.7e-05 s | <= 0.5 s | PASS (spec 10) |
| risk meter reaction to an injected signal, max over scenarios | 0 s | <= 2 s | PASS |
| risk meter reaches the expected level | 1 | >= 1 | PASS |
| risk meter publication rate while a signal is live | 2 Hz | >= 1.8 Hz | PASS (spec test 9: 2 Hz within 10 %) |
| risk meter publication rate upper bound | 2 Hz | <= 2.2 Hz | PASS |
| captions render and shape correctly in scripts | 5 | >= 5 | PASS (of 5: shaping vs raqm reference, and burn-in placement/window/toggle) |
| libraqm in use | 1 | >= 1 | PASS |
| captions published after their display time | 0 | == 0 | PASS (23 published for 24 segments) |
| segments that got a caption (not skipped as late) | 0.9583 | >= 0.75 | PASS (eval-chosen bound; a service that skips everything must not pass) |
| muted words shown in a caption | 0 | == 0 | PASS |

## Music detection

| set | result |
|---|---|
| music only | 20/20 = 1.0 (CI 0.8389-1.0) |
| music under speech (0 dB) | 10/10 = 1.0 (CI 0.7225-1.0) |
| speech only (false positives) | 0/20 = 0.0 (CI 0.0-0.1611) |
|   speech, noise=clean | 0/7 = 0.0 (CI 0.0-0.3543) |
|   speech, noise=pink | 0/7 = 0.0 (CI 0.0-0.3543) |
|   speech, noise=white | 0/6 = 0.0 (CI 0.0-0.3903) |
| music ON latency from music start (informational) | n=30 p50=1.9 p95=1.9 max=1.9 s |

## Fingerprint (5 s snippets, 10 dB pink noise)

| set | result |
|---|---|
| correct id | 40/40 = 1.0 (CI 0.9124-1.0) |
| wrong id | 0/40 = 0.0 (CI 0.0-0.0876) |
| false match (unrelated) | 0/10 = 0.0 (CI 0.0-0.2775) |
|   codec mp3_128k | 20/20 = 1.0 (CI 0.8389-1.0) |
|   codec none | 20/20 = 1.0 (CI 0.8389-1.0) |
| 15 % tempo change matched (documented non-match) | 0/8 = 0.0 (CI 0.0-0.3244) |
| match time ms p50 / p95 | 0.0 / 0.002 |

## Profanity (transcript level)

| group | recall / rate |
|---|---|
| english | 30/30 = 1.0 (CI 0.8865-1.0) |
| hinglish_deva | 50/50 = 1.0 (CI 0.9286-1.0) |
| hinglish_latn | 90/90 = 1.0 (CI 0.9591-1.0) |
| regional | 12/12 = 1.0 (CI 0.7575-1.0) |
| variants | 24/24 = 1.0 (CI 0.862-1.0) |
| Hinglish severity 1 | 53/53 = 1.0 (CI 0.9324-1.0) |
| Hinglish severity 2 | 87/87 = 1.0 (CI 0.9577-1.0) |
| clean false positives | 0/48 = 0.0 (CI 0.0-0.0741) |
| harmless look-alike false positives | 0/22 = 0.0 (CI 0.0-0.1487) |
| signal time ok | 206/206 = 1.0 (CI 0.9817-1.0) |

## Risk meter (virtual clock)

| scenario | expected | peak | reaction s | back to green s | warnings |
|---|---|---|---|---|---|
| music_p0.8 | yellow | yellow | 0 | 2 | n/a |
| fingerprint_match | red | red | 0 | n/a | n/a |
| profanity_L2_transcript | yellow | yellow | 0 | 6 | n/a |
| risky_text | yellow | yellow | 0 | 2 | n/a |

## Caption rendering

| lang | script | shaping vs raqm reference | burn-in checks |
|---|---|---|---|
| bn | Bengali | 2/2 = 1.0 (CI 0.3424-1.0) | 2/2 = 1.0 (CI 0.3424-1.0) |
| hi | Devanagari | 3/3 = 1.0 (CI 0.4385-1.0) | 3/3 = 1.0 (CI 0.4385-1.0) |
| kn | Kannada | 2/2 = 1.0 (CI 0.3424-1.0) | 2/2 = 1.0 (CI 0.3424-1.0) |
| ta | Tamil | 2/2 = 1.0 (CI 0.3424-1.0) | 2/2 = 1.0 (CI 0.3424-1.0) |
| te | Telugu | 2/2 = 1.0 (CI 0.3424-1.0) | 2/2 = 1.0 (CI 0.3424-1.0) |

## Caption arrival

| metric | value |
|---|---|
| segments / published | 24 / 23 |
| covered segments | 23/24 = 0.9583 (CI 0.7976-0.9926) |
| margin s (display minus publish) min / p5 / p50 | 0.416 / 0.68 / 1.25 |
| service stats | {} |

## Gate steps

| step | raw exit | gate exit | seconds |
|---|---|---|---|
| generate guard set | 0 | 0 | 0 |
| data check | 0 | 0 | 3.3 |
| music detection | 0 | 0 | 3 |
| fingerprint | 0 | 0 | 3.1 |
| profanity | 0 | 0 | 3.3 |
| risk meter | 0 | 0 | 3.1 |
| captions render | 0 | 0 | 4.4 |
| captions timing | 0 | 0 | 3.4 |

## Notes

- ALL AUDIO IS SYNTHETIC. Songs are seeded numpy synthesis (pads, lead, bass, kick and hats; eval/data_gen/guard/songs.py, independent of guard.music.synth) and speech is offline English flite TTS. Music recall and fingerprint numbers therefore say nothing about real commercial music, covers or live versions; they test the pipeline and thresholds.
- Profanity is scored at transcript level: the offline TTS cannot say Hinglish or regional profanity and the guard reads the transcript anyway. Word times are exact by construction. The regional and variant groups are informational.
- The risk-meter leg injects signals on a virtual clock (no real time passes): it measures the aggregator and its hysteresis, not the audio path before it. Music-ON latency (clip start to ON) is reported separately and is informational.
- Caption shaping compares the engine mask with an independent Pillow+libraqm reference on separate Noto font files; the arrival leg runs the real CaptionService with a stand-in translation provider (modelled latency 0.15 to 0.5 s) and a monotonic virtual capture clock, not a live Sarvam call.
- No GPU is used by any leg of this gate (the guard is CPU only), so no GPU lock is taken.
