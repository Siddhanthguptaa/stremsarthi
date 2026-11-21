# Video red team (2026-11-21)

- git rev: `178ffa40184e`  profile: `dev`  seed: 1234  hardware: {'cpu': '13th Gen Intel(R) Core(TM) i5-13450HX', 'gpu': 'NVIDIA GeForce RTX 3050 6GB Laptop GPU, 581.86'}
- selection: quick   cases run: 46  exposed-target frames scored: 727
- detectors: cards=ok, faces=ok, plates=ok, screens=ok, text=ok, upi_qr=ok
- read-back: zxing-cpp (QR) + RapidOCR (text)
- command: `python -m eval.run redteam-video --quick`

## How to read this

Each case is an adversarial SPECIMEN clip. Every delivered frame with an exposed target is scored twice. **Proxy leak**: the masks the production planner produced cover less than 90 % of the target quad (the Gate 2 `cov` metric of `eval/phase2`). **Confirmed leak**: the frame is painted with the production painter in the category's configured style and the target is still recoverable from the OUTPUT pixels (zxing decodes the QR, or RapidOCR reads a long run of the card number / phone / UPI id / plate in the target region). **Leak** = proxy or confirmed (conservative headline). `src-unreadable` counts proxy leaks whose target is not recoverable even from the unmasked input (the attack destroyed legibility for OCR; a person may still read it). Read-back runs on every 3th frame, so the confirmed rate is over checked frames. Faces are proxy only. A target is *exposed* when at least 25 % of its quad is in the frame and its shortest side is at least 16 px (the dataset rule is 60 % / 24 px; the red team is stricter on purpose). PROTECT (slate) frames cover everything and never leak; they are reported as `protect`.

## Leak rate per group

| group | frames | leak | proxy | confirmed (checked) |
| --- | --- | --- | --- | --- |
| angle | 36 | 0.0% (0/36) CI 0%-10% | 0.0% (0/36) CI 0%-10% | 0.0% (0/12) CI 0%-24% (12) |
| control | 27 | 0.0% (0/27) CI 0%-12% | 0.0% (0/27) CI 0%-12% | 0.0% (0/9) CI 0%-30% (9) |
| distance | 18 | 0.0% (0/18) CI 0%-18% | 0.0% (0/18) CI 0%-18% | 0.0% (0/6) CI 0%-39% (6) |
| enter_leave | 81 | 74.1% (60/81) CI 64%-82% | 74.1% (60/81) CI 64%-82% | 0.0% (0/28) CI 0%-12% (28) |
| face | 45 | 0.0% (0/45) CI 0%-8% | 0.0% (0/45) CI 0%-8% | n/a (0) |
| flash | 27 | 3.7% (1/27) CI 1%-18% | 3.7% (1/27) CI 1%-18% | 0.0% (0/9) CI 0%-30% (9) |
| fonts | 27 | 0.0% (0/27) CI 0%-12% | 0.0% (0/27) CI 0%-12% | 0.0% (0/9) CI 0%-30% (9) |
| frame_drops | 85 | 0.0% (0/85) CI 0%-4% | 0.0% (0/85) CI 0%-4% | 0.0% (0/15) CI 0%-20% (15) |
| glare | 18 | 0.0% (0/18) CI 0%-18% | 0.0% (0/18) CI 0%-18% | 0.0% (0/6) CI 0%-39% (6) |
| mixed_script | 27 | 0.0% (0/27) CI 0%-12% | 0.0% (0/27) CI 0%-12% | 0.0% (0/9) CI 0%-30% (9) |
| motion | 90 | 70.0% (63/90) CI 60%-78% | 70.0% (63/90) CI 60%-78% | 0.0% (0/30) CI 0%-11% (30) |
| occlusion | 27 | 33.3% (9/27) CI 19%-52% | 33.3% (9/27) CI 19%-52% | 11.1% (1/9) CI 2%-44% (9) |
| photo_of_photo | 18 | 0.0% (0/18) CI 0%-18% | 0.0% (0/18) CI 0%-18% | 0.0% (0/6) CI 0%-39% (6) |
| scene_cut | 120 | 2.5% (3/120) CI 1%-7% | 2.5% (3/120) CI 1%-7% | 0.0% (0/40) CI 0%-9% (40) |
| screens_of_screens | 18 | 0.0% (0/18) CI 0%-18% | 0.0% (0/18) CI 0%-18% | 0.0% (0/6) CI 0%-39% (6) |
| separators | 27 | 0.0% (0/27) CI 0%-12% | 0.0% (0/27) CI 0%-12% | 0.0% (0/9) CI 0%-30% (9) |
| split_lines | 36 | 0.0% (0/36) CI 0%-10% | 0.0% (0/36) CI 0%-10% | 0.0% (0/12) CI 0%-24% (12) |

