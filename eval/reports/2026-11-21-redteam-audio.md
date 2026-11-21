# redteam-audio report (2026-11-21)

Result: **FAIL**

## Run

- clips: 78
- defect: 
- git_rev: 8a1e6e353205
- hardware: {"cpu": "13th Gen Intel(R) Core(TM) i5-13450HX", "gpu": "NVIDIA GeForce RTX 3050 6GB Laptop GPU, 581.86"}
- per_technique: 2
- profile: dev
- reference: False
- seed: 7771
- suite: redteam-audio
- tts_engine: flite
- wall_s: 196

## Checks

| check | value | target | result |
|---|---|---|---|
| miss rate: audio digits_fillers (PII words leaked) | 0.02941 | <= 0.1 | PASS (2/68 words, 1/6 spans leaked) |
| miss rate: audio code_mixed_numbers (PII words leaked) | 0.1569 | <= 0.1 | FAIL (8/51 words, 2/6 spans leaked) |
| miss rate: audio letter_spelling (PII words leaked) | 0.4959 | <= 0.1 | FAIL (60/121 words, 2/4 spans leaked) |
| miss rate: audio read_across_pauses (PII words leaked) | 0.2167 | <= 0.1 | FAIL (13/60 words, 3/6 spans leaked) |
| miss rate: audio whisper_noisy (PII words leaked) | 0 | <= 0.1 | PASS (0/80 words, 0/8 spans leaked) |
| miss rate: audio fast_speech (PII words leaked) | 0.2667 | <= 0.1 | FAIL (16/60 words, 3/6 spans leaked) |
| miss rate: audio overlap_speakers (PII words leaked) | 0.2203 | <= 0.1 | FAIL (13/59 words, 4/6 spans leaked) |
| miss rate: audio background_music (PII words leaked) | 0 | <= 0.1 | PASS (0/60 words, 0/6 spans leaked) |
| miss rate: audio split_segments (PII words leaked) | 0.03846 | <= 0.1 | PASS (2/52 words, 2/6 spans leaked) |
| miss rate: audio upi_phonetic (PII words leaked) | 0.2143 | <= 0.1 | FAIL (9/42 words, 4/4 spans leaked) |
| miss rate: audio stt_failure (PII words leaked) | 0.1849 | <= 0.05 | FAIL (22/119 words, 4/12 spans leaked) |
| miss rate: audio asr_drops (PII words leaked) | 0.275 | <= 0.1 | FAIL (22/80 words, 6/8 spans leaked) |

## Leak rate by category (PII words not muted in the output audio)

| category | clips | PII words | leaked | leak rate | spans with a leak | ceiling |
|---|---|---|---|---|---|---|
| digits_fillers | 6 | 68 | 2 | 2.9% (2/68, CI 1%-10%) | 1/6 | <= 10% |
| code_mixed_numbers | 6 | 51 | 8 | 15.7% (8/51, CI 8%-28%) | 2/6 | <= 10% |
| letter_spelling | 4 | 121 | 60 | 49.6% (60/121, CI 41%-58%) | 2/4 | <= 10% |
| read_across_pauses | 6 | 60 | 13 | 21.7% (13/60, CI 13%-34%) | 3/6 | <= 10% |
| whisper_noisy | 8 | 80 | 0 | 0.0% (0/80, CI 0%-5%) | 0/8 | <= 10% |
| fast_speech | 6 | 60 | 16 | 26.7% (16/60, CI 17%-39%) | 3/6 | <= 10% |
| overlap_speakers | 6 | 59 | 13 | 22.0% (13/59, CI 13%-34%) | 4/6 | <= 10% |
| background_music | 6 | 60 | 0 | 0.0% (0/60, CI 0%-6%) | 0/6 | <= 10% |
| split_segments | 6 | 52 | 2 | 3.8% (2/52, CI 1%-13%) | 2/6 | <= 10% |
| upi_phonetic | 4 | 42 | 9 | 21.4% (9/42, CI 12%-36%) | 4/4 | <= 10% |
| stt_failure | 12 | 119 | 22 | 18.5% (22/119, CI 13%-26%) | 4/12 | <= 5% |
| asr_drops | 8 | 80 | 22 | 27.5% (22/80, CI 19%-38%) | 6/8 | <= 10% |

## Leak rate by technique

