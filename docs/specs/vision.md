# Spec: `vision/`

Status: approved plan, section 7. Contracts: `contracts/`. Budgets: `docs/PLAN.md` section 2.5.

## 1. Purpose and owner

All video detectors. Video never leaves the machine and every detector has a CPU path that runs in real time at its configured rate; the GPU is optional acceleration. Detectors emit `Detection`s; the tracker and `MaskPlanner` turn them into `MaskRequest`s; a `LaneHost` delivers `LaneResult`s to the engine (see `engine.md` section 3). Owner agent: `vision-engineer` (several instances per wave, each restricted to one subpackage).

## 2. Directories owned and file layout

Edit only `vision/`. Tests in `vision/tests/` and per-detector `tests/`.

```
vision/
  registry.py          # autodiscovery of vision/detectors/<id>/ (exports DETECTOR)         T0.5
  lanes.py             # LaneWorker: newest-frame-wins, stride/rate scheduling              T0.5 (adapt irl-shield shield/lanes.py)
  host.py              # LaneHost.build(config, bus): runs lanes, planner, emits LaneResult T1.3
  ort.py, ort_static.py, options.py                                                          T0.5 (adapt bench/onnx_detectors.py, static_model.py, detector_options.py)
  planner.py           # MaskPlanner                                                         T1.3, finalised T2.3
  ocr/                 # service.py (memoised OcrProvider), det.py, latin.py                T1.3
    recognizers/       # devanagari.py, tamil.py, telugu.py, easyocr_extra.py               T2.4
  patterns/verhoeff.py, pan.py                                                               T1.3
  tracking/            # bytetrack.py, hysteresis.py, smoothing.py                          T2.3
  detectors/
    cards/  faces/  upi_qr/  plates/  text/  screens/                                        T1.3 / T2.6 / T2.7 / T2.8 / T2.4 / T2.9
```
`models/manifest.json` and `models/prepare.py` are extended by T0.5 (hash-pinned downloads: YuNet, SFace, PP-OCR det/rec, QR assets). Detector packages must not import each other; shared helpers live in `vision/ocr`, `vision/patterns`, `vision/tracking`.

## 3. Inputs

`Frame` (1280x720 BGR, read-only) per lane at the lane rate; `Config` (categories, detect, gpu); the enrolled allowlist (embeddings directory, `vision.allowlist_dir`); `Resources` (ORT session factory, allowlist handle, clock). **Ambiguity resolved:** `Resources` also carries `ocr: OcrProvider` (callable `ocr(frame) -> OcrResult`, memoised by `frame_seq`), so cards, plates, text and screens share one OCR pass; `OcrProvider` and `OcrResult` are defined in `contracts/protocols.py` (D-25).

## 4. Outputs

`Detection` (category, `quad`, confidence, `track_id`, `allow`, `aux_quads`, `extra` with codes only, never text) per detector; `MaskRequest` from the planner; `LaneResult(ok, requests, aux_detections)` to the engine; `SafetyEvent` only through the engine. Declared `vram_mb` per detector.

## 5. Behaviour

### 5.1 Lane scheduling
- FAST lane (faces, QR, mouth landmarks): process frames with `seq % detect.stride == 0`. `dev` stride 3 (10 Hz), `demo` stride 2 (15 Hz). Input downscaled to 640x360.
- ACCURATE lane (OCR det plus rec, cards, plates, text, screens): newest frame at most every `1/detect.accurate_hz` s (4 Hz default), OCR detector input width `detect.accurate_width` 960 (static shape `[544, 960]`), 2 ORT threads.
- SLOW lane (≤1 Hz, optional): reserved for heavy models; unused in v1.
- A slow lane drops work, never source frames. Detectors in one lane run sequentially on the same frame; a detector that raises marks the lane result `ok=False` (engine then PROTECTs). Every run yields a `LaneResult`, with empty `requests` if nothing found.
- Each lane has its own tracker (FAST `frame_rate=10`, ACCURATE `frame_rate=4`).