## Leak rate per attack

| attack | group | frames | leak | proxy | confirmed | src-unreadable | protect |
| --- | --- | --- | --- | --- | --- | --- | --- |
| control | control | 27 | 0.0% (0/27) CI 0%-12% | 0.0% (0/27) CI 0%-12% | 0.0% (0/9) CI 0%-30% | 0 | 0 |
| distance_card | distance | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 | 0 |
| distance_qr | distance | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 | 0 |
| enter_leave_card | enter_leave | 50 | 62.0% (31/50) CI 48%-74% | 62.0% (31/50) CI 48%-74% | 0.0% (0/17) CI 0%-18% | 7 | 0 |
| enter_leave_qr | enter_leave | 31 | 93.5% (29/31) CI 79%-98% | 93.5% (29/31) CI 79%-98% | 0.0% (0/11) CI 0%-26% | 10 | 1 |
| face_edge | face | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | n/a | 0 | 0 |
| face_occluded | face | 18 | 0.0% (0/18) CI 0%-18% | 0.0% (0/18) CI 0%-18% | n/a | 0 | 0 |
| face_small | face | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | n/a | 0 | 0 |
| face_turned | face | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | n/a | 0 | 0 |
| flash_card | flash | 21 | 0.0% (0/21) CI 0%-15% | 0.0% (0/21) CI 0%-15% | 0.0% (0/7) CI 0%-35% | 0 | 0 |
| flash_qr | flash | 6 | 16.7% (1/6) CI 3%-56% | 16.7% (1/6) CI 3%-56% | 0.0% (0/2) CI 0%-66% | 0 | 0 |
| frame_drops | frame_drops | 85 | 0.0% (0/85) CI 0%-4% | 0.0% (0/85) CI 0%-4% | 0.0% (0/15) CI 0%-20% | 0 | 40 |
| glare_card | glare | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 | 0 |
| glare_qr | glare | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 | 0 |
| mixed_script | mixed_script | 27 | 0.0% (0/27) CI 0%-12% | 0.0% (0/27) CI 0%-12% | 0.0% (0/9) CI 0%-30% | 0 | 0 |
| motion_pan_card | motion | 30 | 100.0% (30/30) CI 89%-100% | 100.0% (30/30) CI 89%-100% | 0.0% (0/10) CI 0%-28% | 10 | 0 |
| motion_pan_qr | motion | 30 | 100.0% (30/30) CI 89%-100% | 100.0% (30/30) CI 89%-100% | 0.0% (0/10) CI 0%-28% | 10 | 0 |
| motion_shake_card | motion | 30 | 10.0% (3/30) CI 3%-26% | 10.0% (3/30) CI 3%-26% | 0.0% (0/10) CI 0%-28% | 0 | 0 |
| occlusion_card | occlusion | 18 | 50.0% (9/18) CI 29%-71% | 50.0% (9/18) CI 29%-71% | 16.7% (1/6) CI 3%-56% | 1 | 0 |
| occlusion_qr | occlusion | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 | 0 |
| photo_of_photo | photo_of_photo | 18 | 0.0% (0/18) CI 0%-18% | 0.0% (0/18) CI 0%-18% | 0.0% (0/6) CI 0%-39% | 0 | 0 |
| scene_cut_card | scene_cut | 60 | 3.3% (2/60) CI 1%-11% | 3.3% (2/60) CI 1%-11% | 0.0% (0/20) CI 0%-16% | 0 | 0 |
| scene_cut_qr | scene_cut | 60 | 1.7% (1/60) CI 0%-9% | 1.7% (1/60) CI 0%-9% | 0.0% (0/20) CI 0%-16% | 0 | 0 |
| screen_of_screen | screens_of_screens | 18 | 0.0% (0/18) CI 0%-18% | 0.0% (0/18) CI 0%-18% | 0.0% (0/6) CI 0%-39% | 0 | 0 |
| separators | separators | 27 | 0.0% (0/27) CI 0%-12% | 0.0% (0/27) CI 0%-12% | 0.0% (0/9) CI 0%-30% | 0 | 0 |
| split_lines | split_lines | 36 | 0.0% (0/36) CI 0%-10% | 0.0% (0/36) CI 0%-10% | 0.0% (0/12) CI 0%-24% | 0 | 0 |
| tilt_card | angle | 18 | 0.0% (0/18) CI 0%-18% | 0.0% (0/18) CI 0%-18% | 0.0% (0/6) CI 0%-39% | 0 | 0 |
| tilt_plate | angle | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 | 0 |
| tilt_qr | angle | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 | 0 |
| unusual_fonts | fonts | 27 | 0.0% (0/27) CI 0%-12% | 0.0% (0/27) CI 0%-12% | 0.0% (0/9) CI 0%-30% | 0 | 0 |