| category/technique | clips | PII words | leaked | word leak rate | digit leak rate | why (no_span: nothing muted; partial_span; late_span: ready after the samples left) |
|---|---|---|---|---|---|---|
| digits_fillers/fillers_every_2_digits | 2 | 20 | 2 | 0.1 | 0.1 | {"no_span": 2} |
| digits_fillers/fillers_random | 2 | 20 | 0 | 0 | 0 | {} |
| digits_fillers/restart_correction | 2 | 28 | 0 | 0 | 0 | {} |
| code_mixed_numbers/mixed_language_digits | 2 | 20 | 0 | 0 | 0 | {} |
| code_mixed_numbers/english_pair_words | 2 | 13 | 8 | 0.6154 | 0.6154 | {"partial_span": 3, "no_span": 5} |
| code_mixed_numbers/double_triple_heavy | 2 | 18 | 0 | 0 | 0 | {} |
| letter_spelling/upi_letter_by_letter | 2 | 43 | 0 | 0 | 0 | {} |
| letter_spelling/upi_z_for_zebra | 2 | 78 | 60 | 0.7692 | 0 | {"no_span": 56, "partial_span": 2, "late_span": 2} |
| read_across_pauses/pair_gaps_1p5s | 2 | 20 | 12 | 0.6 | 0.6 | {"no_span": 12} |
| read_across_pauses/halves_gap_3s | 2 | 20 | 1 | 0.05 | 0.05 | {"partial_span": 1} |
| read_across_pauses/every_digit_gap_0p9s | 2 | 20 | 0 | 0 | 0 | {} |
| whisper_noisy/whisper_quiet | 2 | 20 | 0 | 0 | 0 | {} |
| whisper_noisy/white_noise_0db | 2 | 20 | 0 | 0 | 0 | {} |
| whisper_noisy/babble_0db | 2 | 20 | 0 | 0 | 0 | {} |
| whisper_noisy/far_field_quiet | 2 | 20 | 0 | 0 | 0 | {} |
| fast_speech/speed_1p6 | 2 | 20 | 0 | 0 | 0 | {} |
| fast_speech/speed_2p0 | 2 | 20 | 1 | 0.05 | 0.05 | {"partial_span": 1} |
| fast_speech/speed_2p4 | 2 | 20 | 15 | 0.75 | 0.75 | {"no_span": 15} |
| overlap_speakers/overlap_chatter | 2 | 19 | 10 | 0.5263 | 0.5263 | {"late_span": 1, "no_span": 7, "partial_span": 2} |
| overlap_speakers/overlap_other_digits | 2 | 20 | 0 | 0 | 0 | {} |
| overlap_speakers/overlap_louder_6db | 2 | 20 | 3 | 0.15 | 0.15 | {"partial_span": 1, "late_span": 2} |
| background_music/music_snr_8db | 2 | 20 | 0 | 0 | 0 | {} |
| background_music/music_snr_0db | 2 | 20 | 0 | 0 | 0 | {} |
| background_music/music_snr_minus6db | 2 | 20 | 0 | 0 | 0 | {} |
| split_segments/split_gap_2p5s | 2 | 20 | 0 | 0 | 0 | {} |
| split_segments/split_with_chatter | 2 | 20 | 0 | 0 | 0 | {} |
| split_segments/otp_halves_gap_4s | 2 | 12 | 2 | 0.1667 | 0.1667 | {"no_span": 2} |
| upi_phonetic/handle_spelled_ok_s_b_i | 2 | 20 | 3 | 0.15 | 0 | {"partial_span": 2, "late_span": 1} |
| upi_phonetic/dot_underscore_at | 2 | 22 | 6 | 0.2727 | 0 | {"no_span": 1, "partial_span": 2, "late_span": 3} |
| stt_failure/primary_429_mid_sentence | 2 | 20 | 0 | 0 | 0 | {} |
| stt_failure/primary_hang_mid_sentence | 2 | 19 | 1 | 0.05263 | 0.05263 | {"partial_span": 1} |
| stt_failure/empty_transcripts_mid_sentence | 2 | 20 | 20 | 1 | 1 | {"no_span": 20} |
| stt_failure/both_fail_mid_sentence | 2 | 20 | 0 | 0 | 0 | {} |
| stt_failure/primary_down_whole_clip | 2 | 20 | 1 | 0.05 | 0.05 | {"partial_span": 1} |
| stt_failure/both_down_whole_clip | 2 | 20 | 0 | 0 | 0 | {} |
| asr_drops/drop_10pct | 2 | 20 | 1 | 0.05 | 0.05 | {"partial_span": 1} |
| asr_drops/drop_25pct | 2 | 20 | 3 | 0.15 | 0.15 | {"no_span": 2, "partial_span": 1} |
| asr_drops/drop_50pct | 2 | 20 | 8 | 0.4 | 0.4 | {"no_span": 5, "partial_span": 2, "late_span": 1} |
| asr_drops/mishear_30pct | 2 | 20 | 10 | 0.5 | 0.5 | {"no_span": 8, "partial_span": 2} |

## Leaking clips (31)

