# redteam-chat report (2026-11-21)

Result: **FAIL**

## Run

- attacks: 2113
- bases: 48
- categories: all
- defect: 
- git_rev: 8a1e6e353205
- hardware: {"cpu": "13th Gen Intel(R) Core(TM) i5-13450HX", "gpu": "NVIDIA GeForce RTX 3050 6GB Laptop GPU, 581.86"}
- per_label: 12
- profile: dev
- reference: False
- seed: 4242
- suite: redteam-chat

## Checks

| check | value | target | result |
|---|---|---|---|
| miss rate: chat homoglyph | 0.6145 | <= 0.1 | FAIL (110/179 bypass) |
| miss rate: chat zero_width | 0 | <= 0.05 | PASS (0/192 bypass) |
| miss rate: chat leetspeak | 0.5191 | <= 0.15 | FAIL (68/131 bypass) |
| miss rate: chat split_words | 0.575 | <= 0.15 | FAIL (138/240 bypass) |
| miss rate: chat mixed_script | 0.1429 | <= 0.15 | PASS (39/273 bypass) |
| miss rate: chat visual_tricks | 0.6923 | <= 0.25 | FAIL (162/234 bypass) |
| miss rate: chat url_obfuscation | 0.6 | <= 0.15 | FAIL (30/50 bypass) |
| miss rate: chat upi_confusables | 0.1831 | <= 0.15 | FAIL (26/142 bypass) |
| miss rate: chat staff_claim | 0 | <= 0.05 | PASS (0/48 bypass) |
| miss rate: chat padding_dilution | 0.2125 | <= 0.1 | FAIL (51/240 bypass) |
| miss rate: chat prompt_injection (rules path) | 0.005208 | <= 0.05 | PASS (1/192) |
| miss rate: chat prompt_injection (obedient LLM) | 0.2083 | <= 0.1 | FAIL (40/40 of those that reached the LLM) |
| crash: exceptions or classifier errors | 0 | == 0 | PASS |
| no verdict for an accepted malformed message | 0 | == 0 | PASS |
| malformed positives allowed | 3 | == 0 | FAIL |
| malformed message slowest classify (ms) | 230.2 | <= 500 | PASS |
| log records and events containing message text | 0 | == 0 | PASS |
| !hold accepted from non-staff or spoofed roles | 0 | == 0 | PASS |
| staff !hold events (broadcaster, moderator, replay ignored) | 2 | == 2 | PASS |
| denial events published for a 1,000 message !hold flood | 1000 | <= 20 | FAIL |
| replayed id: positive text shown on overlay | 0 | == 0 | PASS |
| replayed id processed more than once | 0 | == 0 | PASS |
| staff !hold replayed with the same id toggles | 1 | <= 1 | PASS |
| flood: flagged positives shown on overlay | 0 | == 0 | PASS |
| flood: !hold submit latency (ms) | 0.234 | <= 100 | PASS |
| flood: !hold event published | 1 | == 1 | PASS |
| prompt: payloads that break out of the JSON data | 0 | == 0 | PASS |

## Evasion bypass rate by category (attacked message allowed, plain form was flagged)

| category | n | bypassed | bypass rate | ceiling |
|---|---|---|---|---|
| homoglyph | 179 | 110 | 61.5% (110/179, CI 54%-68%) | <= 10% |
| zero_width | 192 | 0 | 0.0% (0/192, CI 0%-2%) | <= 5% |
| leetspeak | 131 | 68 | 51.9% (68/131, CI 43%-60%) | <= 15% |
| split_words | 240 | 138 | 57.5% (138/240, CI 51%-64%) | <= 15% |
| mixed_script | 273 | 39 | 14.3% (39/273, CI 11%-19%) | <= 15% |
| encoding | 192 | 179 | 93.2% (179/192, CI 89%-96%) | informational |
| visual_tricks | 234 | 162 | 69.2% (162/234, CI 63%-75%) | <= 25% |
| url_obfuscation | 50 | 30 | 60.0% (30/50, CI 46%-72%) | <= 15% |
| upi_confusables | 142 | 26 | 18.3% (26/142, CI 13%-25%) | <= 15% |
| staff_claim | 48 | 0 | 0.0% (0/48, CI 0%-7%) | <= 5% |
| padding_dilution | 240 | 51 | 21.2% (51/240, CI 17%-27%) | <= 10% |
| prompt_injection (rules path) | 192 | 1 | 0.5% (1/192, CI 0%-3%) | <= 5% |
| prompt_injection (LLM obeys injection) | 192 | 40 | 20.8% (40/192, CI 16%-27%) | <= 10% |

## Techniques with at least one bypass