## Ranked misses (worst first)

`confirmed` = target recoverable from the output frame. `proxy_readable` = uncovered and readable in the input. `proxy_unreadable` = uncovered but not OCR-readable even unmasked.

| # | attack[param] | kind | leak | confirmed | worst frame | repro (in / out / meta) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | occlusion_card[occ40_left] | confirmed | 100.0% (9/9) CI 70%-100% | 33.3% (1/3) CI 6%-79% | f0 aadhaar cov=0.495 | /tmp/ss_redteam_video/repro/occlusion_card.occ40_left.s1234/f000 |
| 2 | motion_pan_card[v50] | proxy_readable | 100.0% (30/30) CI 89%-100% | 0.0% (0/10) CI 0%-28% | f1 pan cov=0.0 | /tmp/ss_redteam_video/repro/motion_pan_card.v50.s1234/f001 |
| 3 | motion_pan_qr[v50] | proxy_readable | 100.0% (30/30) CI 89%-100% | 0.0% (0/10) CI 0%-28% | f1 upi_qr cov=0.0 | /tmp/ss_redteam_video/repro/motion_pan_qr.v50.s1234/f001 |
| 4 | enter_leave_card[edge_hold35] | proxy_readable | 100.0% (15/15) CI 80%-100% | 0.0% (0/5) CI 0%-43% | f1 aadhaar cov=0.8359 | /tmp/ss_redteam_video/repro/enter_leave_card.edge_hold35.s1234/f001 |
| 5 | enter_leave_qr[fast] | proxy_readable | 93.5% (29/31) CI 79%-98% | 0.0% (0/11) CI 0%-26% | f4 upi_qr cov=0.0 | /tmp/ss_redteam_video/repro/enter_leave_qr.fast.s1234/f004 |
| 6 | enter_leave_card[fast] | proxy_readable | 45.7% (16/35) CI 30%-62% | 0.0% (0/12) CI 0%-24% | f4 pan cov=0.2753 | /tmp/ss_redteam_video/repro/enter_leave_card.fast.s1234/f004 |
| 7 | flash_qr[f6] | proxy_readable | 16.7% (1/6) CI 3%-56% | 0.0% (0/2) CI 0%-66% | f22 upi_qr cov=0.0 | /tmp/ss_redteam_video/repro/flash_qr.f6.s1234/f022 |
| 8 | motion_shake_card[amp40] | proxy_readable | 10.0% (3/30) CI 3%-26% | 0.0% (0/10) CI 0%-28% | f1 pan cov=0.8735 | /tmp/ss_redteam_video/repro/motion_shake_card.amp40.s1234/f001 |
| 9 | scene_cut_card[every10] | proxy_readable | 3.3% (2/60) CI 1%-11% | 0.0% (0/20) CI 0%-16% | f10 aadhaar cov=0.3771 | /tmp/ss_redteam_video/repro/scene_cut_card.every10.s1234/f010 |
| 10 | scene_cut_qr[every10] | proxy_readable | 1.7% (1/60) CI 0%-9% | 0.0% (0/20) CI 0%-16% | f10 upi_qr cov=0.0 | /tmp/ss_redteam_video/repro/scene_cut_qr.every10.s1234/f010 |

## Per parameter

