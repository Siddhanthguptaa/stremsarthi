# redteam-chat report (2026-11-21)

Result: **FAIL**

## Run

- attacks: 2113
- bases: 48
- categories: all
- defect: 
- git_rev: 178ffa40184e
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
| miss rate: chat leetspeak | 0.2443 | <= 0.15 | FAIL (32/131 bypass) |
| miss rate: chat split_words | 0.5 | <= 0.15 | FAIL (120/240 bypass) |
| miss rate: chat mixed_script | 0 | <= 0.15 | PASS (0/273 bypass) |
| miss rate: chat visual_tricks | 0.1068 | <= 0.25 | PASS (25/234 bypass) |
| miss rate: chat url_obfuscation | 0.46 | <= 0.15 | FAIL (23/50 bypass) |
| miss rate: chat upi_confusables | 0.05634 | <= 0.15 | PASS (8/142 bypass) |
| miss rate: chat staff_claim | 0 | <= 0.05 | PASS (0/48 bypass) |
| miss rate: chat padding_dilution | 0.0125 | <= 0.1 | PASS (3/240 bypass) |
| miss rate: chat prompt_injection (rules path) | 0.005208 | <= 0.05 | PASS (1/192) |
| miss rate: chat prompt_injection (obedient LLM) | 0 | <= 0.1 | PASS (0/40 of those that reached the LLM) |
| crash: exceptions or classifier errors | 0 | == 0 | PASS |
| no verdict for an accepted malformed message | 0 | == 0 | PASS |
| malformed positives allowed | 0 | == 0 | PASS |
| malformed message slowest classify (ms) | 1719 | <= 500 | FAIL |
| log records and events containing message text | 0 | == 0 | PASS |
| !hold accepted from non-staff or spoofed roles | 0 | == 0 | PASS |
| staff !hold events (broadcaster, moderator, replay ignored) | 2 | == 2 | PASS |
| denial events published for a 1,000 message !hold flood | 0 | <= 20 | PASS |
| replayed id: positive text shown on overlay | 0 | == 0 | PASS |
| replayed id processed more than once | 0 | == 0 | PASS |
| staff !hold replayed with the same id toggles | 1 | <= 1 | PASS |
| flood: flagged positives shown on overlay | 0 | == 0 | PASS |
| flood: !hold submit latency (ms) | 0.282 | <= 100 | PASS |
| flood: !hold event published | 1 | == 1 | PASS |
| prompt: payloads that break out of the JSON data | 0 | == 0 | PASS |

## Evasion bypass rate by category (attacked message allowed, plain form was flagged)

| category | n | bypassed | bypass rate | ceiling |
|---|---|---|---|---|
| homoglyph | 179 | 0 | 0.0% (0/179, CI 0%-2%) | <= 10% |
| zero_width | 192 | 0 | 0.0% (0/192, CI 0%-2%) | <= 5% |
| leetspeak | 131 | 32 | 24.4% (32/131, CI 18%-32%) | <= 15% |
| split_words | 240 | 120 | 50.0% (120/240, CI 44%-56%) | <= 15% |
| mixed_script | 273 | 0 | 0.0% (0/273, CI 0%-1%) | <= 15% |
| encoding | 192 | 179 | 93.2% (179/192, CI 89%-96%) | informational |
| visual_tricks | 234 | 25 | 10.7% (25/234, CI 7%-15%) | <= 25% |
| url_obfuscation | 50 | 23 | 46.0% (23/50, CI 33%-60%) | <= 15% |
| upi_confusables | 142 | 8 | 5.6% (8/142, CI 3%-11%) | <= 15% |
| staff_claim | 48 | 0 | 0.0% (0/48, CI 0%-7%) | <= 5% |
| padding_dilution | 240 | 3 | 1.2% (3/240, CI 0%-4%) | <= 10% |
| prompt_injection (rules path) | 192 | 1 | 0.5% (1/192, CI 0%-3%) | <= 5% |
| prompt_injection (LLM obeys injection) | 192 | 0 | 0.0% (0/192, CI 0%-2%) | <= 10% |

## Techniques with at least one bypass

