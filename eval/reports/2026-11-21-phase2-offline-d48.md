# phase2-offline-d48 report (2026-11-21)

Result: **FAIL**

**Gate scope: clean+light (D-48); heavy-level recall is reported but not gated**

## Run

- dataset: {"clips": 50, "dataset": "ss_phase2_video", "generator_version": "specimen-video-1.2", "images": 1080, "manifest_sha256": "f3439cbceefffaa5", "n_per_bucket": 40, "seed": 1234}
- git_rev: 8a1e6e353205
- hardware: {"cpu": "13th Gen Intel(R) Core(TM) i5-13450HX", "gpu": "NVIDIA GeForce RTX 3050 6GB Laptop GPU, 581.86"}
- legs_run: ["offline"]
- offline_run: {"at": "2026-11-21T02:31:19+0000", "git_rev": "8a1e6e353205", "hardware": {"cpu": "13th Gen Intel(R) Core(TM) i5-13450HX", "gpu": "NVIDIA GeForce RTX 3050 6GB Laptop GPU, 581.86"}, "vision_files": 63, "vision_newest_mtime": "2026-11-21T00:38:16+0000", "vision_sha256": "1d1e3bc1ea66"}
- profile: dev
- scoring: {"cover_min": 0.9, "definition": "a visible target frame is a hit when the masks the production planner produces cover >= cover_min of its quad (allowlisted faces: < 10 % covered); clips use the deadline engine's nearest-result rule", "gate_levels": ["clean", "light"], "gate_sources": ["img", "clip"], "min_n": 20, "steady_s": 2.0, "warmup_rule": "allowlisted faces on clips are scored from 1 s after the track first appears (settled, the gate row); the detector masks an allowlisted face on purpose while identity votes accrue (spec 5.5). steady = from 2 s, all = every frame; both are reported next to it.", "warmup_s": 1.0}
- seeded_defect: n/a

## Checks

| check | value | target | result |
|---|---|---|---|
| offline: all expected detectors loaded and ran | 6 detectors | == 6 detectors | PASS |
| offline: detector exceptions during the run | 0 calls | == 0 calls | PASS (failed lane runs: 0 (the engine would slate those frames)) |
| offline: Faces masked (non-allowlisted) recall [stills] | 1 | >= 0.95 | PASS (n=107, CI [0.965, 1.000]) |
| offline: Faces masked (non-allowlisted) recall [clips] | 1 | >= 0.95 | PASS (n=900, CI [0.996, 1.000]) |
| offline: Allowlisted faces left visible recall [stills] | 0.2083 | >= 0.95 | FAIL (n=24, CI [0.092, 0.405]) |
| offline: Allowlisted faces left visible recall [clips] | 0.75 | >= 0.95 | FAIL (n=1080, CI [0.723, 0.775], settled = from 1 s after the track first appears (warm-up rule); steady (>= 2 s) 0.750, all frames 0.710) |
| offline: Aadhaar card recall [stills] | 1 | >= 0.95 | PASS (n=24, CI [0.862, 1.000]) |
| offline: Aadhaar card recall [clips] | 1 | >= 0.95 | PASS (n=300, CI [0.987, 1.000]) |
| offline: PAN card recall [stills] | 1 | >= 0.95 | PASS (n=24, CI [0.862, 1.000]) |
| offline: PAN card recall [clips] | 1 | >= 0.95 | PASS (n=300, CI [0.987, 1.000]) |
| offline: UPI QR recall [stills] | 1 | >= 0.98 | PASS (n=24, CI [0.862, 1.000]) |
| offline: UPI QR recall [clips] | 1 | >= 0.98 | PASS (n=300, CI [0.987, 1.000]) |
| offline: Signboard text recall [stills] | 1 | >= 0.85 | PASS (n=275, CI [0.986, 1.000]) |
| offline: Signboard text recall [clips] | 1 | >= 0.85 | PASS (n=1050, CI [0.996, 1.000]) |
| offline: Phone number (on-screen) recall [stills] | 1 | >= 0.9 | PASS (n=31, CI [0.890, 1.000]) |
| offline: Phone number (on-screen) recall [clips] | 1 | >= 0.9 | PASS (n=450, CI [0.992, 1.000]) |
| offline: PIN code (on-screen) recall [stills] | 0.9118 | >= 0.9 | PASS (n=34, CI [0.770, 0.970]) |
| offline: UPI ID (on-screen) recall [stills] | 1 | >= 0.9 | PASS (n=36, CI [0.904, 1.000]) |
| offline: Email (on-screen) recall [stills] | 1 | >= 0.9 | PASS (n=33, CI [0.896, 1.000]) |
| offline: OTP screen recall [stills] | 0.9167 | >= 0.85 | PASS (n=24, CI [0.742, 0.977]) |
| offline: OTP screen recall [clips] | 1 | >= 0.85 | PASS (n=300, CI [0.987, 1.000]) |
| offline: Game IDs (player ID, room ID, room password) recall [stills] | 1 | >= 0.9 | PASS (n=134, CI [0.972, 1.000]) |
| offline: Game IDs (player ID, room ID, room password) recall [clips] | 1 | >= 0.9 | PASS (n=1500, CI [0.997, 1.000]) |
| offline: Number plate recall [stills] | 1 | >= 0.85 | PASS (n=24, CI [0.862, 1.000]) |
| offline: Number plate recall [clips] | 1 | >= 0.85 | PASS (n=300, CI [0.987, 1.000]) |
| offline: ACCURATE lane p95 latency at the production OCR budget | 530.2 ms | <= 450 ms | FAIL (ocr.rec_budget_ms 150, 135 lane runs on 135 stills, CPU, offline (the machine may be shared: latency is indicative); the 5000 ms lift is used for the recall pass only) |
| offline: mask flicker on static targets | 0.01875 | <= 0.02 | PASS (3/160 windows, 21 static clips) |