| category/technique | n | bypassed | rate |
|---|---|---|---|
| homoglyph/homoglyph_cyr_all | 45 | 37 | 0.8222 |
| homoglyph/homoglyph_cyr_partial | 44 | 20 | 0.4545 |
| homoglyph/homoglyph_greek | 45 | 28 | 0.6222 |
| homoglyph/homoglyph_mixed | 45 | 25 | 0.5556 |
| leetspeak/leet_heavy | 44 | 22 | 0.5 |
| leetspeak/leet_light | 43 | 13 | 0.3023 |
| leetspeak/leet_symbols | 44 | 33 | 0.75 |
| split_words/split_dotted | 48 | 37 | 0.7708 |
| split_words/split_midword | 48 | 12 | 0.25 |
| split_words/split_newline | 48 | 12 | 0.25 |
| split_words/split_spaced | 48 | 31 | 0.6458 |
| split_words/split_underscore | 48 | 46 | 0.9583 |
| mixed_script/mixed_deva_latin_words | 48 | 1 | 0.02083 |
| mixed_script/mixed_smallcaps | 45 | 38 | 0.8444 |
| encoding/enc_base64 | 48 | 48 | 1 |
| encoding/enc_hex | 48 | 46 | 0.9583 |
| encoding/enc_reversed | 48 | 47 | 0.9792 |
| encoding/enc_rot13 | 48 | 38 | 0.7917 |
| visual_tricks/vis_regional_letters | 43 | 30 | 0.6977 |
| visual_tricks/vis_rtl_override | 48 | 47 | 0.9792 |
| visual_tricks/vis_variation_selectors | 48 | 33 | 0.6875 |
| visual_tricks/vis_vertical | 48 | 28 | 0.5833 |
| visual_tricks/vis_zalgo | 47 | 24 | 0.5106 |
| url_obfuscation/url_comma_dot | 5 | 5 | 1 |
| url_obfuscation/url_dot_words | 5 | 2 | 0.4 |
| url_obfuscation/url_fullwidth_dot | 5 | 5 | 1 |
| url_obfuscation/url_idn_cyrillic | 5 | 5 | 1 |
| url_obfuscation/url_percent_host | 5 | 4 | 0.8 |
| url_obfuscation/url_spaced | 5 | 5 | 1 |
| url_obfuscation/url_split_two_msgs_style | 5 | 2 | 0.4 |
| url_obfuscation/url_userinfo | 5 | 2 | 0.4 |
| upi_confusables/upi_arabic_digits | 12 | 6 | 0.5 |
| upi_confusables/upi_cyr_handle | 12 | 4 | 0.3333 |
| upi_confusables/upi_cyr_local | 10 | 4 | 0.4 |
| upi_confusables/upi_deva_digits | 12 | 4 | 0.3333 |
| upi_confusables/upi_dot_for_at | 12 | 4 | 0.3333 |
| upi_confusables/upi_spelled_handle | 12 | 4 | 0.3333 |
| padding_dilution/pad_benign_before | 48 | 1 | 0.02083 |
| padding_dilution/pad_beyond_process_limit | 48 | 48 | 1 |
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
| homoglyph/abuse | 40 | 15 | 0.375 |
| homoglyph/doxxing | 43 | 15 | 0.3488 |
| homoglyph/fake_upi | 48 | 38 | 0.7917 |
| homoglyph/scam | 48 | 42 | 0.875 |
| leetspeak/abuse | 30 | 7 | 0.2333 |
| leetspeak/doxxing | 29 | 12 | 0.4138 |
| leetspeak/fake_upi | 36 | 22 | 0.6111 |
| leetspeak/scam | 36 | 27 | 0.75 |
| mixed_script/abuse | 62 | 10 | 0.1613 |
| mixed_script/doxxing | 67 | 8 | 0.1194 |
| mixed_script/fake_upi | 72 | 10 | 0.1389 |
| mixed_script/scam | 72 | 11 | 0.1528 |
| padding_dilution/abuse | 60 | 15 | 0.25 |
| padding_dilution/doxxing | 60 | 12 | 0.2 |
| padding_dilution/fake_upi | 60 | 12 | 0.2 |
| padding_dilution/scam | 60 | 12 | 0.2 |
| prompt_injection/abuse | 48 | 1 | 0.02083 |
| split_words/abuse | 60 | 42 | 0.7 |
| split_words/doxxing | 60 | 34 | 0.5667 |
| split_words/fake_upi | 60 | 25 | 0.4167 |
| split_words/scam | 60 | 37 | 0.6167 |
| upi_confusables/fake_upi | 142 | 26 | 0.1831 |
| url_obfuscation/scam | 50 | 30 | 0.6 |
| visual_tricks/abuse | 58 | 33 | 0.569 |
| visual_tricks/doxxing | 57 | 34 | 0.5965 |
| visual_tricks/fake_upi | 59 | 41 | 0.6949 |
| visual_tricks/scam | 60 | 54 | 0.9 |

## Robustness

| scenario | numbers |
|---|---|
| hold | {"spoof_messages": 56, "hold_events_from_nonstaff": 0, "mod_unhold_accepted": 0, "staff_hold_events": 2, "staff_hold_expected": 2, "denied_events_single": 21, "denied_events_flood_1000": 1000} |
| duplicates | {"positive_text_shown_after_replay": 0, "ids_with_more_than_one_verdict": 0, "reprocessed_after_cache_eviction": 1, "staff_cmd_replay_events": 1, "staff_cmd_after_viewer_same_id_events": 0} |
| flood | {"messages": 4000, "positives": 400, "submit_burst_s": 0.005, "hold_submit_ms": 0.234, "hold_events": 1, "positives_shown_on_overlay": 0, "positives_flagged": 100, "positives_dropped_unclassified": 300, "benign_dropped_unclassified": 2700, "dropped_overload": 3000} |
| malformed | {"cases": 30, "crashes": [], "rejected_at_contract": [], "no_verdict": [], "positive_bypass": ["nul_inside_slur", "slur_tail_beyond_limit", "newline_flood"], "positives": 10, "max_ms": 230.2, "classify_errors": 0, "shown_no_verdict": []} |
| malformed slowest | [{"name": "regex_hostile_dots", "ms": 230.2}, {"name": "slur_head_then_huge", "ms": 40.4}, {"name": "slur_tail_beyond_limit", "ms": 22.2}, {"name": "huge_1m", "ms": 21.5}] |
| prompt structure | {"payloads": 195, "breakouts": 0, "payload_in_system_prompt": 0} |
| logs | {"log_records": 12298, "log_leaks": 0, "events_scanned": 5462, "event_leaks": 0} |
| injection (obedient LLM) | {"llm_calls": 6, "plain_bases_reaching_llm": 10} |

## Bypass list (804; ids and techniques, message text via --show ID)

