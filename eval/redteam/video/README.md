# eval/redteam/video (T7.1)

Adversarial SPECIMEN video suite. Pushes synthetic clips through the real vision detectors and the production mask
painter and reports, per attack, the fraction of frames where the target is still readable in the output.

Attacks: partial occlusion (card, QR), tilt/perspective, small and distant targets, glare, screen of a screen, photo
of a photo, rapid pan and shake, targets entering and leaving the frame, a card shown for under 1 s, unusual fonts,
mixed-script text, PII split across lines, separators and zero-width characters, faces (edge, turned, sunglasses,
mask, small), scene cuts and frame drops. `python -m eval.run redteam-video --list` prints every case.

Scoring (per frame with an exposed target):
- proxy leak: masks cover < 90 % of the target quad (same `cov` as `eval/phase2`).
- confirmed leak: the frame is painted in the category style and the target is still recoverable from the OUTPUT
  (zxing decodes the QR, RapidOCR reads the number/phone/UPI id/plate). Faces: proxy only.
- leak = proxy or confirmed. Proxy leaks whose target is unreadable even unmasked are counted as `src-unreadable`.

Run (inside WSL, one process at a time, CPU only):

    bash scripts/ss.sh python -m eval.run redteam-video --selftest          # seeded broken detectors are flagged
    bash scripts/ss.sh python -m eval.run redteam-video --quick             # subset run + report
    bash scripts/ss.sh python -m eval.run redteam-video --attacks flash_card tilt_qr --no-report
    bash scripts/ss.sh bash scripts/gate_redteam_video.sh [--quick|--full]  # self-check + run + regression check

Outputs: `eval/reports/<date>-redteam-video.{md,json}` (numbers, ranked misses), `/tmp/ss_redteam_video/records.jsonl`
and `/tmp/ss_redteam_video/repro/<case>/f<frame>_{input,output}.png + _meta.json` (minimal repro per leaking case;
synthetic SPECIMEN frames, regenerable from the seed). Baseline: `--write-baseline` stores
`eval/baselines/redteam_video.json`; the gate fails on a rise of more than 5 points.

Tests: `bash scripts/ss.sh python -m pytest eval/tests/test_redteam_video.py -q` (CPU, no models).
Files: `attacks.py` (catalogue), `scene.py`/`textscenes.py` (rendering), `pipeline.py` (sources + scoring),
`readback.py` (output decode/OCR), `score.py` (aggregation), `report.py`, `cli.py`.