## Per-category frame-level recall (levels clean+light; hit = masks cover >= 0.90 of the target quad)

| category | detector | stills n | stills recall | stills 95% CI | clips n (frames) | clips recall | target | result |
|---|---|---|---|---|---|---|---|---|
| Faces masked (non-allowlisted) | faces | 107 | 1 | [0.965, 1.000] | 900 | 1 | 0.95 | PASS |
| Allowlisted faces left visible [clips: settled, >= 1 s after the track first appears] | faces | 24 | 0.208 | [0.092, 0.405] | 1080 | 0.75 | 0.95 | FAIL |
| Aadhaar card | cards | 24 | 1 | [0.862, 1.000] | 300 | 1 | 0.95 | PASS |
| PAN card | cards | 24 | 1 | [0.862, 1.000] | 300 | 1 | 0.95 | PASS |
| UPI QR | upi_qr | 24 | 1 | [0.862, 1.000] | 300 | 1 | 0.98 | PASS |
| Signboard text | text | 275 | 1 | [0.986, 1.000] | 1050 | 1 | 0.85 | PASS |
| Phone number (on-screen) | text | 31 | 1 | [0.890, 1.000] | 450 | 1 | 0.9 | PASS |
| PIN code (on-screen) | text | 34 | 0.912 | [0.770, 0.970] | 0 | n/a | 0.9 | PASS |
| UPI ID (on-screen) | text | 36 | 1 | [0.904, 1.000] | 0 | n/a | 0.9 | PASS |
| Email (on-screen) | text | 33 | 1 | [0.896, 1.000] | 0 | n/a | 0.9 | PASS |
| OTP screen | screens | 24 | 0.917 | [0.742, 0.977] | 300 | 1 | 0.85 | PASS |
| Game IDs (player ID, room ID, room password) | screens | 134 | 1 | [0.972, 1.000] | 1500 | 1 | 0.9 | PASS |
| Number plate | plates | 24 | 1 | [0.862, 1.000] | 300 | 1 | 0.85 | PASS |
| OTP code digits (inside the banner) | screens | 18 | 1 | [0.824, 1.000] | 300 | 1 | n/a | info |
| Game player ID | screens | 38 | 1 | [0.908, 1.000] | 300 | 1 | n/a | info |
| Game room ID | screens | 48 | 1 | [0.926, 1.000] | 600 | 1 | n/a | info |
| Game room password | screens | 48 | 1 | [0.926, 1.000] | 600 | 1 | n/a | info |