| category | id | technique | label | base kind |
|---|---|---|---|---|
| homoglyph | rt00011 | homoglyph_cyr_partial | scam | crypto |
| homoglyph | rt00013 | homoglyph_cyr_partial | scam | hack_tool |
| homoglyph | rt00014 | homoglyph_cyr_partial | scam | invite |
| homoglyph | rt00015 | homoglyph_cyr_partial | scam | kyc_bank |
| homoglyph | rt00018 | homoglyph_cyr_partial | scam | phishing |
| homoglyph | rt00019 | homoglyph_cyr_partial | scam | shortener |
| homoglyph | rt00020 | homoglyph_cyr_partial | scam | support_call |
| homoglyph | rt00021 | homoglyph_cyr_partial | scam | crypto |
| homoglyph | rt00022 | homoglyph_cyr_partial | scam | giveaway |
| homoglyph | rt00023 | homoglyph_cyr_partial | fake_upi | app_deeplink |
| homoglyph | rt00024 | homoglyph_cyr_partial | fake_upi | collect |
| homoglyph | rt00026 | homoglyph_cyr_partial | fake_upi | pay_to_id |
| homoglyph | rt00028 | homoglyph_cyr_partial | fake_upi | app_deeplink |
| homoglyph | rt00029 | homoglyph_cyr_partial | fake_upi | collect |
| homoglyph | rt00030 | homoglyph_cyr_partial | fake_upi | deeplink |
| homoglyph | rt00031 | homoglyph_cyr_partial | fake_upi | pay_to_id |
| homoglyph | rt00033 | homoglyph_cyr_partial | fake_upi | app_deeplink |
| homoglyph | rt00034 | homoglyph_cyr_partial | fake_upi | collect |
| homoglyph | rt00035 | homoglyph_cyr_partial | doxxing | address |
| homoglyph | rt00037 | homoglyph_cyr_partial | doxxing | phone_spoken |
| homoglyph | rt00045 | homoglyph_cyr_all | abuse | abbrev |
| homoglyph | rt00046 | homoglyph_cyr_all | abuse | english |
| homoglyph | rt00047 | homoglyph_cyr_all | abuse | profane_praise |
| homoglyph | rt00048 | homoglyph_cyr_all | abuse | sexual |
| homoglyph | rt00049 | homoglyph_cyr_all | abuse | slur_latn |
| homoglyph | rt00050 | homoglyph_cyr_all | abuse | threat |
| homoglyph | rt00051 | homoglyph_cyr_all | abuse | abbrev |
| homoglyph | rt00052 | homoglyph_cyr_all | abuse | english |
| homoglyph | rt00053 | homoglyph_cyr_all | abuse | profane_praise |
| homoglyph | rt00054 | homoglyph_cyr_all | abuse | sexual |
| homoglyph | rt00055 | homoglyph_cyr_all | scam | crypto |
| homoglyph | rt00057 | homoglyph_cyr_all | scam | hack_tool |
| homoglyph | rt00058 | homoglyph_cyr_all | scam | invite |
| homoglyph | rt00059 | homoglyph_cyr_all | scam | kyc_bank |
| homoglyph | rt00060 | homoglyph_cyr_all | scam | loan |
| homoglyph | rt00061 | homoglyph_cyr_all | scam | lottery |
| homoglyph | rt00062 | homoglyph_cyr_all | scam | phishing |
| homoglyph | rt00063 | homoglyph_cyr_all | scam | shortener |
| homoglyph | rt00064 | homoglyph_cyr_all | scam | support_call |
| homoglyph | rt00065 | homoglyph_cyr_all | scam | crypto |
| homoglyph | rt00066 | homoglyph_cyr_all | scam | giveaway |
| homoglyph | rt00067 | homoglyph_cyr_all | fake_upi | app_deeplink |
| homoglyph | rt00068 | homoglyph_cyr_all | fake_upi | collect |
| homoglyph | rt00069 | homoglyph_cyr_all | fake_upi | deeplink |
| homoglyph | rt00070 | homoglyph_cyr_all | fake_upi | pay_to_id |
| homoglyph | rt00072 | homoglyph_cyr_all | fake_upi | app_deeplink |
| homoglyph | rt00073 | homoglyph_cyr_all | fake_upi | collect |
| homoglyph | rt00074 | homoglyph_cyr_all | fake_upi | deeplink |
| homoglyph | rt00075 | homoglyph_cyr_all | fake_upi | pay_to_id |
| homoglyph | rt00077 | homoglyph_cyr_all | fake_upi | app_deeplink |
| homoglyph | rt00078 | homoglyph_cyr_all | fake_upi | collect |
| homoglyph | rt00079 | homoglyph_cyr_all | doxxing | address |
| homoglyph | rt00080 | homoglyph_cyr_all | doxxing | intent |
| homoglyph | rt00082 | homoglyph_cyr_all | doxxing | phone_spoken |
| homoglyph | rt00083 | homoglyph_cyr_all | doxxing | streamer_private |
| homoglyph | rt00084 | homoglyph_cyr_all | doxxing | address |
| homoglyph | rt00088 | homoglyph_cyr_all | doxxing | phone_spoken |
| homoglyph | rt00095 | homoglyph_greek | abuse | threat |
| homoglyph | rt00098 | homoglyph_greek | abuse | profane_praise |
| homoglyph | rt00099 | homoglyph_greek | abuse | sexual |
| homoglyph | rt00100 | homoglyph_greek | scam | crypto |
| homoglyph | rt00102 | homoglyph_greek | scam | hack_tool |
| homoglyph | rt00103 | homoglyph_greek | scam | invite |
| homoglyph | rt00104 | homoglyph_greek | scam | kyc_bank |
| homoglyph | rt00105 | homoglyph_greek | scam | loan |
| homoglyph | rt00106 | homoglyph_greek | scam | lottery |
| homoglyph | rt00107 | homoglyph_greek | scam | phishing |
| homoglyph | rt00108 | homoglyph_greek | scam | shortener |
| homoglyph | rt00109 | homoglyph_greek | scam | support_call |
| homoglyph | rt00110 | homoglyph_greek | scam | crypto |
| homoglyph | rt00111 | homoglyph_greek | scam | giveaway |
| homoglyph | rt00112 | homoglyph_greek | fake_upi | app_deeplink |
| homoglyph | rt00113 | homoglyph_greek | fake_upi | collect |
| homoglyph | rt00114 | homoglyph_greek | fake_upi | deeplink |
| homoglyph | rt00115 | homoglyph_greek | fake_upi | pay_to_id |
| homoglyph | rt00117 | homoglyph_greek | fake_upi | app_deeplink |
| homoglyph | rt00118 | homoglyph_greek | fake_upi | collect |
| homoglyph | rt00119 | homoglyph_greek | fake_upi | deeplink |
| homoglyph | rt00120 | homoglyph_greek | fake_upi | pay_to_id |
| homoglyph | rt00122 | homoglyph_greek | fake_upi | app_deeplink |
| homoglyph | rt00123 | homoglyph_greek | fake_upi | collect |
| homoglyph | rt00124 | homoglyph_greek | doxxing | address |
| homoglyph | rt00127 | homoglyph_greek | doxxing | phone_spoken |
| homoglyph | rt00130 | homoglyph_greek | doxxing | intent |
| homoglyph | rt00133 | homoglyph_greek | doxxing | phone_spoken |
| homoglyph | rt00140 | homoglyph_mixed | abuse | threat |
| homoglyph | rt00144 | homoglyph_mixed | abuse | sexual |
| homoglyph | rt00145 | homoglyph_mixed | scam | crypto |
| homoglyph | rt00147 | homoglyph_mixed | scam | hack_tool |
| homoglyph | rt00148 | homoglyph_mixed | scam | invite |
| homoglyph | rt00149 | homoglyph_mixed | scam | kyc_bank |
| homoglyph | rt00150 | homoglyph_mixed | scam | loan |
| homoglyph | rt00151 | homoglyph_mixed | scam | lottery |
| homoglyph | rt00152 | homoglyph_mixed | scam | phishing |
| homoglyph | rt00153 | homoglyph_mixed | scam | shortener |
| homoglyph | rt00154 | homoglyph_mixed | scam | support_call |
| homoglyph | rt00155 | homoglyph_mixed | scam | crypto |
| homoglyph | rt00156 | homoglyph_mixed | scam | giveaway |
| homoglyph | rt00157 | homoglyph_mixed | fake_upi | app_deeplink |
| homoglyph | rt00158 | homoglyph_mixed | fake_upi | collect |
| homoglyph | rt00160 | homoglyph_mixed | fake_upi | pay_to_id |
| homoglyph | rt00162 | homoglyph_mixed | fake_upi | app_deeplink |
| homoglyph | rt00163 | homoglyph_mixed | fake_upi | collect |
| homoglyph | rt00164 | homoglyph_mixed | fake_upi | deeplink |
| homoglyph | rt00165 | homoglyph_mixed | fake_upi | pay_to_id |
| homoglyph | rt00167 | homoglyph_mixed | fake_upi | app_deeplink |
| homoglyph | rt00168 | homoglyph_mixed | fake_upi | collect |
| homoglyph | rt00169 | homoglyph_mixed | doxxing | address |
| homoglyph | rt00172 | homoglyph_mixed | doxxing | phone_spoken |
| homoglyph | rt00175 | homoglyph_mixed | doxxing | intent |
| leetspeak | rt00384 | leet_light | scam | hack_tool |
| leetspeak | rt00385 | leet_light | scam | invite |
| leetspeak | rt00388 | leet_light | scam | lottery |
| leetspeak | rt00389 | leet_light | scam | phishing |
| leetspeak | rt00390 | leet_light | scam | shortener |
| leetspeak | rt00392 | leet_light | scam | crypto |
| leetspeak | rt00393 | leet_light | scam | giveaway |
| leetspeak | rt00395 | leet_light | fake_upi | collect |
| leetspeak | rt00396 | leet_light | fake_upi | deeplink |
| leetspeak | rt00401 | leet_light | fake_upi | deeplink |
| leetspeak | rt00402 | leet_light | fake_upi | pay_to_id |
| leetspeak | rt00406 | leet_light | doxxing | address |
| leetspeak | rt00408 | leet_light | doxxing | phone_spoken |
| leetspeak | rt00425 | leet_heavy | scam | crypto |
| leetspeak | rt00427 | leet_heavy | scam | hack_tool |
| leetspeak | rt00428 | leet_heavy | scam | invite |
| leetspeak | rt00430 | leet_heavy | scam | loan |
| leetspeak | rt00431 | leet_heavy | scam | lottery |
| leetspeak | rt00432 | leet_heavy | scam | phishing |
| leetspeak | rt00433 | leet_heavy | scam | shortener |
| leetspeak | rt00434 | leet_heavy | scam | support_call |
| leetspeak | rt00435 | leet_heavy | scam | crypto |
| leetspeak | rt00436 | leet_heavy | scam | giveaway |
| leetspeak | rt00437 | leet_heavy | fake_upi | app_deeplink |
| leetspeak | rt00438 | leet_heavy | fake_upi | collect |
| leetspeak | rt00439 | leet_heavy | fake_upi | deeplink |
| leetspeak | rt00440 | leet_heavy | fake_upi | pay_to_id |
| leetspeak | rt00442 | leet_heavy | fake_upi | app_deeplink |
| leetspeak | rt00444 | leet_heavy | fake_upi | deeplink |
| leetspeak | rt00445 | leet_heavy | fake_upi | pay_to_id |
| leetspeak | rt00448 | leet_heavy | fake_upi | collect |
| leetspeak | rt00449 | leet_heavy | doxxing | address |
| leetspeak | rt00451 | leet_heavy | doxxing | phone_spoken |
| leetspeak | rt00453 | leet_heavy | doxxing | address |
| leetspeak | rt00457 | leet_heavy | doxxing | phone_spoken |
| leetspeak | rt00459 | leet_symbols | abuse | abbrev |
| leetspeak | rt00460 | leet_symbols | abuse | english |
| leetspeak | rt00461 | leet_symbols | abuse | profane_praise |
| leetspeak | rt00462 | leet_symbols | abuse | sexual |
| leetspeak | rt00463 | leet_symbols | abuse | slur_latn |
| leetspeak | rt00465 | leet_symbols | abuse | abbrev |
| leetspeak | rt00467 | leet_symbols | abuse | profane_praise |
| leetspeak | rt00469 | leet_symbols | scam | crypto |
| leetspeak | rt00471 | leet_symbols | scam | hack_tool |
| leetspeak | rt00472 | leet_symbols | scam | invite |
| leetspeak | rt00474 | leet_symbols | scam | loan |
| leetspeak | rt00475 | leet_symbols | scam | lottery |
| leetspeak | rt00476 | leet_symbols | scam | phishing |
| leetspeak | rt00477 | leet_symbols | scam | shortener |
| leetspeak | rt00478 | leet_symbols | scam | support_call |
| leetspeak | rt00479 | leet_symbols | scam | crypto |
| leetspeak | rt00480 | leet_symbols | scam | giveaway |
| leetspeak | rt00481 | leet_symbols | fake_upi | app_deeplink |
| leetspeak | rt00482 | leet_symbols | fake_upi | collect |
| leetspeak | rt00483 | leet_symbols | fake_upi | deeplink |
| leetspeak | rt00484 | leet_symbols | fake_upi | pay_to_id |
| leetspeak | rt00486 | leet_symbols | fake_upi | app_deeplink |
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
| split_words | rt00504 | split_spaced | abuse | english |
| split_words | rt00506 | split_spaced | abuse | sexual |
| split_words | rt00507 | split_spaced | abuse | slur_deva |
| split_words | rt00508 | split_spaced | abuse | slur_latn |
| split_words | rt00509 | split_spaced | abuse | slur_regional |
| split_words | rt00510 | split_spaced | abuse | threat |
| split_words | rt00511 | split_spaced | abuse | abbrev |
| split_words | rt00512 | split_spaced | abuse | english |
| split_words | rt00514 | split_spaced | abuse | sexual |
| split_words | rt00515 | split_spaced | scam | crypto |
| split_words | rt00517 | split_spaced | scam | hack_tool |
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
| split_words | rt00552 | split_dotted | abuse | english |
| split_words | rt00554 | split_dotted | abuse | sexual |
| split_words | rt00555 | split_dotted | abuse | slur_deva |
| split_words | rt00556 | split_dotted | abuse | slur_latn |
| split_words | rt00557 | split_dotted | abuse | slur_regional |
| split_words | rt00558 | split_dotted | abuse | threat |
| split_words | rt00559 | split_dotted | abuse | abbrev |
| split_words | rt00560 | split_dotted | abuse | english |
| split_words | rt00562 | split_dotted | abuse | sexual |
| split_words | rt00563 | split_dotted | scam | crypto |
| split_words | rt00564 | split_dotted | scam | giveaway |
| split_words | rt00565 | split_dotted | scam | hack_tool |
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
| split_words | rt00648 | split_underscore | abuse | english |
| split_words | rt00650 | split_underscore | abuse | sexual |
| split_words | rt00651 | split_underscore | abuse | slur_deva |
| split_words | rt00652 | split_underscore | abuse | slur_latn |
| split_words | rt00653 | split_underscore | abuse | slur_regional |
| split_words | rt00654 | split_underscore | abuse | threat |
| split_words | rt00655 | split_underscore | abuse | abbrev |
| split_words | rt00656 | split_underscore | abuse | english |
| split_words | rt00658 | split_underscore | abuse | sexual |
| split_words | rt00659 | split_underscore | scam | crypto |
| split_words | rt00660 | split_underscore | scam | giveaway |
| split_words | rt00661 | split_underscore | scam | hack_tool |
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
| mixed_script | rt00923 | mixed_smallcaps | abuse | abbrev |
| mixed_script | rt00924 | mixed_smallcaps | abuse | english |
| mixed_script | rt00925 | mixed_smallcaps | abuse | profane_praise |
| mixed_script | rt00926 | mixed_smallcaps | abuse | sexual |
| mixed_script | rt00927 | mixed_smallcaps | abuse | slur_latn |
| mixed_script | rt00928 | mixed_smallcaps | abuse | threat |
| mixed_script | rt00929 | mixed_smallcaps | abuse | abbrev |
| mixed_script | rt00930 | mixed_smallcaps | abuse | english |
| mixed_script | rt00931 | mixed_smallcaps | abuse | profane_praise |
| mixed_script | rt00932 | mixed_smallcaps | abuse | sexual |
| mixed_script | rt00933 | mixed_smallcaps | scam | crypto |
| mixed_script | rt00935 | mixed_smallcaps | scam | hack_tool |
| mixed_script | rt00936 | mixed_smallcaps | scam | invite |
| mixed_script | rt00937 | mixed_smallcaps | scam | kyc_bank |
| mixed_script | rt00938 | mixed_smallcaps | scam | loan |
| mixed_script | rt00939 | mixed_smallcaps | scam | lottery |
| mixed_script | rt00940 | mixed_smallcaps | scam | phishing |
| mixed_script | rt00941 | mixed_smallcaps | scam | shortener |
| mixed_script | rt00942 | mixed_smallcaps | scam | support_call |
| mixed_script | rt00943 | mixed_smallcaps | scam | crypto |
| mixed_script | rt00944 | mixed_smallcaps | scam | giveaway |
| mixed_script | rt00945 | mixed_smallcaps | fake_upi | app_deeplink |
| mixed_script | rt00946 | mixed_smallcaps | fake_upi | collect |
| mixed_script | rt00947 | mixed_smallcaps | fake_upi | deeplink |
| mixed_script | rt00948 | mixed_smallcaps | fake_upi | pay_to_id |
| mixed_script | rt00950 | mixed_smallcaps | fake_upi | app_deeplink |
| mixed_script | rt00951 | mixed_smallcaps | fake_upi | collect |
| mixed_script | rt00952 | mixed_smallcaps | fake_upi | deeplink |
| mixed_script | rt00953 | mixed_smallcaps | fake_upi | pay_to_id |
| mixed_script | rt00955 | mixed_smallcaps | fake_upi | app_deeplink |
| mixed_script | rt00956 | mixed_smallcaps | fake_upi | collect |
| mixed_script | rt00957 | mixed_smallcaps | doxxing | address |
| mixed_script | rt00958 | mixed_smallcaps | doxxing | intent |
| mixed_script | rt00960 | mixed_smallcaps | doxxing | phone_spoken |
| mixed_script | rt00961 | mixed_smallcaps | doxxing | streamer_private |
| mixed_script | rt00962 | mixed_smallcaps | doxxing | address |
| mixed_script | rt00963 | mixed_smallcaps | doxxing | intent |
| mixed_script | rt00966 | mixed_smallcaps | doxxing | phone_spoken |
| mixed_script | rt01008 | mixed_deva_latin_words | doxxing | phone_spoken |
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
| visual_tricks | rt01208 | vis_rtl_override | abuse | abbrev |
| visual_tricks | rt01209 | vis_rtl_override | abuse | english |
| visual_tricks | rt01210 | vis_rtl_override | abuse | profane_praise |
| visual_tricks | rt01211 | vis_rtl_override | abuse | sexual |
| visual_tricks | rt01212 | vis_rtl_override | abuse | slur_deva |
| visual_tricks | rt01213 | vis_rtl_override | abuse | slur_latn |
| visual_tricks | rt01214 | vis_rtl_override | abuse | slur_regional |
| visual_tricks | rt01215 | vis_rtl_override | abuse | threat |
| visual_tricks | rt01216 | vis_rtl_override | abuse | abbrev |
| visual_tricks | rt01217 | vis_rtl_override | abuse | english |
| visual_tricks | rt01218 | vis_rtl_override | abuse | profane_praise |
| visual_tricks | rt01219 | vis_rtl_override | abuse | sexual |
| visual_tricks | rt01220 | vis_rtl_override | scam | crypto |
| visual_tricks | rt01221 | vis_rtl_override | scam | giveaway |
| visual_tricks | rt01222 | vis_rtl_override | scam | hack_tool |
| visual_tricks | rt01223 | vis_rtl_override | scam | invite |
| visual_tricks | rt01224 | vis_rtl_override | scam | kyc_bank |
| visual_tricks | rt01225 | vis_rtl_override | scam | loan |
| visual_tricks | rt01226 | vis_rtl_override | scam | lottery |
| visual_tricks | rt01227 | vis_rtl_override | scam | phishing |
| visual_tricks | rt01228 | vis_rtl_override | scam | shortener |
| visual_tricks | rt01229 | vis_rtl_override | scam | support_call |
| visual_tricks | rt01230 | vis_rtl_override | scam | crypto |
| visual_tricks | rt01231 | vis_rtl_override | scam | giveaway |
| visual_tricks | rt01232 | vis_rtl_override | fake_upi | app_deeplink |
| visual_tricks | rt01233 | vis_rtl_override | fake_upi | collect |
| visual_tricks | rt01234 | vis_rtl_override | fake_upi | deeplink |
| visual_tricks | rt01235 | vis_rtl_override | fake_upi | pay_to_id |
| visual_tricks | rt01236 | vis_rtl_override | fake_upi | phone_upi |
| visual_tricks | rt01237 | vis_rtl_override | fake_upi | app_deeplink |
| visual_tricks | rt01238 | vis_rtl_override | fake_upi | collect |
| visual_tricks | rt01239 | vis_rtl_override | fake_upi | deeplink |
| visual_tricks | rt01240 | vis_rtl_override | fake_upi | pay_to_id |
| visual_tricks | rt01241 | vis_rtl_override | fake_upi | phone_upi |
| visual_tricks | rt01242 | vis_rtl_override | fake_upi | app_deeplink |
| visual_tricks | rt01243 | vis_rtl_override | fake_upi | collect |
| visual_tricks | rt01244 | vis_rtl_override | doxxing | address |
| visual_tricks | rt01245 | vis_rtl_override | doxxing | intent |
| visual_tricks | rt01246 | vis_rtl_override | doxxing | phone |
| visual_tricks | rt01247 | vis_rtl_override | doxxing | phone_embedded |
| visual_tricks | rt01248 | vis_rtl_override | doxxing | phone_spoken |
| visual_tricks | rt01249 | vis_rtl_override | doxxing | streamer_private |
| visual_tricks | rt01250 | vis_rtl_override | doxxing | address |
| visual_tricks | rt01251 | vis_rtl_override | doxxing | intent |
| visual_tricks | rt01252 | vis_rtl_override | doxxing | phone |
| visual_tricks | rt01253 | vis_rtl_override | doxxing | phone_embedded |
| visual_tricks | rt01254 | vis_rtl_override | doxxing | phone_spoken |
| visual_tricks | rt01260 | vis_zalgo | abuse | slur_deva |
| visual_tricks | rt01262 | vis_zalgo | abuse | slur_regional |
| visual_tricks | rt01268 | vis_zalgo | scam | crypto |
| visual_tricks | rt01270 | vis_zalgo | scam | hack_tool |
| visual_tricks | rt01271 | vis_zalgo | scam | invite |
| visual_tricks | rt01272 | vis_zalgo | scam | kyc_bank |
| visual_tricks | rt01273 | vis_zalgo | scam | loan |
| visual_tricks | rt01274 | vis_zalgo | scam | lottery |
| visual_tricks | rt01275 | vis_zalgo | scam | phishing |
| visual_tricks | rt01276 | vis_zalgo | scam | shortener |
| visual_tricks | rt01277 | vis_zalgo | scam | support_call |
| visual_tricks | rt01278 | vis_zalgo | scam | crypto |
| visual_tricks | rt01279 | vis_zalgo | scam | giveaway |
| visual_tricks | rt01280 | vis_zalgo | fake_upi | app_deeplink |
| visual_tricks | rt01281 | vis_zalgo | fake_upi | collect |
| visual_tricks | rt01282 | vis_zalgo | fake_upi | deeplink |
| visual_tricks | rt01285 | vis_zalgo | fake_upi | app_deeplink |
| visual_tricks | rt01286 | vis_zalgo | fake_upi | collect |
| visual_tricks | rt01287 | vis_zalgo | fake_upi | deeplink |
| visual_tricks | rt01288 | vis_zalgo | fake_upi | pay_to_id |
| visual_tricks | rt01290 | vis_zalgo | fake_upi | app_deeplink |
| visual_tricks | rt01291 | vis_zalgo | fake_upi | collect |
| visual_tricks | rt01292 | vis_zalgo | doxxing | address |
| visual_tricks | rt01295 | vis_zalgo | doxxing | phone_spoken |
| visual_tricks | rt01304 | vis_regional_letters | abuse | english |
| visual_tricks | rt01305 | vis_regional_letters | abuse | profane_praise |
| visual_tricks | rt01306 | vis_regional_letters | abuse | sexual |
| visual_tricks | rt01307 | vis_regional_letters | abuse | slur_latn |
| visual_tricks | rt01308 | vis_regional_letters | abuse | threat |
| visual_tricks | rt01309 | vis_regional_letters | abuse | abbrev |
| visual_tricks | rt01312 | vis_regional_letters | abuse | sexual |
| visual_tricks | rt01313 | vis_regional_letters | scam | crypto |
| visual_tricks | rt01315 | vis_regional_letters | scam | hack_tool |
| visual_tricks | rt01319 | vis_regional_letters | scam | lottery |
| visual_tricks | rt01320 | vis_regional_letters | scam | phishing |
| visual_tricks | rt01321 | vis_regional_letters | scam | shortener |
| visual_tricks | rt01322 | vis_regional_letters | scam | support_call |
| visual_tricks | rt01323 | vis_regional_letters | scam | crypto |
| visual_tricks | rt01324 | vis_regional_letters | scam | giveaway |
| visual_tricks | rt01325 | vis_regional_letters | fake_upi | app_deeplink |
| visual_tricks | rt01326 | vis_regional_letters | fake_upi | collect |
| visual_tricks | rt01328 | vis_regional_letters | fake_upi | pay_to_id |
| visual_tricks | rt01330 | vis_regional_letters | fake_upi | app_deeplink |
| visual_tricks | rt01331 | vis_regional_letters | fake_upi | collect |
| visual_tricks | rt01332 | vis_regional_letters | fake_upi | deeplink |
| visual_tricks | rt01333 | vis_regional_letters | fake_upi | pay_to_id |
| visual_tricks | rt01335 | vis_regional_letters | fake_upi | collect |
| visual_tricks | rt01336 | vis_regional_letters | doxxing | address |
| visual_tricks | rt01337 | vis_regional_letters | doxxing | intent |
| visual_tricks | rt01338 | vis_regional_letters | doxxing | phone_spoken |
| visual_tricks | rt01339 | vis_regional_letters | doxxing | streamer_private |
| visual_tricks | rt01340 | vis_regional_letters | doxxing | address |
| visual_tricks | rt01341 | vis_regional_letters | doxxing | intent |
| visual_tricks | rt01344 | vis_regional_letters | doxxing | phone_spoken |
| visual_tricks | rt01346 | vis_vertical | abuse | abbrev |
| visual_tricks | rt01347 | vis_vertical | abuse | english |
| visual_tricks | rt01349 | vis_vertical | abuse | sexual |
| visual_tricks | rt01350 | vis_vertical | abuse | slur_deva |
| visual_tricks | rt01351 | vis_vertical | abuse | slur_latn |
| visual_tricks | rt01352 | vis_vertical | abuse | slur_regional |
| visual_tricks | rt01353 | vis_vertical | abuse | threat |
| visual_tricks | rt01354 | vis_vertical | abuse | abbrev |
| visual_tricks | rt01355 | vis_vertical | abuse | english |
| visual_tricks | rt01357 | vis_vertical | abuse | sexual |
| visual_tricks | rt01358 | vis_vertical | scam | crypto |
| visual_tricks | rt01360 | vis_vertical | scam | hack_tool |
| visual_tricks | rt01361 | vis_vertical | scam | invite |
| visual_tricks | rt01362 | vis_vertical | scam | kyc_bank |
| visual_tricks | rt01363 | vis_vertical | scam | loan |
| visual_tricks | rt01364 | vis_vertical | scam | lottery |
| visual_tricks | rt01365 | vis_vertical | scam | phishing |
| visual_tricks | rt01366 | vis_vertical | scam | shortener |
| visual_tricks | rt01367 | vis_vertical | scam | support_call |
| visual_tricks | rt01368 | vis_vertical | scam | crypto |
| visual_tricks | rt01369 | vis_vertical | scam | giveaway |
| visual_tricks | rt01382 | vis_vertical | doxxing | address |
| visual_tricks | rt01383 | vis_vertical | doxxing | intent |
| visual_tricks | rt01386 | vis_vertical | doxxing | phone_spoken |
| visual_tricks | rt01387 | vis_vertical | doxxing | streamer_private |
| visual_tricks | rt01388 | vis_vertical | doxxing | address |
| visual_tricks | rt01389 | vis_vertical | doxxing | intent |
| visual_tricks | rt01392 | vis_vertical | doxxing | phone_spoken |
| visual_tricks | rt01398 | vis_variation_selectors | abuse | slur_deva |
| visual_tricks | rt01400 | vis_variation_selectors | abuse | slur_regional |
| visual_tricks | rt01406 | vis_variation_selectors | scam | crypto |
| visual_tricks | rt01407 | vis_variation_selectors | scam | giveaway |
| visual_tricks | rt01408 | vis_variation_selectors | scam | hack_tool |
| visual_tricks | rt01409 | vis_variation_selectors | scam | invite |
| visual_tricks | rt01410 | vis_variation_selectors | scam | kyc_bank |
| visual_tricks | rt01411 | vis_variation_selectors | scam | loan |
| visual_tricks | rt01412 | vis_variation_selectors | scam | lottery |
| visual_tricks | rt01413 | vis_variation_selectors | scam | phishing |
| visual_tricks | rt01414 | vis_variation_selectors | scam | shortener |
| visual_tricks | rt01415 | vis_variation_selectors | scam | support_call |
| visual_tricks | rt01416 | vis_variation_selectors | scam | crypto |
| visual_tricks | rt01417 | vis_variation_selectors | scam | giveaway |
| visual_tricks | rt01418 | vis_variation_selectors | fake_upi | app_deeplink |
| visual_tricks | rt01419 | vis_variation_selectors | fake_upi | collect |
| visual_tricks | rt01420 | vis_variation_selectors | fake_upi | deeplink |
| visual_tricks | rt01421 | vis_variation_selectors | fake_upi | pay_to_id |
| visual_tricks | rt01422 | vis_variation_selectors | fake_upi | phone_upi |
| visual_tricks | rt01423 | vis_variation_selectors | fake_upi | app_deeplink |
| visual_tricks | rt01424 | vis_variation_selectors | fake_upi | collect |
| visual_tricks | rt01425 | vis_variation_selectors | fake_upi | deeplink |
| visual_tricks | rt01426 | vis_variation_selectors | fake_upi | pay_to_id |
| visual_tricks | rt01427 | vis_variation_selectors | fake_upi | phone_upi |
| visual_tricks | rt01428 | vis_variation_selectors | fake_upi | app_deeplink |
| visual_tricks | rt01429 | vis_variation_selectors | fake_upi | collect |
| visual_tricks | rt01430 | vis_variation_selectors | doxxing | address |
| visual_tricks | rt01432 | vis_variation_selectors | doxxing | phone |
| visual_tricks | rt01433 | vis_variation_selectors | doxxing | phone_embedded |
| visual_tricks | rt01434 | vis_variation_selectors | doxxing | phone_spoken |
| visual_tricks | rt01438 | vis_variation_selectors | doxxing | phone |
| visual_tricks | rt01439 | vis_variation_selectors | doxxing | phone_embedded |
| visual_tricks | rt01441 | vis_variation_selectors | doxxing | streamer_private |
| url_obfuscation | rt01447 | url_dot_words | scam | invite |
| url_obfuscation | rt01449 | url_dot_words | scam | loan |
| url_obfuscation | rt01452 | url_spaced | scam | invite |
| url_obfuscation | rt01453 | url_spaced | scam | kyc_bank |
| url_obfuscation | rt01454 | url_spaced | scam | loan |
| url_obfuscation | rt01455 | url_spaced | scam | lottery |
| url_obfuscation | rt01456 | url_spaced | scam | phishing |
| url_obfuscation | rt01457 | url_idn_cyrillic | scam | invite |
| url_obfuscation | rt01458 | url_idn_cyrillic | scam | kyc_bank |
| url_obfuscation | rt01459 | url_idn_cyrillic | scam | loan |
| url_obfuscation | rt01460 | url_idn_cyrillic | scam | lottery |
| url_obfuscation | rt01461 | url_idn_cyrillic | scam | phishing |
| url_obfuscation | rt01463 | url_userinfo | scam | kyc_bank |
| url_obfuscation | rt01466 | url_userinfo | scam | phishing |
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
| upi_confusables | rt01541 | upi_cyr_handle | fake_upi | collect |
| upi_confusables | rt01546 | upi_cyr_handle | fake_upi | collect |
| upi_confusables | rt01548 | upi_cyr_handle | fake_upi | pay_to_id |
| upi_confusables | rt01551 | upi_cyr_handle | fake_upi | collect |
| upi_confusables | rt01553 | upi_cyr_local | fake_upi | collect |
| upi_confusables | rt01557 | upi_cyr_local | fake_upi | collect |
| upi_confusables | rt01559 | upi_cyr_local | fake_upi | pay_to_id |
| upi_confusables | rt01561 | upi_cyr_local | fake_upi | collect |
| upi_confusables | rt01575 | upi_arabic_digits | fake_upi | collect |
| upi_confusables | rt01578 | upi_arabic_digits | fake_upi | phone_upi |
| upi_confusables | rt01580 | upi_arabic_digits | fake_upi | collect |
| upi_confusables | rt01582 | upi_arabic_digits | fake_upi | pay_to_id |
| upi_confusables | rt01583 | upi_arabic_digits | fake_upi | phone_upi |
| upi_confusables | rt01585 | upi_arabic_digits | fake_upi | collect |
| upi_confusables | rt01587 | upi_deva_digits | fake_upi | collect |
| upi_confusables | rt01592 | upi_deva_digits | fake_upi | collect |
| upi_confusables | rt01594 | upi_deva_digits | fake_upi | pay_to_id |
| upi_confusables | rt01597 | upi_deva_digits | fake_upi | collect |
| upi_confusables | rt01599 | upi_spelled_handle | fake_upi | collect |
| upi_confusables | rt01604 | upi_spelled_handle | fake_upi | collect |
| upi_confusables | rt01606 | upi_spelled_handle | fake_upi | pay_to_id |
| upi_confusables | rt01609 | upi_spelled_handle | fake_upi | collect |
| upi_confusables | rt01623 | upi_dot_for_at | fake_upi | collect |
| upi_confusables | rt01628 | upi_dot_for_at | fake_upi | collect |
| upi_confusables | rt01630 | upi_dot_for_at | fake_upi | pay_to_id |
| upi_confusables | rt01633 | upi_dot_for_at | fake_upi | collect |
| padding_dilution | rt01691 | pad_benign_before | abuse | english |
| padding_dilution | rt01778 | pad_beyond_process_limit | abuse | abbrev |
| padding_dilution | rt01779 | pad_beyond_process_limit | abuse | english |
| padding_dilution | rt01780 | pad_beyond_process_limit | abuse | profane_praise |
| padding_dilution | rt01781 | pad_beyond_process_limit | abuse | sexual |
| padding_dilution | rt01782 | pad_beyond_process_limit | abuse | slur_deva |
| padding_dilution | rt01783 | pad_beyond_process_limit | abuse | slur_latn |
| padding_dilution | rt01784 | pad_beyond_process_limit | abuse | slur_regional |
| padding_dilution | rt01785 | pad_beyond_process_limit | abuse | threat |
| padding_dilution | rt01786 | pad_beyond_process_limit | abuse | abbrev |
| padding_dilution | rt01787 | pad_beyond_process_limit | abuse | english |
| padding_dilution | rt01788 | pad_beyond_process_limit | abuse | profane_praise |
| padding_dilution | rt01789 | pad_beyond_process_limit | abuse | sexual |
| padding_dilution | rt01790 | pad_beyond_process_limit | scam | crypto |
| padding_dilution | rt01791 | pad_beyond_process_limit | scam | giveaway |
| padding_dilution | rt01792 | pad_beyond_process_limit | scam | hack_tool |
| padding_dilution | rt01793 | pad_beyond_process_limit | scam | invite |
| padding_dilution | rt01794 | pad_beyond_process_limit | scam | kyc_bank |
| padding_dilution | rt01795 | pad_beyond_process_limit | scam | loan |
| padding_dilution | rt01796 | pad_beyond_process_limit | scam | lottery |
| padding_dilution | rt01797 | pad_beyond_process_limit | scam | phishing |
| padding_dilution | rt01798 | pad_beyond_process_limit | scam | shortener |
| padding_dilution | rt01799 | pad_beyond_process_limit | scam | support_call |
| padding_dilution | rt01800 | pad_beyond_process_limit | scam | crypto |
| padding_dilution | rt01801 | pad_beyond_process_limit | scam | giveaway |
| padding_dilution | rt01802 | pad_beyond_process_limit | fake_upi | app_deeplink |
| padding_dilution | rt01803 | pad_beyond_process_limit | fake_upi | collect |
| padding_dilution | rt01804 | pad_beyond_process_limit | fake_upi | deeplink |
| padding_dilution | rt01805 | pad_beyond_process_limit | fake_upi | pay_to_id |
| padding_dilution | rt01806 | pad_beyond_process_limit | fake_upi | phone_upi |
| padding_dilution | rt01807 | pad_beyond_process_limit | fake_upi | app_deeplink |
| padding_dilution | rt01808 | pad_beyond_process_limit | fake_upi | collect |
| padding_dilution | rt01809 | pad_beyond_process_limit | fake_upi | deeplink |
| padding_dilution | rt01810 | pad_beyond_process_limit | fake_upi | pay_to_id |
| padding_dilution | rt01811 | pad_beyond_process_limit | fake_upi | phone_upi |
| padding_dilution | rt01812 | pad_beyond_process_limit | fake_upi | app_deeplink |
| padding_dilution | rt01813 | pad_beyond_process_limit | fake_upi | collect |
| padding_dilution | rt01814 | pad_beyond_process_limit | doxxing | address |
| padding_dilution | rt01815 | pad_beyond_process_limit | doxxing | intent |
| padding_dilution | rt01816 | pad_beyond_process_limit | doxxing | phone |
| padding_dilution | rt01817 | pad_beyond_process_limit | doxxing | phone_embedded |
| padding_dilution | rt01818 | pad_beyond_process_limit | doxxing | phone_spoken |
| padding_dilution | rt01819 | pad_beyond_process_limit | doxxing | streamer_private |
| padding_dilution | rt01820 | pad_beyond_process_limit | doxxing | address |
| padding_dilution | rt01821 | pad_beyond_process_limit | doxxing | intent |
| padding_dilution | rt01822 | pad_beyond_process_limit | doxxing | phone |
| padding_dilution | rt01823 | pad_beyond_process_limit | doxxing | phone_embedded |
| padding_dilution | rt01824 | pad_beyond_process_limit | doxxing | phone_spoken |
| padding_dilution | rt01825 | pad_beyond_process_limit | doxxing | streamer_private |
| padding_dilution | rt01835 | pad_emoji_flood | abuse | english |
| padding_dilution | rt01883 | pad_repeat_clean | abuse | english |
| prompt_injection | rt02070 | inject_json_breakout | abuse | slur_deva |