| attack[param] | frames | leak | proxy | confirmed | src-unreadable |
| --- | --- | --- | --- | --- | --- |
| control[aadhaar] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| control[phone] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| control[qr] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| distance_card[w06] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| distance_qr[w06] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| enter_leave_card[edge_hold35] | 15 | 100.0% (15/15) CI 80%-100% | 100.0% (15/15) CI 80%-100% | 0.0% (0/5) CI 0%-43% | 5 |
| enter_leave_card[fast] | 35 | 45.7% (16/35) CI 30%-62% | 45.7% (16/35) CI 30%-62% | 0.0% (0/12) CI 0%-24% | 2 |
| enter_leave_qr[fast] | 31 | 93.5% (29/31) CI 79%-98% | 93.5% (29/31) CI 79%-98% | 0.0% (0/11) CI 0%-26% | 10 |
| face_edge[edge50] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | n/a | 0 |
| face_occluded[mask] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | n/a | 0 |
| face_occluded[sunglasses] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | n/a | 0 |
| face_small[w10] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | n/a | 0 |
| face_turned[yaw65] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | n/a | 0 |
| flash_card[f15] | 15 | 0.0% (0/15) CI 0%-20% | 0.0% (0/15) CI 0%-20% | 0.0% (0/5) CI 0%-43% | 0 |
| flash_card[f6] | 6 | 0.0% (0/6) CI 0%-39% | 0.0% (0/6) CI 0%-39% | 0.0% (0/2) CI 0%-66% | 0 |
| flash_qr[f6] | 6 | 16.7% (1/6) CI 3%-56% | 16.7% (1/6) CI 3%-56% | 0.0% (0/2) CI 0%-66% | 0 |
| frame_drops[burst15] | 45 | 0.0% (0/45) CI 0%-8% | 0.0% (0/45) CI 0%-8% | 0.0% (0/15) CI 0%-20% | 0 |
| frame_drops[slots] | 40 | 0.0% (0/40) CI 0%-9% | 0.0% (0/40) CI 0%-9% | n/a | 0 |
| glare_card[s100] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| glare_qr[s95] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| mixed_script[hi_deva_digits] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| mixed_script[mixed_in_number] | 18 | 0.0% (0/18) CI 0%-18% | 0.0% (0/18) CI 0%-18% | 0.0% (0/6) CI 0%-39% | 0 |
| motion_pan_card[v50] | 30 | 100.0% (30/30) CI 89%-100% | 100.0% (30/30) CI 89%-100% | 0.0% (0/10) CI 0%-28% | 10 |
| motion_pan_qr[v50] | 30 | 100.0% (30/30) CI 89%-100% | 100.0% (30/30) CI 89%-100% | 0.0% (0/10) CI 0%-28% | 10 |
| motion_shake_card[amp40] | 30 | 10.0% (3/30) CI 3%-26% | 10.0% (3/30) CI 3%-26% | 0.0% (0/10) CI 0%-28% | 0 |
| occlusion_card[occ40_left] | 9 | 100.0% (9/9) CI 70%-100% | 100.0% (9/9) CI 70%-100% | 33.3% (1/3) CI 6%-79% | 1 |
| occlusion_card[occ60_bottom] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| occlusion_qr[occ30_bottom] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| photo_of_photo[card] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| photo_of_photo[qr] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| scene_cut_card[every10] | 60 | 3.3% (2/60) CI 1%-11% | 3.3% (2/60) CI 1%-11% | 0.0% (0/20) CI 0%-16% | 0 |
| scene_cut_qr[every10] | 60 | 1.7% (1/60) CI 0%-9% | 1.7% (1/60) CI 0%-9% | 0.0% (0/20) CI 0%-16% | 0 |
| screen_of_screen[card_strong] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| screen_of_screen[qr_mild] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| separators[all_spaced] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| separators[words_en] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| separators[zero_width] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| split_lines[phone_hyphen] | 18 | 0.0% (0/18) CI 0%-18% | 0.0% (0/18) CI 0%-18% | 0.0% (0/6) CI 0%-39% | 0 |
| split_lines[upi_at] | 18 | 0.0% (0/18) CI 0%-18% | 0.0% (0/18) CI 0%-18% | 0.0% (0/6) CI 0%-39% | 0 |
| tilt_card[yaw50] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| tilt_card[yaw65] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| tilt_plate[yaw60] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| tilt_qr[yaw60] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| unusual_fonts[hershey_script] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| unusual_fonts[spaced] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |
| unusual_fonts[ubuntu_thin] | 9 | 0.0% (0/9) CI 0%-30% | 0.0% (0/9) CI 0%-30% | 0.0% (0/3) CI 0%-56% | 0 |

## By frame tag (scene cuts: frames since the cut; flash: visible)