## Recall by difficulty level (all levels reported, whatever the gate uses)

| category | set | clean | light | heavy |
|---|---|---|---|---|
| Faces masked (non-allowlisted) | stills | 1.000 (n=32) | 1.000 (n=75) | 0.853 (n=75) |
| Faces masked (non-allowlisted) | clips | 1.000 (n=300) | 1.000 (n=600) | 0.679 (n=900) |
| Allowlisted faces left visible | stills | 0.125 (n=8) | 0.250 (n=16) | 0.188 (n=16) |
| Allowlisted faces left visible (settled frames) | clips | 1.000 (n=540) | 0.500 (n=540) | 0.500 (n=540) |
| Aadhaar card | stills | 1.000 (n=8) | 1.000 (n=16) | 0.375 (n=16) |
| Aadhaar card | clips | 1.000 (n=150) | 1.000 (n=150) | 0.760 (n=300) |
| PAN card | stills | 1.000 (n=8) | 1.000 (n=16) | 0.562 (n=16) |
| PAN card | clips | 1.000 (n=150) | 1.000 (n=150) | 0.983 (n=300) |
| UPI QR | stills | 1.000 (n=8) | 1.000 (n=16) | 0.500 (n=16) |
| UPI QR | clips | 1.000 (n=150) | 1.000 (n=150) | 0.740 (n=300) |
| Signboard text | stills | 1.000 (n=90) | 1.000 (n=185) | 0.690 (n=168) |
| Signboard text | clips | 1.000 (n=300) | 1.000 (n=750) | 0.931 (n=1350) |
| Phone number (on-screen) | stills | 1.000 (n=11) | 1.000 (n=20) | 0.700 (n=20) |
| Phone number (on-screen) | clips | 1.000 (n=150) | 1.000 (n=300) | 0.333 (n=450) |
| PIN code (on-screen) | stills | 1.000 (n=11) | 0.870 (n=23) | 0.500 (n=24) |
| UPI ID (on-screen) | stills | 1.000 (n=12) | 1.000 (n=24) | 0.333 (n=21) |
| Email (on-screen) | stills | 1.000 (n=13) | 1.000 (n=20) | 0.348 (n=23) |
| OTP screen | stills | 1.000 (n=8) | 0.875 (n=16) | 0.500 (n=16) |
| OTP screen | clips | 1.000 (n=150) | 1.000 (n=150) | 0.470 (n=300) |
| Game IDs (player ID, room ID, room password) | stills | 1.000 (n=48) | 1.000 (n=86) | 0.638 (n=58) |
| Game IDs (player ID, room ID, room password) | clips | 1.000 (n=900) | 1.000 (n=600) | 0.683 (n=750) |
| Number plate | stills | 1.000 (n=8) | 1.000 (n=16) | 0.600 (n=15) |
| Number plate | clips | 1.000 (n=150) | 1.000 (n=150) | 0.975 (n=163) |
| OTP code digits (inside the banner) | stills | 1.000 (n=8) | 1.000 (n=10) | 1.000 (n=3) |
| OTP code digits (inside the banner) | clips | 1.000 (n=150) | 1.000 (n=150) | n/a |
| Game player ID | stills | 1.000 (n=16) | 1.000 (n=22) | 0.250 (n=4) |
| Game player ID | clips | 1.000 (n=300) | n/a | n/a |
| Game room ID | stills | 1.000 (n=16) | 1.000 (n=32) | 0.739 (n=23) |
| Game room ID | clips | 1.000 (n=300) | 1.000 (n=300) | 1.000 (n=150) |
| Game room password | stills | 1.000 (n=16) | 1.000 (n=32) | 0.613 (n=31) |
| Game room password | clips | 1.000 (n=300) | 1.000 (n=300) | 0.603 (n=600) |

