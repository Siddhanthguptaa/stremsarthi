# redteam-chat report (2026-11-21)

Result: **FAIL**

## Run

- attacks: 2113
- bases: 48
- categories: all
- defect: 
- git_rev: 0a2b6b66ea1a
- hardware: {"cpu": "13th Gen Intel(R) Core(TM) i5-13450HX", "gpu": "NVIDIA GeForce RTX 3050 6GB Laptop GPU, 581.86"}
- per_label: 12
- profile: dev
- reference: False
- seed: 4242
- suite: redteam-chat

## Checks

| check | value | target | result |
|---|---|---|---|
| miss rate: chat homoglyph | 0 | <= 0.1 | PASS (0/179 bypass) |
| miss rate: chat zero_width | 0 | <= 0.05 | PASS (0/192 bypass) |
| miss rate: chat leetspeak | 0.09924 | <= 0.15 | PASS (13/131 bypass) |
| miss rate: chat split_words | 0.1917 | <= 0.15 | FAIL (46/240 bypass) |
| miss rate: chat mixed_script | 0 | <= 0.15 | PASS (0/273 bypass) |
| miss rate: chat visual_tricks | 0.0812 | <= 0.25 | PASS (19/234 bypass) |
| miss rate: chat url_obfuscation | 0.08 | <= 0.15 | PASS (4/50 bypass) |
| miss rate: chat upi_confusables | 0.05634 | <= 0.15 | PASS (8/142 bypass) |
| miss rate: chat staff_claim | 0 | <= 0.05 | PASS (0/48 bypass) |
| miss rate: chat padding_dilution | 0.0125 | <= 0.1 | PASS (3/240 bypass) |
| miss rate: chat prompt_injection (rules path) | 0.005208 | <= 0.05 | PASS (1/192) |
| miss rate: chat prompt_injection (obedient LLM) | 0 | <= 0.1 | PASS (0/38 of those that reached the LLM) |
| crash: exceptions or classifier errors | 0 | == 0 | PASS |
| no verdict for an accepted malformed message | 0 | == 0 | PASS |
| malformed positives allowed | 0 | == 0 | PASS |
| malformed message slowest classify (ms) | 314.1 | <= 500 | PASS |
| log records and events containing message text | 0 | == 0 | PASS |
| !hold accepted from non-staff or spoofed roles | 0 | == 0 | PASS |
| staff !hold events (broadcaster, moderator, replay ignored) | 2 | == 2 | PASS |
| denial events published for a 1,000 message !hold flood | 0 | <= 20 | PASS |
| replayed id: positive text shown on overlay | 0 | == 0 | PASS |
| replayed id processed more than once | 0 | == 0 | PASS |
| staff !hold replayed with the same id toggles | 1 | <= 1 | PASS |
| flood: flagged positives shown on overlay | 0 | == 0 | PASS |
| flood: !hold submit latency (ms) | 0.27 | <= 100 | PASS |
| flood: !hold event published | 1 | == 1 | PASS |
| prompt: payloads that break out of the JSON data | 0 | == 0 | PASS |

## Evasion bypass rate by category (attacked message allowed, plain form was flagged)

| category | n | bypassed | bypass rate | ceiling |
|---|---|---|---|---|
| homoglyph | 179 | 0 | 0.0% (0/179, CI 0%-2%) | <= 10% |
| zero_width | 192 | 0 | 0.0% (0/192, CI 0%-2%) | <= 5% |
| leetspeak | 131 | 13 | 9.9% (13/131, CI 6%-16%) | <= 15% |
| split_words | 240 | 46 | 19.2% (46/240, CI 15%-25%) | <= 15% |
| mixed_script | 273 | 0 | 0.0% (0/273, CI 0%-1%) | <= 15% |
| encoding | 192 | 1 | 0.5% (1/192, CI 0%-3%) | informational |
| visual_tricks | 234 | 19 | 8.1% (19/234, CI 5%-12%) | <= 25% |
| url_obfuscation | 50 | 4 | 8.0% (4/50, CI 3%-19%) | <= 15% |
| upi_confusables | 142 | 8 | 5.6% (8/142, CI 3%-11%) | <= 15% |
| staff_claim | 48 | 0 | 0.0% (0/48, CI 0%-7%) | <= 5% |
| padding_dilution | 240 | 3 | 1.2% (3/240, CI 0%-4%) | <= 10% |
| prompt_injection (rules path) | 192 | 1 | 0.5% (1/192, CI 0%-3%) | <= 5% |
| prompt_injection (LLM obeys injection) | 192 | 0 | 0.0% (0/192, CI 0%-2%) | <= 10% |