| case id | leaked/PII words | why | late spans | failsafe spans |
|---|---|---|---|---|
| rt-digits_fillers-fillers_every_2_digits-0 | 2/10 | {"no_span": 2} | 0 | 0 |
| rt-code_mixed_numbers-english_pair_words-0 | 5/7 | {"partial_span": 2, "no_span": 3} | 0 | 0 |
| rt-code_mixed_numbers-english_pair_words-1 | 3/6 | {"no_span": 2, "partial_span": 1} | 0 | 0 |
| rt-letter_spelling-upi_z_for_zebra-0 | 28/39 | {"no_span": 25, "partial_span": 1, "late_span": 2} | 1 | 0 |
| rt-letter_spelling-upi_z_for_zebra-1 | 32/39 | {"no_span": 31, "partial_span": 1} | 0 | 0 |
| rt-read_across_pauses-pair_gaps_1p5s-0 | 6/10 | {"no_span": 6} | 0 | 0 |
| rt-read_across_pauses-pair_gaps_1p5s-1 | 6/10 | {"no_span": 6} | 0 | 0 |
| rt-read_across_pauses-halves_gap_3s-0 | 1/10 | {"partial_span": 1} | 0 | 0 |
| rt-fast_speech-speed_2p0-1 | 1/10 | {"partial_span": 1} | 0 | 0 |
| rt-fast_speech-speed_2p4-0 | 5/10 | {"no_span": 5} | 0 | 0 |
| rt-fast_speech-speed_2p4-1 | 10/10 | {"no_span": 10} | 0 | 0 |
| rt-overlap_speakers-overlap_chatter-0 | 6/9 | {"late_span": 1, "no_span": 5} | 0 | 0 |
| rt-overlap_speakers-overlap_chatter-1 | 4/10 | {"partial_span": 2, "no_span": 2} | 0 | 0 |
| rt-overlap_speakers-overlap_louder_6db-0 | 1/10 | {"partial_span": 1} | 0 | 0 |
| rt-overlap_speakers-overlap_louder_6db-1 | 2/10 | {"late_span": 2} | 0 | 0 |
| rt-split_segments-otp_halves_gap_4s-0 | 1/6 | {"no_span": 1} | 0 | 0 |
| rt-split_segments-otp_halves_gap_4s-1 | 1/6 | {"no_span": 1} | 0 | 0 |
| rt-upi_phonetic-handle_spelled_ok_s_b_i-0 | 2/11 | {"partial_span": 1, "late_span": 1} | 1 | 0 |
| rt-upi_phonetic-handle_spelled_ok_s_b_i-1 | 1/9 | {"partial_span": 1} | 0 | 0 |
| rt-upi_phonetic-dot_underscore_at-0 | 4/11 | {"no_span": 1, "partial_span": 2, "late_span": 1} | 1 | 0 |
| rt-upi_phonetic-dot_underscore_at-1 | 2/11 | {"late_span": 2} | 1 | 0 |
| rt-stt_failure-primary_hang_mid_sentence-0 | 1/9 | {"partial_span": 1} | 0 | 0 |
| rt-stt_failure-empty_transcripts_mid_sentence-0 | 10/10 | {"no_span": 10} | 0 | 0 |
| rt-stt_failure-empty_transcripts_mid_sentence-1 | 10/10 | {"no_span": 10} | 0 | 0 |
| rt-stt_failure-primary_down_whole_clip-0 | 1/10 | {"partial_span": 1} | 0 | 0 |
| rt-asr_drops-drop_10pct-0 | 1/10 | {"partial_span": 1} | 0 | 0 |
| rt-asr_drops-drop_25pct-1 | 3/10 | {"no_span": 2, "partial_span": 1} | 0 | 0 |
| rt-asr_drops-drop_50pct-0 | 6/10 | {"no_span": 5, "partial_span": 1} | 0 | 0 |
| rt-asr_drops-drop_50pct-1 | 2/10 | {"partial_span": 1, "late_span": 1} | 1 | 0 |
| rt-asr_drops-mishear_30pct-0 | 5/10 | {"no_span": 3, "partial_span": 2} | 0 | 0 |
| rt-asr_drops-mishear_30pct-1 | 5/10 | {"no_span": 5} | 0 | 0 |

## Context

| metric | value |
|---|---|
| clips | 78 |
| audio seconds | 696.2 |
| mean over-mute of non-PII speech | 0.164 |
| VAD | ['silero'] |

## Notes

- THE RECOGNISER IS THE PHASE-3 ORACLE: it answers from the truth words with seeded errors, so these numbers measure VAD, segmentation, PII rules, merging, the fail-safe and mute timing under attack, not recognition. Overlap, whisper and ASR-drop attacks are modelled by hiding words from the oracle.
- The offline TTS is English flite voices: Hindi and Hinglish are respelled; no native Indic phonetics. Compound Hindi number words and whispered or far-field speech are not real here (level and noise are applied to the English TTS).
- Leak = under 90 % of a PII word is below -50 dBFS in the 48 kHz output after engine.audio_mute.MuteBook applied the spans that were ready in time (delay 4.0 s). One leaked digit counts as one leaked word; the 'spans with a leak' column is the number-level view.
- Failure-injection windows are placed on the clip time of the first PII span; 'hang' never answers, '429' and '500' raise the provider errors, 'empty' returns a transcript with no words.
