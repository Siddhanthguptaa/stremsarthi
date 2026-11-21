"""Build and load the guard eval set. Layout under the output directory::

    manifest.json              counts, seeds, per-clip truth, sha256 of every wav (first 16 hex), TTS engine
    music/library/lib_XX.wav   the demo library (5 synthetic songs; audio only ever lives here, never in git)
    music/other_XX.wav         songs NOT in the library (negatives for the fingerprint matcher)
    clips/*.wav                music-only, music under speech, speech-only, fingerprint snippets (5.0 s)
    profanity.json             written utterances with word timings and truth
    captions.json              shaping strings and the spoken timeline

Same seed gives the same manifest hash. Default sizes are small (about 25 MB, a few seconds of generation).
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from . import captions as cap
from . import clips as cl
from . import profanity as prof
from .songs import make_song

DEFAULT_SEED = 1234
N_LIBRARY = 5
LIB_SECONDS = 60.0
CLIP_SECONDS = 10.0
SNIPPET_SECONDS = 5.0


def _ids(prefix: str, n: int) -> list[str]:
    return [f"{prefix}{i:02d}" for i in range(n)]


def build_suite(out: Path, seed: int = DEFAULT_SEED, *, quick: bool = False) -> dict[str, Any]:
    """``quick`` shrinks every part (unit tests): 2 library songs of 30 s and about a quarter of the clips."""
    t0 = time.time()
    out = Path(out)
    rng = random.Random(seed)
    engine = cl.make_engine("auto", None)
    n_lib, lib_s = (2, 30.0) if quick else (N_LIBRARY, LIB_SECONDS)
    q = 4 if quick else 1
    lib = [make_song(i, lib_s) for i in range(n_lib)]
    other = [make_song(100 + i, 40.0) for i in range(max(2, 6 // q))]
    manifest: dict[str, Any] = {
        "seed": seed, "tts_engine": engine.name, "sample_rate": cl.SR, "library": [], "clips": [], "snippets": [],
        "mp3_available": True,
    }  # fmt: skip
    for i, x in enumerate(lib, start=1):
        h = cl.write_wav(out / "music" / "library" / f"lib_{i:02d}.wav", x)
        manifest["library"].append(
            {"id": f"demo_{i:02d}", "file": f"music/library/lib_{i:02d}.wav", "seconds": lib_s, "sha": h}
        )
    for i, x in enumerate(other):
        cl.write_wav(out / "music" / f"other_{i:02d}.wav", x)

    def add(cid: str, x: np.ndarray, **truth: Any) -> None:
        h = cl.write_wav(out / "clips" / f"{cid}.wav", x)
        manifest["clips"].append(
            {"id": cid, "file": f"clips/{cid}.wav", "sha": h, "seconds": round(len(x) / cl.SR, 2), **truth}
        )

    n = CLIP_SECONDS
    sp_seed = 0
    # 20 music-only clips: 10 excerpts of library songs, 10 of other songs
    for i in range(20 // q):
        song, track = (
            (lib[i % n_lib], f"demo_{i % n_lib + 1:02d}") if i % 2 == 0 else (other[i % len(other)], None)
        )
        off = rng.uniform(0, len(song) / cl.SR - n)
        x = song[int(off * cl.SR) : int((off + n) * cl.SR)]
        add(
            f"music_{i:02d}",
            x,
            kind="music_only",
            music=True,
            speech=False,
            music_start_s=0.0,
            track=track,
            offset_s=round(off, 2),
        )
    # 10 music under speech at 0 dB (speech minus music), music enters at 3.0 s
    for i in range(10 // q):
        sp = cl.speech(engine, 500 + sp_seed, n)
        sp_seed += 1
        song = other[i % len(other)]
        off = rng.uniform(0, len(song) / cl.SR - n)
        mix = cl.mix_speech_music(sp, song[int(off * cl.SR) : int((off + n) * cl.SR)], 0.0, 3.0)
        add(
            f"under_{i:02d}",
            mix,
            kind="music_under_speech",
            music=True,
            speech=True,
            music_start_s=3.0,
            snr_db=0.0,
            track=None,
        )
    # 20 speech-only clips: clean, pink noise 15 dB, white noise 20 dB
    for i in range(20 // q):
        sp = cl.speech(engine, 900 + i, n)
        noise = ("clean", "pink", "white")[i % 3]
        x = sp if noise == "clean" else cl.add_noise(sp, noise, 15.0 if noise == "pink" else 20.0, seed + i)
        add(
            f"speech_{i:02d}",
            x,
            kind="speech_only",
            music=False,
            speech=True,
            music_start_s=None,
            noise=noise,
            track=None,
        )
    # fingerprint snippets: library excerpts at 10 dB pink noise, half through MP3 128 kbps
    mp3_ok = True
    for i in range(40 // q):
        k = i % n_lib
        off = rng.uniform(0, lib_s - SNIPPET_SECONDS)
        x = lib[k][int(off * cl.SR) : int((off + SNIPPET_SECONDS) * cl.SR)]
        x = cl.add_noise(x, "pink", 10.0, seed * 7 + i)
        codec = "none"
        if i % 2:
            y = cl.mp3_roundtrip(x)
            if y is None:
                mp3_ok = False
            else:
                x, codec = y, "mp3_128k"
        cid = f"fp_{i:02d}"
        h = cl.write_wav(out / "clips" / f"{cid}.wav", x)
        manifest["snippets"].append(
            {"id": cid, "file": f"clips/{cid}.wav", "sha": h, "kind": "fp_positive", "track": f"demo_{k + 1:02d}", "offset_s": round(off, 2), "snr_db": 10.0, "codec": codec}
        )  # fmt: skip
    for i in range(10 // q):  # unrelated music, same treatment
        s = other[i % len(other)]
        off = rng.uniform(0, len(s) / cl.SR - SNIPPET_SECONDS)
        x = cl.add_noise(
            s[int(off * cl.SR) : int((off + SNIPPET_SECONDS) * cl.SR)], "pink", 10.0, seed * 11 + i
        )
        cid = f"fpneg_{i:02d}"
        h = cl.write_wav(out / "clips" / f"{cid}.wav", x)
        manifest["snippets"].append(
            {"id": cid, "file": f"clips/{cid}.wav", "sha": h, "kind": "fp_negative", "track": None}
        )
    for i in range(8 // q):  # 15 % faster library music: a documented non-match
        k = i % n_lib
        fast = cl.tempo_change(lib[k][: int(30 * cl.SR)], 1.15)[: int(SNIPPET_SECONDS * cl.SR)]
        cid = f"fptempo_{i:02d}"
        h = cl.write_wav(out / "clips" / f"{cid}.wav", fast)
        manifest["snippets"].append(
            {
                "id": cid,
                "file": f"clips/{cid}.wav",
                "sha": h,
                "kind": "fp_tempo",
                "track": f"demo_{k + 1:02d}",
            }
        )
    manifest["mp3_available"] = mp3_ok

    utts = prof.build_utterances(seed, scale=0.25 if quick else 1.0)
    (out / "profanity.json").write_text(
        json.dumps([asdict(u) for u in utts], ensure_ascii=False, indent=0), encoding="utf-8"
    )
    shaping = {"shaping": cap.SHAPING, "script_names": cap.SCRIPT_NAMES}
    (out / "captions.json").write_text(
        json.dumps(
            {**shaping, "timeline": cap.timeline(seed, 10 if quick else 24)}, ensure_ascii=False, indent=0
        ),
        encoding="utf-8",
    )
    manifest["totals"] = totals(manifest, utts)
    manifest["gen_seconds"] = round(time.time() - t0, 1)
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def totals(m: dict[str, Any], utts: list[prof.Utt]) -> dict[str, Any]:
    kinds: dict[str, int] = {}
    for c in m["clips"] + m["snippets"]:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    groups: dict[str, int] = {}
    for u in utts:
        groups[u.group] = groups.get(u.group, 0) + 1
    return {
        "library_tracks": len(m["library"]),
        "clips_by_kind": dict(sorted(kinds.items())),
        "utterances_by_group": dict(sorted(groups.items())),
    }


def manifest_hash(m: dict[str, Any]) -> str:
    keep = {k: v for k, v in m.items() if k not in ("gen_seconds",)}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load(out: Path) -> dict[str, Any]:
    return json.loads((Path(out) / "manifest.json").read_text(encoding="utf-8"))


def load_utterances(out: Path) -> list[prof.Utt]:
    raw = json.loads((Path(out) / "profanity.json").read_text(encoding="utf-8"))
    return [prof.Utt(**{**r, "words": [tuple(w) for w in r["words"]]}) for r in raw]


def load_captions(out: Path) -> dict[str, Any]:
    return json.loads((Path(out) / "captions.json").read_text(encoding="utf-8"))
