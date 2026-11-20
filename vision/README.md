# vision

Video detector SDK and the T1.3 card pipeline. Spec: `docs/specs/vision.md`.

- `registry.py`  autodiscovers `vision/detectors/<id>/` (exports `DETECTOR`), validates the `Detector` protocol, checks `vram_mb`.
- `lanes.py`     `LaneWorker`: newest-frame-wins thread per lane (FAST stride, ACCURATE rate). Every run yields a `LaneResult`.
- `host.py`      `build(config, bus)` -> `LaneHost`; wires `planner.planner_factory` (one `MaskPlanner` + tracker per lane).
- `planner.py`   `MaskPlanner`: detections -> tracks -> `MaskRequest`s (enabled/style/padding/sensitivity, validity window, hold,
  velocity projection of held/moving masks up to 2 s, merge). `Tracker` protocol; `IouHoldTracker` is the automatic fallback.
- `tracking/`    `bytetrack.py` (in-house ByteTrack, D-31), `hysteresis.py` (K-confirm/hold on tracks, `TrackVotes` for the face
  allowlist), `tracker.py` (`ByteTrackHold`, the planner's tracker), `sim.py` (synthetic trajectories, flicker/coverage metrics).
- `ocr/`         `OcrService` (`contracts.OcrProvider`): PP-OCR det (v6 tiny) + Latin rec on ranked crops, memoised per frame.
  Installed in `VisionResources.ocr` (lazy; CPU default, CUDA with `gpu.*`). `normalize.py`, `layout.py` are shared helpers.
- `patterns/`    Verhoeff, Aadhaar runs, PAN (+ OCR-confusion repair). Pure text.
- `detectors/cards/`  Aadhaar + PAN: OCR hit -> whole-card quad (contour, text block, padded), `card_shape_unsure`, `card_like`.
- `ort*.py`, `options.py`, `resources.py`, `probe.py`  ORT session factory and static PP-OCR graphs (T0.5; IRL Shield, MIT).
- `testing.py`, `accept_cards.py`  SPECIMEN scene generator/scoring and the T1.3 acceptance runner. Licences: `LICENSE.irl-shield`, `LICENSE.privastream`.

```
bash scripts/ss.sh python models/prepare.py --profile dev          # fetch + verify weights (models/weights/)
bash scripts/ss.sh python -m pytest vision models -q               # CPU tests (real-OCR ones skip without weights)
bash scripts/ss.sh python -m pytest vision -q -m slow -s           # T1.3 acceptance on 72 SPECIMEN positives (CPU)
bash scripts/ss.sh python -m vision.accept_cards --json out.json   # same, as a report
bash scripts/ss.sh flock /tmp/streamsarthi-gpu.lock python -m vision.accept_cards --provider cuda   # GPU + VRAM
bash scripts/ss.sh python -m pytest vision/tests/test_vision_tracking*.py vision/tests/test_vision_planner*.py -q -s   # T2.3 tracking, flicker, perf
bash scripts/ss.sh python -m vision.probe                          # CPU ms per model
bash scripts/ss.sh ruff check vision && bash scripts/ss.sh ruff format --check vision
```
