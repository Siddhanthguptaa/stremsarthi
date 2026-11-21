# guard/music (T5.1)

Music presence (YAMNet) and demo-library fingerprint match. CPU only, VRAM 0. Emits `RiskSignal`s
(`music` ttl 10 s every second while ON; `fp:<id>` COPYRIGHT_MATCH score 0.95 ttl 60 s).

- `detector.py` YAMNet ONNX scorer (SHA-256 pinned, class index looked up by name), EMA + on/off state machine.
- `spectral.py` weaker fallback scorer, used only if the weights are missing (a warning is logged).
- `fingerprint.py` Chromaprint raw fingerprint via `libchromaprint` (ctypes, LGPL, dynamic) plus a numpy
  landmark fallback (own implementation, audfprint-style). `library.py`, `build_library.py`: JSON of ids and fingerprints only.
- `service.py` `MusicGuard`: `push(samples)` never blocks; `poll()` or `start(callback)` does the work.
- `synth.py` seeded synthetic songs (demo library, tests). `evaluate.py` prints the acceptance numbers.
- `library/fingerprints.json` demo library: 5 SYNTHETIC songs. Real songs: put them in
  `eval/data/private/music` and run `python -m guard.music.build_library --src eval/data/private/music --out <dir>/fingerprints.json`.

Weights: `models/weights/yamnet.onnx` (manifest entry requested from the lead; see `data/NOTICE.md`).

Run (WSL, through scripts/ss.sh):
`pytest guard/tests/test_guard_music_*.py -q` and `python -m guard.music.evaluate [--fallback]`.

Limits: no match after a tempo or pitch change of 15 %, covers or live versions; not Content ID parity.
Numbers are on synthetic music and TTS speech only.
