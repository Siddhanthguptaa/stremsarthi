# Gate 2 (video accuracy and real-time) in detail

Entry points: `scripts/gate_phase2.sh` (the gate), `python -m eval.run phase2 --leg preflight|dataset-check|offline|rescore|perf`,
`python -m eval.run phase2-report`, `scripts/gate_phase2_acceptance.sh` (seeded-defect proof). Code: `eval/phase2/`.

## Legs
- `offline`: every detector the registry supplies over the stills and the 30 fps clips, on the CPU, no stream. Scoring is mask
  coverage of the ground-truth quad by what the production planner emits (`vision.planner.MaskPlanner`). A detector that is
  missing, broken or cannot load is NOT RUN (never a pass). One process at a time (25 to 40 min on the dev laptop).
- `perf`: real chain with all detectors on (`many_targets`, 120 s), CPU and GPU (under `flock /tmp/streamsarthi-gpu.lock`).
- Report: `eval/reports/<date>-phase2[-offline|-quick|-seeded|-subset].md` plus a JSON twin. Gate exit codes: 0 pass,
  1 fail or NOT RUN, 2 environment error, 3 required hardware leg skipped.

## Scoring rules worth knowing
- Stills run the FAST lane `cfg.vision.face_vote_min + 2` times (7), so an allowlisted face can collect the >= 5 matched votes of 7
  the detector needs (`--fast-still-runs` overrides; the report says when it was overridden).
- **Allowlisted faces on clips have a warm-up.** The detector masks an allowlisted face for about 0.5 to 1.4 s while votes accrue
  (spec 5.5; a false allow is a privacy leak, so the detector is not weakened). Three numbers are reported: `settled` (from 1.0 s
  after the track first appears: the gate row), `steady` (from 2.0 s) and `all frames`. Face clips are longer (10 s, 6 clips) for this.
- `neg_text` false masks count only phone_number, pin_code, upi_id, email and pan masks: the text detector's `mask_all_text`
  rule masks every signboard-like box there by design. `neg_scene` (natural scenes and text-free UI) is the signboard negative,
  reported, not gated.
- Latency: the recall pass lifts `ocr.rec_budget_ms` to 5000 for repeatable recall. The ACCURATE lane is judged in a second,
  latency pass at the production budget (150 ms) over `--latency-per-bucket` stills per bucket (check: p95 <= 450 ms); the report
  shows both and the recall at each budget on the same stills.

## Dataset
`python -m eval.run gen-video --out /tmp/ss_phase2_video --n 40 --clips 4 --face-clips 6 --face-clip-seconds 10` (27 buckets).
To change only some buckets of an existing dataset: `... --refresh-face-clips --clips 4 --face-clips 6 --face-clip-seconds 10` or
`--refresh-buckets neg_scene` (re-renders those items and patches `manifest.json`; everything else stays byte-identical).

## Seeded-defect acceptance
`--seeded-defect blind:cards|crash:text|absent:plates` breaks one detector in memory. `scripts/gate_phase2_acceptance.sh` runs a
clean build and the three defective builds on a small subset and exits 0 only when the gate flagged each defect.