## Techniques with at least one bypass

| category/technique | n | bypassed | rate |
|---|---|---|---|
| leetspeak/leet_heavy | 44 | 2 | 0.04545 |
| leetspeak/leet_light | 43 | 1 | 0.02326 |
| leetspeak/leet_symbols | 44 | 10 | 0.2273 |
| split_words/split_dotted | 48 | 2 | 0.04167 |
| split_words/split_midword | 48 | 9 | 0.1875 |
| split_words/split_newline | 48 | 9 | 0.1875 |
| split_words/split_spaced | 48 | 19 | 0.3958 |
| split_words/split_underscore | 48 | 7 | 0.1458 |
| encoding/enc_base64 | 48 | 1 | 0.02083 |
| visual_tricks/vis_vertical | 48 | 19 | 0.3958 |
| url_obfuscation/url_comma_dot | 5 | 2 | 0.4 |
| url_obfuscation/url_split_two_msgs_style | 5 | 2 | 0.4 |
| upi_confusables/upi_dot_for_at | 12 | 4 | 0.3333 |
| upi_confusables/upi_spelled_handle | 12 | 4 | 0.3333 |
| padding_dilution/pad_benign_before | 48 | 1 | 0.02083 |
| padding_dilution/pad_emoji_flood | 48 | 1 | 0.02083 |
| padding_dilution/pad_repeat_clean | 48 | 1 | 0.02083 |
| prompt_injection/inject_json_breakout | 48 | 1 | 0.02083 |

## Bypass by label

| category/label | n | bypassed | rate |
|---|---|---|---|
| encoding/doxxing | 48 | 1 | 0.02083 |
| leetspeak/abuse | 30 | 2 | 0.06667 |
| leetspeak/doxxing | 29 | 4 | 0.1379 |
| leetspeak/fake_upi | 36 | 3 | 0.08333 |
| leetspeak/scam | 36 | 4 | 0.1111 |
| padding_dilution/abuse | 60 | 3 | 0.05 |
| prompt_injection/abuse | 48 | 1 | 0.02083 |
| split_words/abuse | 60 | 12 | 0.2 |
| split_words/doxxing | 60 | 23 | 0.3833 |
| split_words/fake_upi | 60 | 3 | 0.05 |
| split_words/scam | 60 | 8 | 0.1333 |
| upi_confusables/fake_upi | 142 | 8 | 0.05634 |
| url_obfuscation/scam | 50 | 4 | 0.08 |
| visual_tricks/abuse | 58 | 4 | 0.06897 |
| visual_tricks/doxxing | 57 | 6 | 0.1053 |
| visual_tricks/fake_upi | 59 | 3 | 0.05085 |
| visual_tricks/scam | 60 | 6 | 0.1 |

## Robustness

| scenario | numbers |
|---|---|
| hold | {"spoof_messages": 56, "hold_events_from_nonstaff": 0, "mod_unhold_accepted": 0, "staff_hold_events": 2, "staff_hold_expected": 2, "denied_events_single": 1, "denied_events_flood_1000": 0} |
| duplicates | {"positive_text_shown_after_replay": 0, "ids_with_more_than_one_verdict": 0, "reprocessed_after_cache_eviction": 1, "staff_cmd_replay_events": 1, "staff_cmd_after_viewer_same_id_events": 0} |
| flood | {"messages": 4000, "positives": 400, "submit_burst_s": 0.012, "hold_submit_ms": 0.27, "hold_events": 1, "positives_shown_on_overlay": 0, "positives_flagged": 100, "positives_dropped_unclassified": 300, "benign_dropped_unclassified": 2700, "dropped_overload": 3000} |
| malformed | {"cases": 30, "crashes": [], "rejected_at_contract": [], "no_verdict": [], "positive_bypass": [], "positives": 10, "max_ms": 314.1, "classify_errors": 0, "shown_no_verdict": []} |
| malformed slowest | [{"name": "regex_hostile_dots", "ms": 314.1}, {"name": "slur_head_then_huge", "ms": 264.0}, {"name": "repeat_slur_10k", "ms": 175.3}, {"name": "huge_1m", "ms": 159.7}] |
| prompt structure | {"payloads": 195, "breakouts": 0, "payload_in_system_prompt": 0} |
| logs | {"log_records": 13450, "log_leaks": 0, "events_scanned": 7452, "event_leaks": 0} |
| injection (obedient LLM) | {"llm_calls": 6, "plain_bases_reaching_llm": 10} |