### 5.2 OCR service (`vision/ocr/`)
1. Detect text boxes (PP-OCRv6 tiny det, v5 mobile as alternate) via the ORT helper with fixed shapes; DB thresholds 0.3/0.5, unclip 1.6.
2. Prefilter crops for recognition: digit-heavy or plate/phone-like aspect, height at least 10 px, at most `ocr.rec_max_crops` (8) per frame, recognition budget at most 150 ms.
3. Recognise with the latin recogniser first; if confidence under 0.5 or empty, try devanagari, then ta, te (script routing). Kannada and Bengali recognisers exist only as the optional EasyOCR extra and never run on the real-time path.
4. Normalise Devanagari, Bengali, Tamil, Telugu and Kannada digits to ASCII; map OCR confusions by position (O to 0, I/l to 1, B to 8, S to 5) only inside digit-expected fields.
5. Return `OcrResult(boxes, texts, confs, scripts)`. Text lives in memory for one frame; it is never logged, stored or put in `extra`.

### 5.3 MaskPlanner (`planner.py`)
For each tracked `Detection` in a lane run:
1. Drop if `categories[cat].enabled` is false. `allow=True` faces yield no `MaskRequest` (they go to `aux_detections` if they have `aux_quads`).
2. Apply `sensitivity`: minimum confidence = `1 - sensitivity` clamped to 0.15-0.85; with `mask_when_unsure` the floor drops by 0.15 for cards, QR and faces.
3. Pad by `padding_px` (default 12; faces 20 % of box size), clamp to the canvas.
4. Emit `MaskRequest(frame_pts=pts, pts_start=pts, pts_end=pts + lane_valid_for, style, reason)`. `lane_valid_for` is `max_age_frames / fps`: FAST 0.2 s, ACCURATE 0.4 s. Requests continue to be emitted while hysteresis holds a track (so a mask outlives one missed detection).
5. Merge: quads of the same category and style with IoU above 0.5 collapse to the union bounding quad; text boxes on one line merge into one quad.
6. `RISKY_TEXT` yields a `Detection` only (consumed by `guard/`); no `MaskRequest` unless `categories[RISKY_TEXT].enabled` is true (default false).

### 5.4 `tracking/`
- ByteTrack through `supervision` (MIT): `track_activation_threshold 0.25`, `lost_track_buffer 15` (1.5 s at FAST rate), `minimum_matching_threshold 0.8`.
- Hysteresis (adapt `privastream/.../pii_blur/pii_detector.py` `Hysteresis`): activate after `K_confirm=2` hits, except confidence at least 0.8 or `mask_when_unsure` categories confirm immediately (K=1). Hold after the last hit: faces 0.8 s, text categories 1.0 s, cards and QR 2.0 s. GC after 3x hold.
- Smoothing: EMA on corners (alpha 0.5) while IoU with the previous quad is above 0.3; snap otherwise.
- Global motion is projected by the engine; the tracker only associates detections.