## Stress levels (heavy): reported, NOT gated (D-48); the gate targets are shown for reference only

| category | set | level | n | recall | target (reference) | meets target |
|---|---|---|---|---|---|---|
| Faces masked (non-allowlisted) | stills | heavy | 75 | 0.853 | 0.95 | no |
| Faces masked (non-allowlisted) | clips | heavy | 900 | 0.679 | 0.95 | no |
| Allowlisted faces left visible | stills | heavy | 16 | 0.188 | 0.95 | no |
| Allowlisted faces left visible (settled frames) | clips | heavy | 540 | 0.5 | 0.95 | no |
| Aadhaar card | stills | heavy | 16 | 0.375 | 0.95 | no |
| Aadhaar card | clips | heavy | 300 | 0.76 | 0.95 | no |
| PAN card | stills | heavy | 16 | 0.562 | 0.95 | no |
| PAN card | clips | heavy | 300 | 0.983 | 0.95 | yes |
| UPI QR | stills | heavy | 16 | 0.5 | 0.98 | no |
| UPI QR | clips | heavy | 300 | 0.74 | 0.98 | no |
| Signboard text | stills | heavy | 168 | 0.69 | 0.85 | no |
| Signboard text | clips | heavy | 1350 | 0.931 | 0.85 | yes |
| Phone number (on-screen) | stills | heavy | 20 | 0.7 | 0.9 | no |
| Phone number (on-screen) | clips | heavy | 450 | 0.333 | 0.9 | no |
| PIN code (on-screen) | stills | heavy | 24 | 0.5 | 0.9 | no |
| UPI ID (on-screen) | stills | heavy | 21 | 0.333 | 0.9 | no |
| Email (on-screen) | stills | heavy | 23 | 0.348 | 0.9 | no |
| OTP screen | stills | heavy | 16 | 0.5 | 0.85 | no |
| OTP screen | clips | heavy | 300 | 0.47 | 0.85 | no |
| Game IDs (player ID, room ID, room password) | stills | heavy | 58 | 0.638 | 0.9 | no |
| Game IDs (player ID, room ID, room password) | clips | heavy | 750 | 0.683 | 0.9 | no |
| Number plate | stills | heavy | 15 | 0.6 | 0.85 | no |
| Number plate | clips | heavy | 163 | 0.975 | 0.85 | yes |

## Allowlisted faces left visible on clips: settled / steady / all frames. settled = frames >= 1 s after the track first appears (the gate row); steady = frames >= 2 s after; all = every visible frame. The detector masks an allowlisted face while identity votes accrue (spec 5.5), on purpose.

| view | clean | light | heavy | gate levels |
|---|---|---|---|---|
| settled (gate row, >= 1 s) | 1.000 (n=540) | 0.500 (n=540) | 0.500 (n=540) | 0.750 (n=1080) |
| steady (>= 2 s) | 1.000 (n=480) | 0.500 (n=480) | 0.500 (n=480) | 0.750 (n=960) |
| all frames (includes the warm-up) | 0.947 (n=600) | 0.473 (n=600) | 0.500 (n=600) | 0.710 (n=1200) |

## Recall by sub-group (stills)

