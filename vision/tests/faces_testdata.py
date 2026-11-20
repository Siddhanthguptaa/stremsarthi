"""Public-domain portrait fetcher for the face tests and the T2.6 acceptance run (network only here).

Source: NASA Image and Video Library (images-assets.nasa.gov, the ``~medium.jpg`` rendition). Licence: NASA
media are not copyrighted and may be used freely (NASA Media Usage Guidelines,
https://www.nasa.gov/nasa-brand-center/images-and-media/); every photo below is an official NASA-JSC, NASA-HQ
or NASA/ISS image. Each file is pinned by SHA-256, so a changed download is rejected. Eight people, 3 to 5
different photos each (official portraits from different years, suits and on-orbit shots).

Cached under ``<repo>/.tmp/facedata/photos`` (gitignored, never committed). Nothing is downloaded unless a
file is missing; ``ensure_photos`` returns ``None`` when offline so callers can skip. Never executes
downloaded content; JPEGs are only decoded by OpenCV later.

Caveat for reports: astronaut portraits are a stand-in. They are frontal, well lit and not representative of
the streamers (Indian faces, phone cameras, mixed lighting); thresholds tuned on them need re-checking on
consented teammate footage.
"""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / ".tmp" / "facedata" / "photos"
URL = "https://images-assets.nasa.gov/image/{id}/{id}~medium.jpg"