## Bypass list (95; ids and techniques, message text via --show ID)

| category | id | technique | label | base kind |
|---|---|---|---|---|
| leetspeak | rt00408 | leet_light | doxxing | phone_spoken |
| leetspeak | rt00451 | leet_heavy | doxxing | phone_spoken |
| leetspeak | rt00457 | leet_heavy | doxxing | phone_spoken |
| leetspeak | rt00459 | leet_symbols | abuse | abbrev |
| leetspeak | rt00465 | leet_symbols | abuse | abbrev |
| leetspeak | rt00474 | leet_symbols | scam | loan |
| leetspeak | rt00478 | leet_symbols | scam | support_call |
| leetspeak | rt00479 | leet_symbols | scam | crypto |
| leetspeak | rt00480 | leet_symbols | scam | giveaway |
| leetspeak | rt00482 | leet_symbols | fake_upi | collect |
| leetspeak | rt00487 | leet_symbols | fake_upi | collect |
| leetspeak | rt00492 | leet_symbols | fake_upi | collect |
| leetspeak | rt00495 | leet_symbols | doxxing | phone_spoken |
| split_words | rt00503 | split_spaced | abuse | abbrev |
| split_words | rt00506 | split_spaced | abuse | sexual |
| split_words | rt00507 | split_spaced | abuse | slur_deva |
| split_words | rt00511 | split_spaced | abuse | abbrev |
| split_words | rt00515 | split_spaced | scam | crypto |
| split_words | rt00519 | split_spaced | scam | kyc_bank |
| split_words | rt00522 | split_spaced | scam | phishing |
| split_words | rt00523 | split_spaced | scam | shortener |
| split_words | rt00525 | split_spaced | scam | crypto |
| split_words | rt00526 | split_spaced | scam | giveaway |
| split_words | rt00533 | split_spaced | fake_upi | collect |
| split_words | rt00535 | split_spaced | fake_upi | pay_to_id |
| split_words | rt00538 | split_spaced | fake_upi | collect |
| split_words | rt00539 | split_spaced | doxxing | address |
| split_words | rt00540 | split_spaced | doxxing | intent |
| split_words | rt00543 | split_spaced | doxxing | phone_spoken |
| split_words | rt00545 | split_spaced | doxxing | address |
| split_words | rt00546 | split_spaced | doxxing | intent |
| split_words | rt00549 | split_spaced | doxxing | phone_spoken |
| split_words | rt00551 | split_dotted | abuse | abbrev |
| split_words | rt00591 | split_dotted | doxxing | phone_spoken |
| split_words | rt00602 | split_midword | abuse | sexual |
| split_words | rt00606 | split_midword | abuse | threat |
| split_words | rt00610 | split_midword | abuse | sexual |
| split_words | rt00612 | split_midword | scam | giveaway |
| split_words | rt00635 | split_midword | doxxing | address |
| split_words | rt00636 | split_midword | doxxing | intent |
| split_words | rt00639 | split_midword | doxxing | phone_spoken |
| split_words | rt00640 | split_midword | doxxing | streamer_private |
| split_words | rt00645 | split_midword | doxxing | phone_spoken |
| split_words | rt00647 | split_underscore | abuse | abbrev |
| split_words | rt00683 | split_underscore | doxxing | address |
| split_words | rt00685 | split_underscore | doxxing | phone |
| split_words | rt00687 | split_underscore | doxxing | phone_spoken |
| split_words | rt00691 | split_underscore | doxxing | phone |
| split_words | rt00692 | split_underscore | doxxing | phone_embedded |
| split_words | rt00694 | split_underscore | doxxing | streamer_private |
| split_words | rt00698 | split_newline | abuse | sexual |
| split_words | rt00702 | split_newline | abuse | threat |
| split_words | rt00706 | split_newline | abuse | sexual |
| split_words | rt00708 | split_newline | scam | giveaway |
| split_words | rt00731 | split_newline | doxxing | address |
| split_words | rt00732 | split_newline | doxxing | intent |
| split_words | rt00735 | split_newline | doxxing | phone_spoken |
| split_words | rt00736 | split_newline | doxxing | streamer_private |
| split_words | rt00741 | split_newline | doxxing | phone_spoken |
| encoding | rt01054 | enc_base64 | doxxing | phone |
| visual_tricks | rt01346 | vis_vertical | abuse | abbrev |
| visual_tricks | rt01349 | vis_vertical | abuse | sexual |
| visual_tricks | rt01350 | vis_vertical | abuse | slur_deva |
| visual_tricks | rt01354 | vis_vertical | abuse | abbrev |
| visual_tricks | rt01358 | vis_vertical | scam | crypto |
| visual_tricks | rt01362 | vis_vertical | scam | kyc_bank |
| visual_tricks | rt01365 | vis_vertical | scam | phishing |
| visual_tricks | rt01366 | vis_vertical | scam | shortener |
| visual_tricks | rt01368 | vis_vertical | scam | crypto |
| visual_tricks | rt01369 | vis_vertical | scam | giveaway |
| visual_tricks | rt01376 | vis_vertical | fake_upi | collect |
| visual_tricks | rt01378 | vis_vertical | fake_upi | pay_to_id |
| visual_tricks | rt01381 | vis_vertical | fake_upi | collect |
| visual_tricks | rt01382 | vis_vertical | doxxing | address |
| visual_tricks | rt01383 | vis_vertical | doxxing | intent |
| visual_tricks | rt01386 | vis_vertical | doxxing | phone_spoken |
| visual_tricks | rt01388 | vis_vertical | doxxing | address |
| visual_tricks | rt01389 | vis_vertical | doxxing | intent |
| visual_tricks | rt01392 | vis_vertical | doxxing | phone_spoken |
| url_obfuscation | rt01477 | url_comma_dot | scam | invite |
| url_obfuscation | rt01480 | url_comma_dot | scam | lottery |
| url_obfuscation | rt01487 | url_split_two_msgs_style | scam | invite |
| url_obfuscation | rt01489 | url_split_two_msgs_style | scam | loan |
| upi_confusables | rt01599 | upi_spelled_handle | fake_upi | collect |
| upi_confusables | rt01604 | upi_spelled_handle | fake_upi | collect |
| upi_confusables | rt01606 | upi_spelled_handle | fake_upi | pay_to_id |
| upi_confusables | rt01609 | upi_spelled_handle | fake_upi | collect |
| upi_confusables | rt01623 | upi_dot_for_at | fake_upi | collect |
| upi_confusables | rt01628 | upi_dot_for_at | fake_upi | collect |
| upi_confusables | rt01630 | upi_dot_for_at | fake_upi | pay_to_id |
| upi_confusables | rt01633 | upi_dot_for_at | fake_upi | collect |
| padding_dilution | rt01691 | pad_benign_before | abuse | english |
| padding_dilution | rt01835 | pad_emoji_flood | abuse | english |
| padding_dilution | rt01883 | pad_repeat_clean | abuse | english |
| prompt_injection | rt02070 | inject_json_breakout | abuse | slur_deva |

## Bypass list, obedient LLM (0)

| id | technique | label | base kind | reached LLM |
|---|---|---|---|---|

## Notes

- The Groq stage is a stand-in (no key, offline): ObedientLlm answers the truth label except where the text carries an injected instruction, which it obeys. It measures how much the pipeline's structure lets a fooled LLM release a flagged message, not Groq's real robustness.
- Bypass is measured only over bases the plain classifier already flagged; bases it misses are skipped (counted in the JSON as plain_not_flagged_skipped), so the rate isolates the evasion.
- Without an LLM, borderline messages become HOLD_FOR_MOD and count as caught (they are hidden from the overlay).
- 'encoding' (base64, rot13, hex, reversed) is informational: a viewer cannot read those either.
- Duplicate-id and id-cache-eviction numbers: reprocessed_after_cache_eviction is informational (ids are platform-assigned).