### 5.5 `detectors/faces/` (T2.6)
1. YuNet 2023mar through `cv2.FaceDetectorYN` on the 640x360 frame, score threshold 0.6 (0.5 with `mask_when_unsure`), NMS 0.3, top-k 50. CPU; ORT GPU adapter optional.
2. For new tracks, and every 5th detection of a track, align and embed with SFace 2021dec (`FaceRecognizerSF`, 128-d, L2-normalised); cosine similarity to each enrolled person; a sample "matches" if `max cos >= 0.40` (stricter than the OpenCV Zoo 0.363; tune on eval).
3. **Per-track voting** (replaces PrivaStream's shared vote buffer, which leaked a stranger's mask to the creator's votes): each track keeps its own last 7 samples; `allow=True` only when at least 5 of 7 match and the track has at least 3 samples. New or unconfirmed tracks are masked. A track that flips to non-match for 3 consecutive samples is masked again.
4. Dilate each box 20 %; low-light CLAHE when mean luma is under 60 (adapt PrivaStream `face_detector.py`).
5. `aux_quads["mouth"]` from YuNet landmarks (order: right eye, left eye, nose, right mouth corner, left mouth corner): centre on the two mouth corners, width `1.4 * d`, height `1.2 * d` where `d` is the corner distance.
6. Enrolment (`faces/enroll.py`, library function used by `app/api`): at least 20 frames of the largest face, average the L2-normalised embeddings, re-normalise, store `{person_id, label, model: "sface_2021dec", embedding: [128], created}` as JSON in `vision.allowlist_dir` with file mode 0600; provide `delete_all()`. Embeddings are biometric data: gitignored, never logged, never sent out, and enrolment is user initiated. Do not use PrivaStream's `creator_embedding.json` (not in the copy) or buffalo_l.
7. Optional adapter for InsightFace buffalo_s behind an extra; marked "replace before commercial release" in `docs/THIRD_PARTY.md`.

### 5.6 `detectors/cards/` (T1.3)
- **Verhoeff** (`patterns/verhoeff.py`). `d` = dihedral multiplication table, `p` = permutation table, `inv` = inverse table:
  `d = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],[6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],[9,8,7,6,5,4,3,2,1,0]]`;
  `p = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],[2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]`; `inv = [0,4,3,2,1,5,6,7,8,9]`.
  Validate: `c = 0; for i, ch in enumerate(reversed(digits)): c = d[c][p[i % 8][int(ch)]]`; valid iff `c == 0`. Generate: append check digit `inv[c]` computed over the digits followed by a 0. Aadhaar is 12 digits, first digit 2-9, optionally grouped `NNNN NNNN NNNN`, and Verhoeff-valid. Also match a 16-digit masked variant `XXXX XXXX NNNN` only as a hint (no mask).
- **PAN**: `(?<![A-Z0-9])[A-Z]{5}[0-9]{4}[A-Z](?![A-Z0-9])`; 4th character in `{A,B,C,F,G,H,L,J,P,T,K}` raises confidence to 0.9, otherwise 0.6.
- Flow per ACCURATE frame: OCR boxes, find a Verhoeff-valid 12-digit run (also across two or three adjacent boxes of one line) or a PAN match, then expand to the card.
- **Card quad expansion**, in order: (a) crop around the number box (3x width, 4x height), grayscale, Canny plus morphological close, `findContours`, `approxPolyDP`; take the largest convex 4-point contour that contains the number box with aspect 1.3-1.9 (ID-1 is 1.585, perspective widens the range) and area at least 4x the box; (b) fallback: bounding hull of all OCR text boxes within 2.5x the number-box width, padded 8 %; (c) last resort: the number box padded 150 % (capped to the frame). Always mask the whole card, never only the digits.
- **Card-like without a readable number** (blurred or far): an ID-1 quad containing at least 6 text boxes is masked as `AADHAAR` with confidence 0.5, `reason="card_like"`, when `mask_when_unsure` is on.
- A confirmed card keeps masking 2.0 s after it is lost.

### 5.7 `detectors/upi_qr/` (T2.7)
`cv2.QRCodeDetectorAruco` and WeChat QR (opencv-contrib) at the FAST lane, plus `zxing-cpp` on candidate crops; budget at most 20 ms. Decode payload; if it starts with `upi://` (case-insensitive) or contains `pay?pa=` mask it as `UPI_QR` (confidence 0.95). Payloads are discarded immediately and never put in `extra`. A QR-like code that fails to decode is masked as `UPI_QR` with `reason="undecodable_qr"` when `mask_when_unsure` is on. Non-UPI QRs pass.

### 5.8 `detectors/plates/` (T2.8)
OCR-first. Normalise to uppercase alphanumerics (join two-line plates stacked within one box height). Formats: standard `^[A-Z]{2}\d{2}[A-Z]{1,3}\d{4}$`; older and single-digit RTO `^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}$`; Bharat series `^\d{2}BH\d{4}[A-Z]{1,2}$`. Confusion repair by position (state code letters, RTO digits, series letters, serial digits). A match needs a plate-like aspect (2:1 to 5:1) and sits on a vehicle-like region: optional Apache-2.0 vehicle detector (YOLOX-nano ONNX, UNVERIFIED availability); without it, accept on format match alone. Optional YOLO plate weights are an AGPL extra, loaded only if present; never required.

### 5.9 `detectors/text/` (T2.4)
Regexes on normalised OCR text (digits already ASCII):
- Indian mobile: `(?<!\d)(?:(?:\+?91|0)[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)`.
- PIN code: `(?<!\d)[1-9]\d{2}\s?\d{3}(?!\d)`; masked only with a cue (`pin`, `pincode`, `पिन`, address words) in the same or an adjacent box, or when the box is at most 40 characters and sensitivity is at least 0.5 (tuning UNVERIFIED).
- UPI ID: `[A-Za-z0-9._-]{2,64}@[A-Za-z][A-Za-z0-9]{1,31}` where the domain part has no dot; confidence 0.9 for known handles (`oksbi, okhdfcbank, okicici, okaxis, ybl, ibl, axl, paytm, apl, upi, sbi, hdfcbank, icici, axisbank, pnb`), 0.6 otherwise.
- Email: `[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}`.
- PAN also matched here for free text.
- **Signboards** (`SIGNBOARD`): detection-only mask-all-text. Any text box with height at least `signboard.min_text_height_px` (14 px at 720p) and det confidence at least 0.5 is masked, with no recognition and no script assumptions (covers Devanagari, Kannada, Tamil, Telugu, Bengali). Nearby boxes on one line merge. Intended for IRL; disabled by `categories.signboard.enabled=false` for gaming scenes where HUD text would blank the stream.
- `RISKY_TEXT`: configurable blocklist (`guard.risky_text_blocklist`) on normalised text; emits `Detection` only.
- Indic recognisers (`ocr/recognizers/`): devanagari, ta, te via RapidOCR ONNX; optional EasyOCR for kn/bn on crops.

### 5.10 `detectors/screens/` (T2.9)
- **OTP/notification screens**: from OCR text, cue patterns (`otp`, `one time password`, `verification code`, `do not share`, `is your`, `वन टाइम`, `ओटीपी`) plus a 4-8 digit code within the same or adjacent box. Mask the text box and its enclosing banner (rounded rectangle from contours); if the banner lies inside a detected phone-screen quad (bright high-contrast rectangle, aspect near 9:19.5 or 19.5:9, containing at least 5 text boxes) mask the whole screen quad. Confidence 0.8.
- **BGMI and Free Fire**: template matching of a lobby/room UI anchor (multi-scale `matchTemplate`) at ACCURATE rate, then fixed normalised regions for player ID, custom-room ID and password (`GAME_ID`, `ROOM_CREDENTIAL`). Numeric patterns as secondary confirmation: ID 8-12 digits, room ID and password 4-8 characters (lengths UNVERIFIED). Region templates need calibration screenshots from the user's phone; the synthetic overlays from `eval/` are the test fixtures until then.

## 6. Reuse

| Source (`reference/`) | Target | Change |
|---|---|---|
| `irl-shield/shield/lanes.py` | `vision/lanes.py` | rate/stride per lane, extend result beyond quads |
| `irl-shield/bench/onnx_detectors.py`, `static_model.py`, `detector_options.py` | `vision/ort.py`, `ort_static.py`, `options.py`, `ocr/det.py` | move out of `bench/`, provider check, `disable_fallback`, arena limit, RapidOCR wrapper |
| `irl-shield/models/prepare.py`, `manifest.json` | `models/` | add our models |
| `privastream/.../pii_blur/pii_detector.py` (`Hysteresis`) | `tracking/hysteresis.py` | box IoU to tracks |
| `privastream/.../face_blur/face_detector.py`, `enroll_face.py` | `detectors/faces/` | ideas only (CLAHE, dilation, TTA idea); YuNet+SFace; per-track votes |
Never load `pii_clf.joblib` (pickle). Never copy PrivaStream plate code (AGPL path, broken constructor).

## 7. Edge cases

Tilted, blurred, distant, glared or partly covered cards; many faces; tiny text; fast motion; non-Latin digits; OCR confusions; lane running late (planner marks stale so the engine PROTECTs); allowlisted person turning away (track lost, masked on reappearance until re-confirmed); two cards in frame; QR on a phone screen; text on a streamer's own overlay (signboard category off); enrolled face with glasses or mask (lower match rate leads to masking, which is the safe side); camera exposure changes.

## 8. Config keys used

`detect.stride`, `detect.fast_hz`, `detect.accurate_hz`, `detect.accurate_width`, `gpu.enabled|mem_limit_mb|ort_provider`, `categories.<cat>.enabled|style|sensitivity|mask_when_unsure|padding_px`. Added to `Config` (D-25): `vision.allowlist_dir`, `vision.face_match_cos` (0.40), `vision.face_vote_window` (7), `vision.face_vote_min` (5), `ocr.rec_max_crops` (8), `ocr.rec_budget_ms` (150), `signboard.min_text_height_px` (14), `guard.risky_text_blocklist`.

## 9. Test cases (CPU only, SPECIMEN data from `eval/`)

1. Registry finds every `vision/detectors/<id>/` exporting `DETECTOR`; a package without it fails loudly; no detector imports another.
2. Lane: stride 3 processes exactly frames 0, 3, 6; a 400 ms detector in the ACCURATE lane drops frames and never blocks the FAST lane.
3. Verhoeff: all 100 generated SPECIMEN numbers validate; flipping any one digit (and any adjacent transposition) fails.
4. PAN and regex tables: positive and negative lists for PAN, phone (with `+91`, `0`, separators), PIN, UPI ID vs email, plates (standard, BH, single-digit RTO), game IDs.
5. Cards: SPECIMEN Aadhaar and PAN renders with tilt up to 25 degrees, glare, blur sigma 2, occlusion of 30 % of the card: recall at least 95 % in the clean and tilt sets; whole-card quad covers at least 90 % of the card area.
6. `card_like` fallback fires on a blurred card with no readable number and not on a plain rectangle.
7. UPI QR: `upi://pay?pa=...` masked; non-UPI QR passes; undecodable QR masked when `mask_when_unsure`; payload not present in any `Detection.extra`.
8. Faces: enrolled person visible (at least 95 % of frames), strangers masked (at least 95 %); a stranger beside the enrolled person is never unmasked by the neighbour's votes (regression for the shared-buffer bug); mouth quad within the true mouth region.
9. Tracking: a static target shows at most 2 % flicker (on, off, on within 1 s); hysteresis holds masks for the configured seconds after a missed detection.
10. Text: signboard mask-all covers Devanagari, Kannada, Tamil, Telugu, Bengali SPECIMEN boards (at least 85 % of text boxes); structured PII recall at least 90 %.
11. Screens: OTP banner and phone-screen masks on SPECIMEN renders (at least 85 %); BGMI/Free Fire overlays at least 90 %.
12. Planner: disabled category yields no requests; `allow=True` face yields none; overlapping quads merge; `RISKY_TEXT` yields none by default.
13. Privacy: a log capture of a full run contains no OCR text, no QR payload and no embedding values.
14. Perf (CPU only, marked): FAST at most 25 ms/frame, ACCURATE at most 450 ms/frame on the dev laptop.
15. **Acceptance:** T1.3 SPECIMEN card recall at least 95 % through `LaneHost`; T2.x per-category recall in `docs/PLAN.md` gate G2 (faces 95, cards 95, QR 98, signboards 85, phone/PIN/UPI/email 90, OTP 85, game IDs 90, plates 85).

## 10. Latency target

FAST at most 25 ms per frame (result within 0.1 s of arrival); ACCURATE at most 450 ms on CPU, at most 120 ms with GPU; tracking and planning at most 5 ms; result `completed_at <= arrival + D - 0.5 s`.

## 11. VRAM and CPU target

Default CPU. If `gpu.enabled`: `dev` at most 0.8 GB for vision (OCR det/rec about 0.4 GB, arena capped by `gpu_mem_limit`), `demo` at most 2.0 GB. CPU about 1.0-1.5 cores for all lanes. Every detector declares `vram_mb`; the host refuses a config whose sum exceeds the profile cap. GPU runs use `flock /tmp/streamsarthi-gpu.lock`.

## 12. Tasks

| Task | Wave | Depends | Scope |
|---|---|---|---|
| T0.5 | 0.B | contracts | registry, lanes, ORT helper, manifest and downloader, latency probe |
| T1.3 | 1 | T0.5 | `ocr/`, `patterns/`, `detectors/cards/`, `host.py`, `planner.py` |
| T2.3 | 2.A | G1 | `tracking/`, planner finalised |
| T2.4 | 2.A | G1 | `detectors/text/`, `ocr/recognizers/` |
| T2.6 | 2.B | T2.3 | `detectors/faces/` |
| T2.7 | 2.B | T2.3 | `detectors/upi_qr/` |
| T2.8 | 2.B | T2.4 | `detectors/plates/` |
| T2.9 | 2.B | T2.4 | `detectors/screens/` |
| T7.5 | 7.B | T7.1 | fix red-team misses |

## 13. Out of scope

Model training or fine-tuning (Colab later), ByteTrack-based local motion beyond association, audio, chat, NSFW or weapon detection, face recognition for anything but the allowlist, storing any image, crop, text or embedding other than enrolled embeddings, Ultralytics or InsightFace as required dependencies.
