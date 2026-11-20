# Fonts used by the SPECIMEN video generators

Noto Sans (Latin) and Noto Sans Devanagari, Kannada, Tamil, Telugu and Bengali. Licence: SIL Open Font
License 1.1 (full text per family in `OFL-<family>.txt`). Copyright The Noto Project Authors.

Source: the Google Fonts repository, pinned to commit `869789e84da92782c144d83d69b64cec54905b5f`
(`https://github.com/google/fonts`, directory `ofl/<family>/`). These are the variable builds
(weight and width axes). `eval/data_gen/common/fonts.py` verifies the SHA-256 below before use and can
re-download a missing or damaged file from the pinned URL (fails closed on a hash mismatch).

| file | family dir | size (bytes) | SHA-256 |
|---|---|---|---|
| NotoSans-VF.ttf | notosans | 2049096 | bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d |
| NotoSansDevanagari-VF.ttf | notosansdevanagari | 641944 | 14ec4af41f27482216d1c2229f417ff9b1425e1babb014e57d1d40d03229853e |
| NotoSansKannada-VF.ttf | notosanskannada | 641372 | cca4f3b3a8cb12fb261f1b43baf5d2f7f59d90fe123d41f0065ed3a183997ec9 |
| NotoSansTamil-VF.ttf | notosanstamil | 340668 | aa3a9b321f4b0bb2c40203ffbde9af89713227866e0e13f76e5b9eeea727cf88 |
| NotoSansTelugu-VF.ttf | notosanstelugu | 888808 | e618af7bf999df192ed4f388eba2e563f2b5015034e9cbb317b5bd793bd7334d |
| NotoSansBengali-VF.ttf | notosansbengali | 463668 | dcd42978094e584a849c84a51450eeac40c8826057d566ea6d4b9627a403a05a |

Indic text must be shaped with libraqm (`PIL.features.check("raqm")` is true in this environment); without it
the generators refuse to render Indic scripts instead of drawing wrong glyphs.

Redistribution: OFL 1.1 allows bundling the unmodified font files with the project; the font files are not
sold on their own and keep their licence texts next to them.
