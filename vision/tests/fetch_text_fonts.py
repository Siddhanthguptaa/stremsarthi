"""Fetch the Noto Sans fonts the T2.4 text scenes need (Tamil, Telugu, Kannada, Bengali) into ``.tmp/fonts``.

    python vision/tests/fetch_text_fonts.py

Source: the official Google Fonts repository (https://github.com/google/fonts), pinned to one commit, SIL Open
Font License 1.1 (the OFL text is saved beside each font). Every file is verified against a SHA-256 before it is
kept; nothing downloaded is executed. Devanagari and the Latin test font are already in ``engine/assets``.
T2.1 may provide the same fonts under ``eval/fonts`` and then this script is not needed
(``vision.testing_text.find_font`` looks there first).
"""

from __future__ import annotations

import hashlib
import sys
import urllib.parse
import urllib.request
from pathlib import Path

COMMIT = "869789e84da92782c144d83d69b64cec54905b5f"
BASE = f"https://github.com/google/fonts/raw/{COMMIT}/ofl"
FONTS = {  # directory in google/fonts -> (file name, sha256)
    "notosanstamil": (
        "NotoSansTamil[wdth,wght].ttf",
        "aa3a9b321f4b0bb2c40203ffbde9af89713227866e0e13f76e5b9eeea727cf88",
    ),
    "notosanstelugu": (
        "NotoSansTelugu[wdth,wght].ttf",
        "e618af7bf999df192ed4f388eba2e563f2b5015034e9cbb317b5bd793bd7334d",
    ),
    "notosanskannada": (
        "NotoSansKannada[wdth,wght].ttf",
        "cca4f3b3a8cb12fb261f1b43baf5d2f7f59d90fe123d41f0065ed3a183997ec9",
    ),
    "notosansbengali": (
        "NotoSansBengali[wdth,wght].ttf",
        "dcd42978094e584a849c84a51450eeac40c8826057d566ea6d4b9627a403a05a",
    ),
}


def main() -> int:
    out = Path(__file__).resolve().parents[2] / ".tmp" / "fonts"
    out.mkdir(parents=True, exist_ok=True)
    for folder, (name, sha) in FONTS.items():
        short = name.split("[")[0] + "-VF.ttf"
        target = out / short
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == sha:
            print("ok", short)
            continue
        url = f"{BASE}/{folder}/{urllib.parse.quote(name)}"
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - pinned https URL
            data = resp.read()
        if hashlib.sha256(data).hexdigest() != sha:
            print("SHA-256 mismatch for", short, file=sys.stderr)
            return 1
        target.write_bytes(data)
        lic = urllib.request.urlopen(f"{BASE}/{folder}/OFL.txt", timeout=60).read()  # noqa: S310
        (out / f"OFL-{short.split('-')[0]}.txt").write_bytes(lic)
        print("fetched", short)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