| category/technique | n | bypassed | rate |
|---|---|---|---|
| leetspeak/leet_heavy | 44 | 7 | 0.1591 |
| leetspeak/leet_light | 43 | 4 | 0.09302 |
| leetspeak/leet_symbols | 44 | 21 | 0.4773 |
| split_words/split_dotted | 48 | 31 | 0.6458 |
| split_words/split_midword | 48 | 12 | 0.25 |
| split_words/split_newline | 48 | 12 | 0.25 |
| split_words/split_spaced | 48 | 25 | 0.5208 |
| split_words/split_underscore | 48 | 40 | 0.8333 |
| encoding/enc_base64 | 48 | 48 | 1 |
| encoding/enc_hex | 48 | 46 | 0.9583 |
| encoding/enc_reversed | 48 | 47 | 0.9792 |
| encoding/enc_rot13 | 48 | 38 | 0.7917 |
| visual_tricks/vis_vertical | 48 | 25 | 0.5208 |
| url_obfuscation/url_comma_dot | 5 | 5 | 1 |
| url_obfuscation/url_dot_words | 5 | 2 | 0.4 |
| url_obfuscation/url_fullwidth_dot | 5 | 5 | 1 |
| url_obfuscation/url_percent_host | 5 | 4 | 0.8 |
| url_obfuscation/url_spaced | 5 | 5 | 1 |
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
| encoding/abuse | 48 | 46 | 0.9583 |
| encoding/doxxing | 48 | 42 | 0.875 |
| encoding/fake_upi | 48 | 46 | 0.9583 |
| encoding/scam | 48 | 45 | 0.9375 |
| leetspeak/abuse | 30 | 3 | 0.1 |
| leetspeak/doxxing | 29 | 12 | 0.4138 |
| leetspeak/fake_upi | 36 | 13 | 0.3611 |
| leetspeak/scam | 36 | 4 | 0.1111 |
| padding_dilution/abuse | 60 | 3 | 0.05 |
| prompt_injection/abuse | 48 | 1 | 0.02083 |
| split_words/abuse | 60 | 27 | 0.45 |
| split_words/doxxing | 60 | 34 | 0.5667 |
| split_words/fake_upi | 60 | 25 | 0.4167 |
| split_words/scam | 60 | 34 | 0.5667 |
| upi_confusables/fake_upi | 142 | 8 | 0.05634 |
| url_obfuscation/scam | 50 | 23 | 0.46 |
| visual_tricks/abuse | 58 | 5 | 0.08621 |
| visual_tricks/doxxing | 57 | 7 | 0.1228 |
| visual_tricks/fake_upi | 59 | 3 | 0.05085 |
| visual_tricks/scam | 60 | 10 | 0.1667 |

## Robustness

| scenario | numbers |
|---|---|
| hold | {"spoof_messages": 56, "hold_events_from_nonstaff": 0, "mod_unhold_accepted": 0, "staff_hold_events": 2, "staff_hold_expected": 2, "denied_events_single": 1, "denied_events_flood_1000": 0} |
| duplicates | {"positive_text_shown_after_replay": 0, "ids_with_more_than_one_verdict": 0, "reprocessed_after_cache_eviction": 1, "staff_cmd_replay_events": 1, "staff_cmd_after_viewer_same_id_events": 0} |
| flood | {"messages": 4000, "positives": 400, "submit_burst_s": 0.006, "hold_submit_ms": 0.282, "hold_events": 1, "positives_shown_on_overlay": 0, "positives_flagged": 100, "positives_dropped_unclassified": 300, "benign_dropped_unclassified": 2700, "dropped_overload": 3000} |
| malformed | {"cases": 30, "crashes": [], "rejected_at_contract": [], "no_verdict": [], "positive_bypass": [], "positives": 10, "max_ms": 1719.1, "classify_errors": 0, "shown_no_verdict": []} |
| malformed slowest | [{"name": "regex_hostile_dots", "ms": 1719.1}, {"name": "repeat_slur_10k", "ms": 184.4}, {"name": "slur_head_then_huge", "ms": 181.5}, {"name": "huge_1m", "ms": 146.5}] |
| prompt structure | {"payloads": 195, "breakouts": 0, "payload_in_system_prompt": 0} |
| logs | {"log_records": 12994, "log_leaks": 0, "events_scanned": 6633, "event_leaks": 0} |
| injection (obedient LLM) | {"llm_calls": 6, "plain_bases_reaching_llm": 10} |

## Bypass list (391; ids and techniques, message text via --show ID)

