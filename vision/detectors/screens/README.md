# `vision/detectors/screens/` (T2.9)

OTP / notification screens and BGMI / Free Fire player IDs and custom-room credentials. ACCURATE lane, CPU only,
no model of its own (it reads the lane's shared OCR pass; `vram_mb = 0`). Spec: `docs/specs/vision.md` 5.10.

| File | What |
|---|---|
| `detector.py` | the plugin (`DETECTOR`): OCR pass, OTP route, game routes, detections with codes only |
| `otp.py` | cue + 4-8 digit code (same paragraph, or a code-only box under a heading), banner by colour, phone screen by contour |
| `games.py` | label route: `ID:` / `UID:` / `ROOM ID` / `PASSWORD` plus value-shape validation, needs game context |
| `templates.py` | template route: multi-scale `matchTemplate` of a UI anchor, then fixed regions relative to it |
| `templates/` | shipped anchors (`*.png` + `*.json`), derived from the SPECIMEN overlays in `eval/` |

Categories: `OTP_SCREEN`, `GAME_ID`, `ROOM_CREDENTIAL`. Params (`DetectorConfig.params`): `templates` (list of
`GameTemplate`), `templates_dir`, `screen_mask` (default true: mask the whole phone screen), `screen_min_boxes` (5).

Important: the shipped templates and the label/region geometry were built against our own SPECIMEN overlays, not the
real games (layouts change with the game version and the phone: UNVERIFIED). Calibrate on screenshots from the
streamer's phone with `vision.testing_screens.calibrate(...)` + `vision.detectors.screens.templates.save_template`
before relying on the template route. Player-ID (8-12 digits) and room ID / password (4-10 characters) lengths
are UNVERIFIED too.

## Run

```
# unit tests (CPU, fake OCR, no models; the games file needs eval fonts)
MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -u root -- bash scripts/ss.sh python -m pytest \
    vision/tests/test_vision_screens_otp.py vision/tests/test_vision_screens_games.py -x -q
# real OCR on SPECIMEN scenes (skips without OCR weights)
... python -m pytest vision/tests/test_vision_screens_real.py -x -q
# acceptance: recall, false alarms, latency on eval/ scenes
... python -m vision.accept_screens --otp 60 --games 30 --json /tmp/screens.json
... python -m vision.accept_screens --ablate no_templates      # label route alone
# rebuild the shipped SPECIMEN templates
... python -m vision.testing_screens
```