| attack | tag | frames | leak |
| --- | --- | --- | --- |
| scene_cut_card | cut+0 | 6 | 16.7% (1/6) CI 3%-56% |
| scene_cut_card | cut+1 | 6 | 16.7% (1/6) CI 3%-56% |
| scene_cut_card | cut+2 | 6 | 0.0% (0/6) CI 0%-39% |
| scene_cut_card | cut+3 | 6 | 0.0% (0/6) CI 0%-39% |
| scene_cut_card | cut+4 | 6 | 0.0% (0/6) CI 0%-39% |
| scene_cut_card | cut+5 | 6 | 0.0% (0/6) CI 0%-39% |
| scene_cut_card | steady | 24 | 0.0% (0/24) CI 0%-14% |
| scene_cut_qr | cut+0 | 6 | 16.7% (1/6) CI 3%-56% |
| scene_cut_qr | cut+1 | 6 | 0.0% (0/6) CI 0%-39% |
| scene_cut_qr | cut+2 | 6 | 0.0% (0/6) CI 0%-39% |
| scene_cut_qr | cut+3 | 6 | 0.0% (0/6) CI 0%-39% |
| scene_cut_qr | cut+4 | 6 | 0.0% (0/6) CI 0%-39% |
| scene_cut_qr | cut+5 | 6 | 0.0% (0/6) CI 0%-39% |
| scene_cut_qr | steady | 24 | 0.0% (0/24) CI 0%-14% |

## Not run / limits

- Faces are scored by the coverage proxy only (no output-side face check); `face_turned` is a frontal portrait foreshortened by yaw, not a true profile view (no profile images are available).
- The read-back is OCR (RapidOCR PP-OCR, Latin/Chinese model) and zxing; it under-reads Indic digits and stylised fonts, so for those attacks the proxy leak is the stronger signal and `src-unreadable` is expected.
- Frame drops model a dropped frame as a lane run that does not happen on that capture slot; the engine's own sequencing under real drops is covered by the Phase 1/7 stream tests, not here.
- Offline and single-process: lane latency is not applied (detector results are as fresh as the schedule allows), so real-time leaks caused by slow lanes are not visible here.

## Attack catalogue

- `control` (control): clean frontal targets: the harness floor, a miss here is a detector regression
- `occlusion_card` (occlusion): parameter grid: occ20_right, occ40_left, occ60_bottom, occ60_top
- `occlusion_qr` (occlusion): parameter grid: occ15_right, occ30_bottom, occ45_left
- `tilt_card` (angle): parameter grid: yaw35, yaw50, yaw65, yaw72
- `tilt_qr` (angle): parameter grid: yaw40, yaw60, yaw70
- `tilt_plate` (angle): parameter grid: yaw40, yaw60
- `distance_card` (distance): parameter grid: w10, w06, w04
- `distance_qr` (distance): parameter grid: w10, w06, w04
- `glare_card` (glare): parameter grid: s80, s100
- `glare_qr` (glare): parameter grid: s70, s95
- `screen_of_screen` (screens_of_screens): card or QR shown on a monitor that is itself filmed: bezel, moire, blur, glare
- `photo_of_photo` (photo_of_photo): a printed photo of the card/QR on a desk, warm cast, glossy streak
- `motion_pan_card` (motion): parameter grid: v20, v50, v100
- `motion_pan_qr` (motion): parameter grid: v50
- `motion_shake_card` (motion): parameter grid: amp15, amp40
- `enter_leave_card` (enter_leave): card slides in from the right edge and out the left; edge_hold35 parks it 35% inside the frame
- `enter_leave_qr` (enter_leave): parameter grid: fast
- `flash_card` (flash): card on screen for 0.1 / 0.2 / 0.5 / 0.8 s inside a 2 s clip, starting off the detect stride
- `flash_qr` (flash): parameter grid: f6, f15
- `unusual_fonts` (fonts): a phone number in serif, mono-oblique, thin, condensed, Hershey script, letter-spaced and rotated renders
- `mixed_script` (mixed_script): parameter grid: hi_latin_digits, hi_deva_digits, bn_digits, ta_digits, mixed_in_number, hinglish_upi
- `split_lines` (split_lines): one identifier broken across two or three lines; every fragment is a target
- `separators` (separators): zero_width is pixel-identical to the plain number (U+200B draws nothing): it exercises the text path, not the pixels
- `face_edge` (face): face half out of the frame; proxy only (no read-back for faces)
- `face_turned` (face): a frontal portrait foreshortened by yaw; NOT a true profile view (no profile source images)
- `face_occluded` (face): synthetic sunglasses bar / face mask drawn over the portrait
- `face_small` (face): face 40-50 px wide
- `scene_cut_card` (scene_cut): hard cut to a new backdrop, card and pose; tags cut+0..cut+5 are the frames right after the cut
- `scene_cut_qr` (scene_cut): parameter grid: every10
- `frame_drops` (frame_drops): dropped frames never reach the detectors; slots drops exactly the frames the fast lane would run on