| category | attribute | recall by value |
|---|---|---|
| Signboard text | script | bengali: 0.943 (n=53); devanagari: 0.875 (n=56); kannada: 0.933 (n=60); latin: 0.871 (n=170); tamil: 0.824 (n=51); telugu: 0.868 (n=53) |
| Phone number (on-screen) | form | devanagari: 0.800 (n=5); devanagari_spaced: 0.857 (n=7); hyphen: 1.000 (n=8); plain: 0.800 (n=5); plus91: 1.000 (n=7); plus91_dash: 1.000 (n=4); spaced: 0.750 (n=8); zero: 0.857 (n=7) |
| PIN code (on-screen) | form | Bengaluru - {pin}: 0.800 (n=5); Chennai {pin}: 1.000 (n=6); Delhi - {pin}: 0.857 (n=7); Mumbai {pin}: 1.000 (n=2); PIN Code - {pin}: 0.833 (n=6); PIN: {pin}: 0.667 (n=6); Pin {pin}: 0.429 (n=7); Pincode: {pin}: 0.750 (n=4); पिन {pin}: 1.000 (n=1); पिन कोड: {pin}: 0.643 (n=14) |
| OTP screen | lang | en: 0.889 (n=18); hi: 0.500 (n=8); hinglish: 0.714 (n=14) |
| OTP screen | layout | banner: 0.875 (n=8); entry: 0.909 (n=11); lock: 0.636 (n=11); sms: 0.600 (n=10) |
| Game IDs (player ID, room ID, room password) | style | bgmi: 0.875 (n=96); freefire: 0.906 (n=96) |
| Number plate | layout | one_line: 0.871 (n=31); two_line: 0.750 (n=8) |
| OTP code digits (inside the banner) | lang | en: 1.000 (n=11); hi: 1.000 (n=2); hinglish: 1.000 (n=8) |
| Game player ID | style | bgmi: 0.900 (n=20); freefire: 0.955 (n=22) |
| Game room ID | style | bgmi: 0.892 (n=37); freefire: 0.941 (n=34) |
| Game room password | style | bgmi: 0.846 (n=39); freefire: 0.850 (n=40) |

## Mask precision (informational; 'inside' = >= 50 % of the mask lies on ground truth that justifies its category, 'IoU 0.5' = spec 5.5 against a single target, 'stray' = masks on frames that hold nothing of that category; the planner merges and pads nearby boxes, which lowers both precision figures)

| mask category/set | masks | precision (inside) | precision (IoU >= 0.5) | stray masks |
|---|---|---|---|---|
| aadhaar/clip | 572 | 0.713 | 0.713 | 164 |
| aadhaar/img | 61 | 0.492 | 0.492 | 30 |
| email/img | 33 | 0.758 | 0.606 | 0 |
| face/clip | 6196 | 0.206 | 0.496 | 387 |
| face/img | 205 | 0.122 | 0.654 | 3 |
| game_id/clip | 1363 | 0 | 0 | 0 |
| game_id/img | 66 | 0 | 0 | 4 |
| otp_screen/clip | 408 | 0.5 | 0.5 | 0 |
| otp_screen/img | 20 | 0.95 | 0.85 | 0 |
| pan/clip | 1347 | 0.47 | 0.454 | 714 |
| pan/img | 54 | 0.611 | 0.611 | 21 |
| phone_number/clip | 616 | 0.32 | 0.089 | 44 |
| phone_number/img | 31 | 0.194 | 0.161 | 0 |
| pin_code/clip | 354 | 0 | 0 | 354 |
| pin_code/img | 47 | 0 | 0 | 18 |
| plate/clip | 408 | 0 | 0 | 0 |
| plate/img | 23 | 0 | 0.043 | 0 |
| room_credential/clip | 1632 | 1 | 0.001 | 0 |
| room_credential/img | 103 | 0.971 | 0.019 | 3 |
| signboard/clip | 34587 | 0.091 | 0.059 | 31289 |
| signboard/img | 4095 | 0.139 | 0.085 | 3506 |
| upi_id/clip | 408 | 0 | 0 | 408 |
| upi_id/img | 67 | 0.507 | 0.433 | 24 |
| upi_qr/clip | 1182 | 0.677 | 1 | 0 |
| upi_qr/img | 28 | 1 | 1 | 0 |
| all/clip | 49073 | 0.169 | 0.155 | 33360 |
| all/img | 4833 | 0.18 | 0.134 | 3609 |

