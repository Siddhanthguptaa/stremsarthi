"""Regenerate the caption shaping reference renders: run once through scripts/ss.sh, then LOOK at the PNGs.

    python -m engine.tests.gen_engine_caption_goldens

The goldens are the raqm-shaped coverage masks of conjunct-heavy strings (one per script) at a fixed
size. ``test_engine_captions_shaping.py`` compares fresh renders to them pixel by pixel, and compares
them to the unshaped (basic layout) renders to prove shaping really happened.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from engine.captions import CaptionRenderer

GOLDEN_DIR = Path(__file__).with_name("golden_captions")
SIZE = 56
# (name, lang, text): conjuncts, split vowels and reordering that only a shaping engine gets right
CASES = (
    ("devanagari", "hi", "क्षत्रिय श्रृंगार स्वतंत्रता"),  # ksha, tra, shra + vocalic r, sva + anusvara
    ("kannada", "kn", "ಕ್ರಿಯೆ ಸ್ವಾತಂತ್ರ್ಯ"),  # kra + i, sva, repeated conjunct stacks
    ("tamil", "ta", "ஸ்ரீ க்ஷ கொ கௌ"),  # sri, ksha, split vowels ko and kau (glyphs both sides of the consonant)
    ("telugu", "te", "క్రియ స్వాతంత్ర్యం"),  # stacked subjoined consonants
    ("bengali", "bn", "ক্ষত্রিয় শ্রী"),  # ksha, tra with ya-phala, shra
)


def render(name: str, lang: str, text: str, *, raqm: bool = True):
    return CaptionRenderer(use_raqm=raqm).text_mask(text, lang, SIZE)


def main() -> None:
    GOLDEN_DIR.mkdir(exist_ok=True)
    for name, lang, text in CASES:
        mask = render(name, lang, text)
        Image.fromarray(mask).save(GOLDEN_DIR / f"{name}.png", optimize=True)
        print(name, mask.shape)


if __name__ == "__main__":
    main()