## Bypass list, obedient LLM (40)

| id | technique | label | base kind | reached LLM |
|---|---|---|---|---|
| rt01922 | inject_prefix | abuse | abbrev | True |
| rt01925 | inject_prefix | abuse | sexual | True |
| rt01926 | inject_prefix | abuse | slur_deva | True |
| rt01927 | inject_prefix | abuse | slur_latn | True |
| rt01930 | inject_prefix | abuse | abbrev | True |
| rt01931 | inject_prefix | abuse | english | True |
| rt01933 | inject_prefix | abuse | sexual | True |
| rt01937 | inject_prefix | scam | invite | True |
| rt01939 | inject_prefix | scam | loan | True |
| rt01943 | inject_prefix | scam | support_call | True |
| rt01970 | inject_suffix | abuse | abbrev | True |
| rt01973 | inject_suffix | abuse | sexual | True |
| rt01974 | inject_suffix | abuse | slur_deva | True |
| rt01975 | inject_suffix | abuse | slur_latn | True |
| rt01978 | inject_suffix | abuse | abbrev | True |
| rt01979 | inject_suffix | abuse | english | True |
| rt01981 | inject_suffix | abuse | sexual | True |
| rt01985 | inject_suffix | scam | invite | True |
| rt01987 | inject_suffix | scam | loan | True |
| rt01991 | inject_suffix | scam | support_call | True |
| rt02018 | inject_both | abuse | abbrev | True |
| rt02021 | inject_both | abuse | sexual | True |
| rt02022 | inject_both | abuse | slur_deva | True |
| rt02023 | inject_both | abuse | slur_latn | True |
| rt02026 | inject_both | abuse | abbrev | True |
| rt02027 | inject_both | abuse | english | True |
| rt02029 | inject_both | abuse | sexual | True |
| rt02033 | inject_both | scam | invite | True |
| rt02035 | inject_both | scam | loan | True |
| rt02039 | inject_both | scam | support_call | True |
| rt02066 | inject_json_breakout | abuse | abbrev | True |
| rt02069 | inject_json_breakout | abuse | sexual | True |
| rt02070 | inject_json_breakout | abuse | slur_deva | True |
| rt02071 | inject_json_breakout | abuse | slur_latn | True |
| rt02074 | inject_json_breakout | abuse | abbrev | True |
| rt02075 | inject_json_breakout | abuse | english | True |
| rt02077 | inject_json_breakout | abuse | sexual | True |
| rt02081 | inject_json_breakout | scam | invite | True |
| rt02083 | inject_json_breakout | scam | loan | True |
| rt02087 | inject_json_breakout | scam | support_call | True |

## Notes

- The Groq stage is a stand-in (no key, offline): ObedientLlm answers the truth label except where the text carries an injected instruction, which it obeys. It measures how much the pipeline's structure lets a fooled LLM release a flagged message, not Groq's real robustness.
- Bypass is measured only over bases the plain classifier already flagged; bases it misses are skipped (counted in the JSON as plain_not_flagged_skipped), so the rate isolates the evasion.
- Without an LLM, borderline messages become HOLD_FOR_MOD and count as caught (they are hidden from the overlay).
- 'encoding' (base64, rot13, hex, reversed) is informational: a viewer cannot read those either.
- Duplicate-id and id-cache-eviction numbers: reprocessed_after_cache_eviction is informational (ids are platform-assigned).
