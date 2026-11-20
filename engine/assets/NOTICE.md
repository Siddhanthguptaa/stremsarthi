# Fonts used by the engine (slates now, captions in T5.4)

Both fonts are licensed under the SIL Open Font License 1.1. The licence text sits next to each font and must stay with it.

| File | Font | Licence text | Source | SHA-256 |
|---|---|---|---|---|
| `AtkinsonHyperlegibleNext-wght.ttf` | Atkinson Hyperlegible Next (variable, weight axis), Copyright 2020-2024 The Atkinson Hyperlegible Next Project Authors | `OFL-AtkinsonHyperlegibleNext.txt` | copied from `reference/irl-shield/shield/presentation/assets/` (IRL Shield, MIT; the font itself is OFL-1.1) | `5a455d1cfa099b601ab70751bb9673e8fe1854dc4500c80e1a220d0d75e31745` |
| `NotoSansDevanagari-VF.ttf` | Noto Sans Devanagari (variable: weight 100-900, width 62-100), Copyright 2022 The Noto Project Authors | `OFL-NotoSansDevanagari.txt` | `https://github.com/google/fonts/raw/main/ofl/notosansdevanagari/NotoSansDevanagari[wdth,wght].ttf`, fetched 2026-11-20 (official Google Fonts repository, upstream `notofonts/devanagari`), renamed on copy | `14ec4af41f27482216d1c2229f417ff9b1425e1babb014e57d1d40d03229853e` |

The OFL texts: `OFL-AtkinsonHyperlegibleNext.txt` has SHA-256 `a1d2975ff736f64e5d9eb34579abb93a0e02b5ceb59c8a2c2fa7db53badfa1fd`, `OFL-NotoSansDevanagari.txt` has `a216f6f8d85c7228093e0ee5e258d9d377e6671f68acb4db1930b29583d0f331`.

The fonts are loaded with Pillow and libraqm (`PIL.features.check("raqm")` is true with the Pillow wheel in `.venv`), so Devanagari conjuncts are shaped. They are not modified. `engine/tests/test_engine_slate.py` checks the hashes above against the files.

Kannada, Tamil, Telugu and Bengali Noto fonts arrive with the caption renderer (T5.4).