# person -> [(nasa_id, sha256)]
PHOTOS: dict[str, list[tuple[str, str]]] = {
    "meir": [
        ("NHQ202009150005", "c99c8f145e5083ecb0b5d47722a510ccfea94f47bb1a524c6b7a16fe4e2308e5"),
        ("jsc2025e078605_alt", "3177035d2ef10190bf921fc223cd2624293c75a6e74d9e32c071d31df1c08473"),
        ("iss074e0365172", "e29f7aec7cf98a275dd6d2f155005e132b4c5537c9151f25b94397d66f483715"),
        ("iss074e0832632", "4024c76642c689b05fdfa81d99d2fb570c578444ec55a30c40392c93080f2e9c"),
        ("iss074e0832627", "cf850e92adf73a3ffda569aa04f97599870a2bb2fd6f68392d67844e7c88abd0"),
    ],
    "barron": [
        ("NHQ202009160003", "8278434d850e68f75a8d17b490934103e036e8685d1eba9249e609ceeabd8686"),
        ("NHQ201907120010", "b14bc4ab5595af5a224e7802019c8f0085351cecad78341153de58ae516ba609"),
        ("jsc2021e044312_alt", "19e9b94ed6a4f5c10a3e9adce4389abb8862021c85b97490cc89d95fdb6f7ddc"),
        ("jsc2021e044353_alt", "92277678e5337d76ca4aa0ee451d842da0851998e114275a210115e0f79ddeb3"),
        ("jsc2021e019446_alt", "760e1775563111320546671615da51025d7271bc7ecba54f11c7baa16e4fab19"),
    ],
    "chari": [
        ("NHQ202009080001", "a7878e55e6822d3349b2e3989632ef2acea4aace18499892d04923518cb85423"),
        ("NHQ201907120006", "58815033422e96cf513075b1cbe13e451a8ebd918952a9f0692826b76bd0bf67"),
        ("NHQ201907120005", "018336c216ea1471dad8883fe6d21a5e32bfbcbaa9d9b89265e8505b883cbcc9"),
        ("jsc2021e013603_alt", "e1b33f188690bf98c84f680cf18ece32538e3061f483baf89496d964e47efb42"),
        ("jsc2021e013795_alt", "ac40f4b5597e0aab69026129b8eedebff55a47ff2980ace131b9289ee8926cb4"),
    ],
    "glover": [
        ("NHQ202009150002", "3a36a61de3c9000c76c4c85cc5abca3b06468830834ae8b78f6ee349a49a6ed9"),
        ("KSC-20200814-PH-SPX01_0005", "1cb0a42a09c7201e0e6d2aec3cf23ed391b68b238bb1b5f39620fbeb899be008"),
        ("jsc2019e021787", "9da79daac6c3179db956037b4e817441fae2c24b0c6cfd4b2bb6355fffc3c43b"),
        ("iss064e044145", "660622ef19dce2772922f89dca7243517ed6aed4f7be2c6d1db904bb6ca66ed5"),
    ],
    "delaney": [
        ("jsc2026e034984_alt", "40699a827a122ab82ce2d7463e98a7baa4a50dc1ba5d1acd26586c58dbe11df5"),
        ("jsc2024e013665", "bb06f77935fc4ab3c17ec2323640e5a088572d50e5006918c6b22c1043d4a04e"),
        ("jsc2024e013664", "9d926a1a512a0839f4cfcc7cf749565ba58dda578249100988245bcc3b3750ba"),
        ("jsc2024e013684_alt", "582b75a016d63d1b84576c1bf2d8ca14d9fb190a0210ebf25a0b0f4748276b2d"),
        ("luke-delaney_51729219778_o", "9615d2bb81774a12517863c09f3a8b8bcef9ea0312c39cc1cf7d5b71d0f5519b"),
    ],
    "menon": [
        ("jsc2025e074501", "d1c12b3f2836a1148066bd416a5c432733b223fdf1614a7720a2fd1d0badb58b"),
        ("jsc2026e039165", "e16e11c1f1a4c938515b44aebc290e61544b856df59e3b75bd5dd62840e40cbc"),
        ("jsc2026e000758", "78331bba0a17b76e057650283825d30b83813be388cc272d6414d0be57db0f0b"),
        ("jsc2026e000750", "6fd6d4bf9388db52b67c979336cdfdaee8b12b1eeede424170f8ee972fed3fcc"),
        ("jsc2026e000701_alt", "f745d65ee5dfd78513fe54751f8b3a1c288dfdf0a047a0d9285e6fc2db9bb95f"),
    ],
    "barratt": [
        ("jsc2010e177742", "a872a2684cf55c8992addbe8c60ea5ac446282924c46b6bf061ae90dce18e8a4"),
        ("jsc2023e000249_alt", "8b391ecce5bcdf2eeb18da8b299200ff773a3cfb1c1d48679ea8645d280b758b"),
        ("jsc2023e000234_alt", "11072fc189fa35cddce90fb07a623262c220b080ea1ba1803234f9b56b0d9c50"),
    ],
    "watkins": [
        ("NHQ202009150003", "8d1c2bc32763b8429695457980be96cc46d1415cbc9f1192a6d15973fac0f1ae"),
        ("jsc2026e035015_alt", "b657558f221d6fe27035c2f6f2f01dd2c6f61bdbb51fc618fc4e999563fcfb76"),
        ("jsc2017e067121", "7a5e6303509778c2679fee3640eb6ca2d848a60661d12e41c167342a38915a2c"),
        ("jsc2021e037768_alt", "8564ca92418f2f6b1ecde657b37544e3f49426b45d21b2045c3e8195dd81ebc3"),
    ],
}


def _fetch(nasa_id: str, sha256: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(URL.format(id=nasa_id), headers={"User-Agent": "face-test-fetch"})
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed https host
            data = resp.read(8 * 1024 * 1024)
    except OSError:
        return False
    if hashlib.sha256(data).hexdigest() != sha256:
        return False
    dest.write_bytes(data)
    return True


def ensure_photos(cache: Path = CACHE) -> dict[str, list[Path]] | None:
    """``{person: [jpeg paths]}`` from the cache, downloading what is missing. None when anything could not be
    fetched or verified (offline): the caller should skip."""
    cache.mkdir(parents=True, exist_ok=True)
    out: dict[str, list[Path]] = {}
    for person, items in PHOTOS.items():
        for nasa_id, sha in items:
            path = cache / f"{person}__{nasa_id}.jpg"
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                path.unlink()
            if not path.is_file() and not _fetch(nasa_id, sha, path):
                return None
            out.setdefault(person, []).append(path)
    return out