## Hard negatives: items/frames where the detector under test produced a false mask (distractors masked: 2543/3430). neg_text is read on PII masks only (phone_number, pin_code, upi_id, email, pan): its signboard masks come from the text detector's mask_all_text rule, by design. neg_scene (natural scenes and text-free UI) is the signboard negative: informational, not gated

| bucket/set | n | false masks | rate | counted as false | signboard-only masks | signboard rate | slated (protect) |
|---|---|---|---|---|---|---|---|
| faces_neg/img | 40 | 0 | 0 | any mask of the detector | n/a | n/a | 0 |
| neg_card/img | 40 | 24 | 0.6 | any mask of the detector | n/a | n/a | 0 |
| neg_game/img | 40 | 2 | 0.05 | any mask of the detector | n/a | n/a | 0 |
| neg_plate/img | 40 | 0 | 0 | any mask of the detector | n/a | n/a | 0 |
| neg_scene/img | 40 | 19 | 0.475 | any mask of the detector | 19 | 0.475 | 0 |
| neg_screen/img | 40 | 0 | 0 | any mask of the detector | n/a | n/a | 0 |
| neg_text/img | 40 | 0 | 0 | phone_number, pin_code, upi_id, email, pan | 39 | 0.975 | 0 |
| qr_negative/clip | 600 | 0 | 0 | any mask of the detector | n/a | n/a | 0 |
| qr_negative/img | 40 | 0 | 0 | any mask of the detector | n/a | n/a | 0 |

## Flicker on static clips (mask on, off, on inside one second on a continuously visible target)

| scope | windows | flickering | fraction |
|---|---|---|---|
| all | 160 | 3 | 0.0187 |
| row aadhaar | 10 | 0 | 0 |
| row face_masked | 20 | 0 | 0 |
| row game_ids | 40 | 0 | 0 |
| row otp_screen | 10 | 0 | 0 |
| row pan | 10 | 0 | 0 |
| row phone | 15 | 0 | 0 |
| row plate | 10 | 0 | 0 |
| row signboard | 35 | 2 | 0.0571 |
| row upi_qr | 10 | 1 | 0.1 |
| level clean | 70 | 0 | 0 |
| level heavy | 90 | 3 | 0.0333 |

## Latency per lane, offline CPU (ms; budget from docs/PLAN.md 2.5). The 5000 ms OCR budget lift is used for the recall pass ONLY; the lane is judged on the production-budget pass (latency stills of every bucket; shared machine, so indicative)

| lane / pass | runs | p50 | p95 | max | budget p95 | within |
|---|---|---|---|---|---|---|
| accurate, production OCR budget 150 ms (judged) | 135 | 246.7 | 530.2 | 575.2 | 450 | False |
| fast, production OCR budget 150 ms (judged) | 945 | 20.3 | 55.74 | 106.4 | 25 | False |
| accurate, recall pass, OCR budget lifted (ceiling, informational) | 2144 | 228.2 | 759.5 | 1810 | 450 | False |
| fast, recall pass, OCR budget lifted (ceiling, informational) | 10360 | 19.36 | 55.23 | 164.7 | 25 | False |

## Recall at the production OCR budget (150 ms, budget-capped) vs the lifted budget (5000 ms), same 135 stills (informational)

| row | visible target frames | production budget | lifted budget |
|---|---|---|---|
| aadhaar | 5 | 0.8 | 0.8 |
| email | 6 | 0.667 | 0.667 |
| face_allowed | 5 | 0.4 | 0.2 |
| face_masked | 24 | 0.917 | 0.917 |
| game_ids | 25 | 0.84 | 0.84 |
| otp_screen | 5 | 1 | 1 |
| pan | 5 | 1 | 1 |
| phone | 8 | 1 | 1 |
| pin | 7 | 0.857 | 0.857 |
| plate | 5 | 0.6 | 0.6 |
| signboard | 51 | 0.941 | 0.941 |
| upi_id | 7 | 0.857 | 0.857 |
| upi_qr | 5 | 1 | 1 |