| category | id | technique | label | base kind |
|---|---|---|---|---|
| leetspeak | rt00395 | leet_light | fake_upi | collect |
| leetspeak | rt00402 | leet_light | fake_upi | pay_to_id |
| leetspeak | rt00406 | leet_light | doxxing | address |
| leetspeak | rt00408 | leet_light | doxxing | phone_spoken |
| leetspeak | rt00438 | leet_heavy | fake_upi | collect |
| leetspeak | rt00445 | leet_heavy | fake_upi | pay_to_id |
| leetspeak | rt00448 | leet_heavy | fake_upi | collect |
| leetspeak | rt00449 | leet_heavy | doxxing | address |
| leetspeak | rt00451 | leet_heavy | doxxing | phone_spoken |
| leetspeak | rt00453 | leet_heavy | doxxing | address |
| leetspeak | rt00457 | leet_heavy | doxxing | phone_spoken |
| leetspeak | rt00459 | leet_symbols | abuse | abbrev |
| leetspeak | rt00462 | leet_symbols | abuse | sexual |
| leetspeak | rt00465 | leet_symbols | abuse | abbrev |
| leetspeak | rt00474 | leet_symbols | scam | loan |
| leetspeak | rt00478 | leet_symbols | scam | support_call |
| leetspeak | rt00479 | leet_symbols | scam | crypto |
| leetspeak | rt00480 | leet_symbols | scam | giveaway |
| leetspeak | rt00482 | leet_symbols | fake_upi | collect |
| leetspeak | rt00483 | leet_symbols | fake_upi | deeplink |
| leetspeak | rt00484 | leet_symbols | fake_upi | pay_to_id |
| leetspeak | rt00487 | leet_symbols | fake_upi | collect |
| leetspeak | rt00488 | leet_symbols | fake_upi | deeplink |
| leetspeak | rt00489 | leet_symbols | fake_upi | pay_to_id |
| leetspeak | rt00491 | leet_symbols | fake_upi | app_deeplink |
| leetspeak | rt00492 | leet_symbols | fake_upi | collect |
| leetspeak | rt00493 | leet_symbols | doxxing | address |
| leetspeak | rt00494 | leet_symbols | doxxing | intent |
| leetspeak | rt00495 | leet_symbols | doxxing | phone_spoken |
| leetspeak | rt00496 | leet_symbols | doxxing | streamer_private |
| leetspeak | rt00497 | leet_symbols | doxxing | address |
| leetspeak | rt00501 | leet_symbols | doxxing | phone_spoken |
| split_words | rt00503 | split_spaced | abuse | abbrev |
| split_words | rt00506 | split_spaced | abuse | sexual |
| split_words | rt00507 | split_spaced | abuse | slur_deva |
| split_words | rt00509 | split_spaced | abuse | slur_regional |
| split_words | rt00511 | split_spaced | abuse | abbrev |
| split_words | rt00515 | split_spaced | scam | crypto |
| split_words | rt00518 | split_spaced | scam | invite |
| split_words | rt00519 | split_spaced | scam | kyc_bank |
| split_words | rt00520 | split_spaced | scam | loan |
| split_words | rt00521 | split_spaced | scam | lottery |
| split_words | rt00522 | split_spaced | scam | phishing |
| split_words | rt00523 | split_spaced | scam | shortener |
| split_words | rt00524 | split_spaced | scam | support_call |
| split_words | rt00525 | split_spaced | scam | crypto |
| split_words | rt00526 | split_spaced | scam | giveaway |
| split_words | rt00533 | split_spaced | fake_upi | collect |
| split_words | rt00535 | split_spaced | fake_upi | pay_to_id |
| split_words | rt00538 | split_spaced | fake_upi | collect |
| split_words | rt00539 | split_spaced | doxxing | address |
| split_words | rt00540 | split_spaced | doxxing | intent |
| split_words | rt00543 | split_spaced | doxxing | phone_spoken |
| split_words | rt00544 | split_spaced | doxxing | streamer_private |
| split_words | rt00545 | split_spaced | doxxing | address |
| split_words | rt00546 | split_spaced | doxxing | intent |
| split_words | rt00549 | split_spaced | doxxing | phone_spoken |
| split_words | rt00551 | split_dotted | abuse | abbrev |
| split_words | rt00554 | split_dotted | abuse | sexual |
| split_words | rt00555 | split_dotted | abuse | slur_deva |
| split_words | rt00557 | split_dotted | abuse | slur_regional |
| split_words | rt00559 | split_dotted | abuse | abbrev |
| split_words | rt00563 | split_dotted | scam | crypto |
| split_words | rt00564 | split_dotted | scam | giveaway |
| split_words | rt00566 | split_dotted | scam | invite |
| split_words | rt00567 | split_dotted | scam | kyc_bank |
| split_words | rt00568 | split_dotted | scam | loan |
| split_words | rt00569 | split_dotted | scam | lottery |
| split_words | rt00570 | split_dotted | scam | phishing |
| split_words | rt00571 | split_dotted | scam | shortener |
| split_words | rt00572 | split_dotted | scam | support_call |
| split_words | rt00573 | split_dotted | scam | crypto |
| split_words | rt00574 | split_dotted | scam | giveaway |
| split_words | rt00575 | split_dotted | fake_upi | app_deeplink |
| split_words | rt00576 | split_dotted | fake_upi | collect |
| split_words | rt00577 | split_dotted | fake_upi | deeplink |
| split_words | rt00578 | split_dotted | fake_upi | pay_to_id |
| split_words | rt00580 | split_dotted | fake_upi | app_deeplink |
| split_words | rt00581 | split_dotted | fake_upi | collect |
| split_words | rt00582 | split_dotted | fake_upi | deeplink |
| split_words | rt00583 | split_dotted | fake_upi | pay_to_id |
| split_words | rt00585 | split_dotted | fake_upi | app_deeplink |
| split_words | rt00586 | split_dotted | fake_upi | collect |
| split_words | rt00588 | split_dotted | doxxing | intent |
| split_words | rt00591 | split_dotted | doxxing | phone_spoken |
| split_words | rt00592 | split_dotted | doxxing | streamer_private |
| split_words | rt00594 | split_dotted | doxxing | intent |
| split_words | rt00597 | split_dotted | doxxing | phone_spoken |
| split_words | rt00602 | split_midword | abuse | sexual |
| split_words | rt00603 | split_midword | abuse | slur_deva |
| split_words | rt00604 | split_midword | abuse | slur_latn |
| split_words | rt00606 | split_midword | abuse | threat |
| split_words | rt00608 | split_midword | abuse | english |
| split_words | rt00610 | split_midword | abuse | sexual |
| split_words | rt00612 | split_midword | scam | giveaway |
| split_words | rt00635 | split_midword | doxxing | address |
| split_words | rt00636 | split_midword | doxxing | intent |
| split_words | rt00639 | split_midword | doxxing | phone_spoken |
| split_words | rt00640 | split_midword | doxxing | streamer_private |
| split_words | rt00645 | split_midword | doxxing | phone_spoken |
| split_words | rt00647 | split_underscore | abuse | abbrev |
| split_words | rt00650 | split_underscore | abuse | sexual |
| split_words | rt00651 | split_underscore | abuse | slur_deva |
| split_words | rt00653 | split_underscore | abuse | slur_regional |
| split_words | rt00655 | split_underscore | abuse | abbrev |
| split_words | rt00659 | split_underscore | scam | crypto |
| split_words | rt00660 | split_underscore | scam | giveaway |
| split_words | rt00662 | split_underscore | scam | invite |
| split_words | rt00663 | split_underscore | scam | kyc_bank |
| split_words | rt00664 | split_underscore | scam | loan |
| split_words | rt00665 | split_underscore | scam | lottery |
| split_words | rt00666 | split_underscore | scam | phishing |
| split_words | rt00667 | split_underscore | scam | shortener |
| split_words | rt00668 | split_underscore | scam | support_call |
| split_words | rt00669 | split_underscore | scam | crypto |
| split_words | rt00670 | split_underscore | scam | giveaway |
| split_words | rt00671 | split_underscore | fake_upi | app_deeplink |
| split_words | rt00672 | split_underscore | fake_upi | collect |
| split_words | rt00673 | split_underscore | fake_upi | deeplink |
| split_words | rt00674 | split_underscore | fake_upi | pay_to_id |
| split_words | rt00675 | split_underscore | fake_upi | phone_upi |
| split_words | rt00676 | split_underscore | fake_upi | app_deeplink |
| split_words | rt00677 | split_underscore | fake_upi | collect |
| split_words | rt00678 | split_underscore | fake_upi | deeplink |
| split_words | rt00679 | split_underscore | fake_upi | pay_to_id |
| split_words | rt00680 | split_underscore | fake_upi | phone_upi |
| split_words | rt00681 | split_underscore | fake_upi | app_deeplink |
| split_words | rt00682 | split_underscore | fake_upi | collect |
| split_words | rt00683 | split_underscore | doxxing | address |
| split_words | rt00684 | split_underscore | doxxing | intent |
| split_words | rt00685 | split_underscore | doxxing | phone |
| split_words | rt00686 | split_underscore | doxxing | phone_embedded |
| split_words | rt00687 | split_underscore | doxxing | phone_spoken |
| split_words | rt00688 | split_underscore | doxxing | streamer_private |
| split_words | rt00689 | split_underscore | doxxing | address |
| split_words | rt00690 | split_underscore | doxxing | intent |
| split_words | rt00691 | split_underscore | doxxing | phone |
| split_words | rt00692 | split_underscore | doxxing | phone_embedded |
| split_words | rt00693 | split_underscore | doxxing | phone_spoken |
| split_words | rt00694 | split_underscore | doxxing | streamer_private |
| split_words | rt00698 | split_newline | abuse | sexual |
| split_words | rt00699 | split_newline | abuse | slur_deva |
| split_words | rt00700 | split_newline | abuse | slur_latn |
| split_words | rt00702 | split_newline | abuse | threat |
| split_words | rt00704 | split_newline | abuse | english |
| split_words | rt00706 | split_newline | abuse | sexual |
| split_words | rt00708 | split_newline | scam | giveaway |
| split_words | rt00731 | split_newline | doxxing | address |
| split_words | rt00732 | split_newline | doxxing | intent |
| split_words | rt00735 | split_newline | doxxing | phone_spoken |
| split_words | rt00736 | split_newline | doxxing | streamer_private |
| split_words | rt00741 | split_newline | doxxing | phone_spoken |
| encoding | rt01016 | enc_base64 | abuse | abbrev |
| encoding | rt01017 | enc_base64 | abuse | english |
| encoding | rt01018 | enc_base64 | abuse | profane_praise |
| encoding | rt01019 | enc_base64 | abuse | sexual |
| encoding | rt01020 | enc_base64 | abuse | slur_deva |
| encoding | rt01021 | enc_base64 | abuse | slur_latn |
| encoding | rt01022 | enc_base64 | abuse | slur_regional |
| encoding | rt01023 | enc_base64 | abuse | threat |
| encoding | rt01024 | enc_base64 | abuse | abbrev |
| encoding | rt01025 | enc_base64 | abuse | english |
| encoding | rt01026 | enc_base64 | abuse | profane_praise |
| encoding | rt01027 | enc_base64 | abuse | sexual |
| encoding | rt01028 | enc_base64 | scam | crypto |
| encoding | rt01029 | enc_base64 | scam | giveaway |
| encoding | rt01030 | enc_base64 | scam | hack_tool |
| encoding | rt01031 | enc_base64 | scam | invite |
| encoding | rt01032 | enc_base64 | scam | kyc_bank |
| encoding | rt01033 | enc_base64 | scam | loan |
| encoding | rt01034 | enc_base64 | scam | lottery |
| encoding | rt01035 | enc_base64 | scam | phishing |
| encoding | rt01036 | enc_base64 | scam | shortener |
| encoding | rt01037 | enc_base64 | scam | support_call |
| encoding | rt01038 | enc_base64 | scam | crypto |
| encoding | rt01039 | enc_base64 | scam | giveaway |
| encoding | rt01040 | enc_base64 | fake_upi | app_deeplink |
| encoding | rt01041 | enc_base64 | fake_upi | collect |
| encoding | rt01042 | enc_base64 | fake_upi | deeplink |
| encoding | rt01043 | enc_base64 | fake_upi | pay_to_id |
| encoding | rt01044 | enc_base64 | fake_upi | phone_upi |
| encoding | rt01045 | enc_base64 | fake_upi | app_deeplink |
| encoding | rt01046 | enc_base64 | fake_upi | collect |
| encoding | rt01047 | enc_base64 | fake_upi | deeplink |
| encoding | rt01048 | enc_base64 | fake_upi | pay_to_id |
| encoding | rt01049 | enc_base64 | fake_upi | phone_upi |
| encoding | rt01050 | enc_base64 | fake_upi | app_deeplink |
| encoding | rt01051 | enc_base64 | fake_upi | collect |
| encoding | rt01052 | enc_base64 | doxxing | address |
| encoding | rt01053 | enc_base64 | doxxing | intent |
| encoding | rt01054 | enc_base64 | doxxing | phone |
| encoding | rt01055 | enc_base64 | doxxing | phone_embedded |
| encoding | rt01056 | enc_base64 | doxxing | phone_spoken |
| encoding | rt01057 | enc_base64 | doxxing | streamer_private |
| encoding | rt01058 | enc_base64 | doxxing | address |
| encoding | rt01059 | enc_base64 | doxxing | intent |
| encoding | rt01060 | enc_base64 | doxxing | phone |
| encoding | rt01061 | enc_base64 | doxxing | phone_embedded |
| encoding | rt01062 | enc_base64 | doxxing | phone_spoken |
| encoding | rt01063 | enc_base64 | doxxing | streamer_private |
| encoding | rt01064 | enc_rot13 | abuse | abbrev |
| encoding | rt01065 | enc_rot13 | abuse | english |
| encoding | rt01066 | enc_rot13 | abuse | profane_praise |
| encoding | rt01067 | enc_rot13 | abuse | sexual |
| encoding | rt01069 | enc_rot13 | abuse | slur_latn |
| encoding | rt01071 | enc_rot13 | abuse | threat |
| encoding | rt01072 | enc_rot13 | abuse | abbrev |
| encoding | rt01073 | enc_rot13 | abuse | english |
| encoding | rt01074 | enc_rot13 | abuse | profane_praise |
| encoding | rt01075 | enc_rot13 | abuse | sexual |
| encoding | rt01076 | enc_rot13 | scam | crypto |
| encoding | rt01078 | enc_rot13 | scam | hack_tool |
| encoding | rt01079 | enc_rot13 | scam | invite |
| encoding | rt01080 | enc_rot13 | scam | kyc_bank |
| encoding | rt01081 | enc_rot13 | scam | loan |
| encoding | rt01082 | enc_rot13 | scam | lottery |
| encoding | rt01083 | enc_rot13 | scam | phishing |
| encoding | rt01084 | enc_rot13 | scam | shortener |
| encoding | rt01085 | enc_rot13 | scam | support_call |
| encoding | rt01086 | enc_rot13 | scam | crypto |
| encoding | rt01087 | enc_rot13 | scam | giveaway |
| encoding | rt01088 | enc_rot13 | fake_upi | app_deeplink |
| encoding | rt01089 | enc_rot13 | fake_upi | collect |
| encoding | rt01090 | enc_rot13 | fake_upi | deeplink |
| encoding | rt01091 | enc_rot13 | fake_upi | pay_to_id |
| encoding | rt01093 | enc_rot13 | fake_upi | app_deeplink |
| encoding | rt01094 | enc_rot13 | fake_upi | collect |
| encoding | rt01095 | enc_rot13 | fake_upi | deeplink |
| encoding | rt01096 | enc_rot13 | fake_upi | pay_to_id |
| encoding | rt01098 | enc_rot13 | fake_upi | app_deeplink |
| encoding | rt01099 | enc_rot13 | fake_upi | collect |
| encoding | rt01100 | enc_rot13 | doxxing | address |
| encoding | rt01101 | enc_rot13 | doxxing | intent |
| encoding | rt01104 | enc_rot13 | doxxing | phone_spoken |
| encoding | rt01105 | enc_rot13 | doxxing | streamer_private |
| encoding | rt01106 | enc_rot13 | doxxing | address |
| encoding | rt01107 | enc_rot13 | doxxing | intent |
| encoding | rt01110 | enc_rot13 | doxxing | phone_spoken |
| encoding | rt01112 | enc_hex | abuse | abbrev |
| encoding | rt01113 | enc_hex | abuse | english |
| encoding | rt01114 | enc_hex | abuse | profane_praise |
| encoding | rt01115 | enc_hex | abuse | sexual |
| encoding | rt01116 | enc_hex | abuse | slur_deva |
| encoding | rt01117 | enc_hex | abuse | slur_latn |
| encoding | rt01118 | enc_hex | abuse | slur_regional |
| encoding | rt01119 | enc_hex | abuse | threat |
| encoding | rt01120 | enc_hex | abuse | abbrev |
| encoding | rt01121 | enc_hex | abuse | english |
| encoding | rt01122 | enc_hex | abuse | profane_praise |
| encoding | rt01123 | enc_hex | abuse | sexual |
| encoding | rt01124 | enc_hex | scam | crypto |
| encoding | rt01125 | enc_hex | scam | giveaway |
| encoding | rt01126 | enc_hex | scam | hack_tool |
| encoding | rt01127 | enc_hex | scam | invite |
| encoding | rt01128 | enc_hex | scam | kyc_bank |
| encoding | rt01130 | enc_hex | scam | lottery |
| encoding | rt01131 | enc_hex | scam | phishing |
| encoding | rt01132 | enc_hex | scam | shortener |
| encoding | rt01134 | enc_hex | scam | crypto |
| encoding | rt01135 | enc_hex | scam | giveaway |
| encoding | rt01136 | enc_hex | fake_upi | app_deeplink |
| encoding | rt01137 | enc_hex | fake_upi | collect |
| encoding | rt01138 | enc_hex | fake_upi | deeplink |
| encoding | rt01139 | enc_hex | fake_upi | pay_to_id |
| encoding | rt01140 | enc_hex | fake_upi | phone_upi |
| encoding | rt01141 | enc_hex | fake_upi | app_deeplink |
| encoding | rt01142 | enc_hex | fake_upi | collect |
| encoding | rt01143 | enc_hex | fake_upi | deeplink |
| encoding | rt01144 | enc_hex | fake_upi | pay_to_id |
| encoding | rt01145 | enc_hex | fake_upi | phone_upi |
| encoding | rt01146 | enc_hex | fake_upi | app_deeplink |
| encoding | rt01147 | enc_hex | fake_upi | collect |
| encoding | rt01148 | enc_hex | doxxing | address |
| encoding | rt01149 | enc_hex | doxxing | intent |
| encoding | rt01150 | enc_hex | doxxing | phone |
| encoding | rt01151 | enc_hex | doxxing | phone_embedded |
| encoding | rt01152 | enc_hex | doxxing | phone_spoken |
| encoding | rt01153 | enc_hex | doxxing | streamer_private |
| encoding | rt01154 | enc_hex | doxxing | address |
| encoding | rt01155 | enc_hex | doxxing | intent |
| encoding | rt01156 | enc_hex | doxxing | phone |
| encoding | rt01157 | enc_hex | doxxing | phone_embedded |
| encoding | rt01158 | enc_hex | doxxing | phone_spoken |
| encoding | rt01159 | enc_hex | doxxing | streamer_private |
| encoding | rt01160 | enc_reversed | abuse | abbrev |
| encoding | rt01161 | enc_reversed | abuse | english |
| encoding | rt01162 | enc_reversed | abuse | profane_praise |
| encoding | rt01163 | enc_reversed | abuse | sexual |
| encoding | rt01164 | enc_reversed | abuse | slur_deva |
| encoding | rt01165 | enc_reversed | abuse | slur_latn |
| encoding | rt01166 | enc_reversed | abuse | slur_regional |
| encoding | rt01167 | enc_reversed | abuse | threat |
| encoding | rt01168 | enc_reversed | abuse | abbrev |
| encoding | rt01169 | enc_reversed | abuse | english |
| encoding | rt01170 | enc_reversed | abuse | profane_praise |
| encoding | rt01171 | enc_reversed | abuse | sexual |
| encoding | rt01172 | enc_reversed | scam | crypto |
| encoding | rt01173 | enc_reversed | scam | giveaway |
| encoding | rt01174 | enc_reversed | scam | hack_tool |
| encoding | rt01175 | enc_reversed | scam | invite |
| encoding | rt01176 | enc_reversed | scam | kyc_bank |
| encoding | rt01177 | enc_reversed | scam | loan |
| encoding | rt01178 | enc_reversed | scam | lottery |
| encoding | rt01179 | enc_reversed | scam | phishing |
| encoding | rt01180 | enc_reversed | scam | shortener |
| encoding | rt01181 | enc_reversed | scam | support_call |
| encoding | rt01182 | enc_reversed | scam | crypto |
| encoding | rt01183 | enc_reversed | scam | giveaway |
| encoding | rt01184 | enc_reversed | fake_upi | app_deeplink |
| encoding | rt01185 | enc_reversed | fake_upi | collect |
| encoding | rt01186 | enc_reversed | fake_upi | deeplink |
| encoding | rt01187 | enc_reversed | fake_upi | pay_to_id |
| encoding | rt01188 | enc_reversed | fake_upi | phone_upi |
| encoding | rt01189 | enc_reversed | fake_upi | app_deeplink |
| encoding | rt01190 | enc_reversed | fake_upi | collect |
| encoding | rt01191 | enc_reversed | fake_upi | deeplink |
| encoding | rt01192 | enc_reversed | fake_upi | pay_to_id |
| encoding | rt01193 | enc_reversed | fake_upi | phone_upi |
| encoding | rt01194 | enc_reversed | fake_upi | app_deeplink |
| encoding | rt01195 | enc_reversed | fake_upi | collect |
| encoding | rt01196 | enc_reversed | doxxing | address |
| encoding | rt01197 | enc_reversed | doxxing | intent |
| encoding | rt01198 | enc_reversed | doxxing | phone |
| encoding | rt01199 | enc_reversed | doxxing | phone_embedded |
| encoding | rt01200 | enc_reversed | doxxing | phone_spoken |
| encoding | rt01201 | enc_reversed | doxxing | streamer_private |
| encoding | rt01202 | enc_reversed | doxxing | address |
| encoding | rt01203 | enc_reversed | doxxing | intent |
| encoding | rt01204 | enc_reversed | doxxing | phone |
| encoding | rt01205 | enc_reversed | doxxing | phone_embedded |
| encoding | rt01206 | enc_reversed | doxxing | phone_spoken |
| visual_tricks | rt01346 | vis_vertical | abuse | abbrev |
| visual_tricks | rt01349 | vis_vertical | abuse | sexual |
| visual_tricks | rt01350 | vis_vertical | abuse | slur_deva |
| visual_tricks | rt01352 | vis_vertical | abuse | slur_regional |
| visual_tricks | rt01354 | vis_vertical | abuse | abbrev |
| visual_tricks | rt01358 | vis_vertical | scam | crypto |
| visual_tricks | rt01361 | vis_vertical | scam | invite |
| visual_tricks | rt01362 | vis_vertical | scam | kyc_bank |
| visual_tricks | rt01363 | vis_vertical | scam | loan |
| visual_tricks | rt01364 | vis_vertical | scam | lottery |
| visual_tricks | rt01365 | vis_vertical | scam | phishing |
| visual_tricks | rt01366 | vis_vertical | scam | shortener |
| visual_tricks | rt01367 | vis_vertical | scam | support_call |
| visual_tricks | rt01368 | vis_vertical | scam | crypto |
| visual_tricks | rt01369 | vis_vertical | scam | giveaway |
| visual_tricks | rt01376 | vis_vertical | fake_upi | collect |
| visual_tricks | rt01378 | vis_vertical | fake_upi | pay_to_id |
| visual_tricks | rt01381 | vis_vertical | fake_upi | collect |
| visual_tricks | rt01382 | vis_vertical | doxxing | address |
| visual_tricks | rt01383 | vis_vertical | doxxing | intent |
| visual_tricks | rt01386 | vis_vertical | doxxing | phone_spoken |
| visual_tricks | rt01387 | vis_vertical | doxxing | streamer_private |
| visual_tricks | rt01388 | vis_vertical | doxxing | address |
| visual_tricks | rt01389 | vis_vertical | doxxing | intent |
| visual_tricks | rt01392 | vis_vertical | doxxing | phone_spoken |
| url_obfuscation | rt01447 | url_dot_words | scam | invite |
| url_obfuscation | rt01449 | url_dot_words | scam | loan |
| url_obfuscation | rt01452 | url_spaced | scam | invite |
| url_obfuscation | rt01453 | url_spaced | scam | kyc_bank |
| url_obfuscation | rt01454 | url_spaced | scam | loan |
| url_obfuscation | rt01455 | url_spaced | scam | lottery |
| url_obfuscation | rt01456 | url_spaced | scam | phishing |
| url_obfuscation | rt01472 | url_percent_host | scam | invite |
| url_obfuscation | rt01474 | url_percent_host | scam | loan |
| url_obfuscation | rt01475 | url_percent_host | scam | lottery |
| url_obfuscation | rt01476 | url_percent_host | scam | phishing |
| url_obfuscation | rt01477 | url_comma_dot | scam | invite |
| url_obfuscation | rt01478 | url_comma_dot | scam | kyc_bank |
| url_obfuscation | rt01479 | url_comma_dot | scam | loan |
| url_obfuscation | rt01480 | url_comma_dot | scam | lottery |
| url_obfuscation | rt01481 | url_comma_dot | scam | phishing |
| url_obfuscation | rt01482 | url_fullwidth_dot | scam | invite |
| url_obfuscation | rt01483 | url_fullwidth_dot | scam | kyc_bank |
| url_obfuscation | rt01484 | url_fullwidth_dot | scam | loan |
| url_obfuscation | rt01485 | url_fullwidth_dot | scam | lottery |
| url_obfuscation | rt01486 | url_fullwidth_dot | scam | phishing |
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