## Latency per detector, offline CPU (ms)

| detector | calls | p50 | p95 | max |
|---|---|---|---|---|
| cards | 2144 | 159.4 | 493 | 871.5 |
| faces | 10360 | 12.78 | 48.06 | 154.9 |
| plates | 2144 | 0.24 | 1.35 | 4.16 |
| screens | 2144 | 73.45 | 280.7 | 1201 |
| text | 2144 | 0.51 | 2.91 | 16.67 |
| upi_qr | 10360 | 4.71 | 12.73 | 28.98 |

## Missed case ids of failing rows (ids only; images regenerate from the seed)

| row | set | items | ids |
|---|---|---|---|
| face_allowed | stills | 19 | faces_enrolled_0000 faces_enrolled_0001 faces_enrolled_0005 faces_enrolled_0006 faces_enrolled_0007 faces_enrolled_0011 faces_enrolled_0012 faces_enrolled_0015 faces_enrolled_0016 faces_enrolled_0020 faces_enrolled_0021 faces_enrolled_0022 faces_enrolled_0025 faces_enrolled_0027 faces_enrolled_0030 faces_enrolled_0031 faces_enrolled_0032 faces_enrolled_0035 faces_enrolled_0037 |
| face_allowed | clips | 1 | faces_enrolled_clip02 |

## Detectors

| id | state | lane | categories | detail |
|---|---|---|---|---|
| cards | ok | accurate | aadhaar,pan |  |
| faces | ok | fast | face |  |
| plates | ok | accurate | plate |  |
| screens | ok | accurate | game_id,otp_screen,room_credential |  |
| text | ok | accurate | email,phone_number,pin_code,risky_text,signboard,upi_id |  |
| upi_qr | ok | fast | upi_qr |  |

## Failing case ids

- face_allowed:faces_enrolled_0000
- face_allowed:faces_enrolled_0001
- face_allowed:faces_enrolled_0005
- face_allowed:faces_enrolled_0006
- face_allowed:faces_enrolled_0007
- face_allowed:faces_enrolled_0011
- face_allowed:faces_enrolled_0012
- face_allowed:faces_enrolled_0015
- face_allowed:faces_enrolled_0016
- face_allowed:faces_enrolled_0020
- face_allowed:faces_enrolled_0021
- face_allowed:faces_enrolled_0022
- face_allowed:faces_enrolled_0025
- face_allowed:faces_enrolled_0027
- face_allowed:faces_enrolled_0030
- face_allowed:faces_enrolled_0031
- face_allowed:faces_enrolled_0032
- face_allowed:faces_enrolled_0035
- face_allowed:faces_enrolled_0037
- face_allowed:faces_enrolled_clip02

## Notes

- warm-up rule (allowlisted faces on clips): settled = frames >= 1 s after the track first appears (the gate row); steady = frames >= 2 s after; all = every visible frame. The detector masks an allowlisted face while identity votes accrue (spec 5.5), on purpose.
- offline run: 1149.5 s, lane runs {'accurate': 2144, 'fast': 10360}, detector exceptions {}, clip frames 8400, slated (protect) clip frames 0
- enrolled 3/3 people into the allowlist
- fast-lane runs per still: 7 (face_vote_min 5 + 2): an allowlisted face needs >= face_vote_min matched votes of face_vote_window
- OCR budget: recall pass at 5000 ms (lifted, repeatable recall); latency pass at the production budget (see the latency table)
- scoring proxy: hits are mask coverage of the ground-truth quad by what the production planner emits (offline, CPU); the real-time leg measures the engine path end to end on the stamped stream only
