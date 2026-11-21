"""Build the demo fingerprint library.

    python -m guard.music.build_library --src eval/data/private/music --out guard/music/library/fingerprints.json
    python -m guard.music.build_library --synthetic 5 --out guard/music/library/fingerprints.json

``--src`` reads every audio file of a folder (sorted by name, ids demo_01, demo_02, ...). The audio
itself is used only to compute fingerprints; the output holds ids, durations and fingerprints.
``--synthetic N`` builds N seeded synthetic songs (no copyrighted audio). Same input gives the same JSON.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from .fingerprint import SR, Library, fingerprint_track
from .library import save_library
from .synth import synth_song

AUDIO_SUFFIXES = {".wav", ".flac", ".ogg", ".mp3", ".m4a", ".opus"}
SYNTH_SEED_BASE = 100
SYNTH_SECONDS = 120.0


def load_audio_16k(path: Path) -> np.ndarray:
    """Decode any audio file to 16 kHz mono float32 (soundfile first, PyAV for mp3/m4a)."""
    from scipy.signal import resample_poly

    try:
        import soundfile as sf

        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        x = data.mean(axis=1)
    except Exception:
        import av

        chunks = []
        with av.open(str(path)) as container:
            stream = container.streams.audio[0]
            sr = stream.rate
            for frame in container.decode(stream):
                a = frame.to_ndarray().astype(np.float32)
                if a.dtype.kind == "f":
                    pass
                chunks.append(
                    a.reshape(-1, frame.layout.nb_channels).mean(axis=1) if a.ndim == 1 else a.mean(axis=0)
                )
        x = np.concatenate(chunks) if chunks else np.zeros(0, np.float32)
    if sr != SR:
        g = np.gcd(int(sr), SR)
        x = resample_poly(x, SR // g, int(sr) // g).astype(np.float32)
    return x.astype(np.float32)


def build_from_dir(src: Path) -> Library:
    files = sorted(p for p in src.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES)
    return Library(
        [fingerprint_track(f"demo_{i:02d}", load_audio_16k(p)) for i, p in enumerate(files, start=1)]
    )


def build_synthetic(n: int, seconds: float = SYNTH_SECONDS) -> Library:
    return Library(
        [
            fingerprint_track(f"demo_{i:02d}", synth_song(SYNTH_SEED_BASE + i, seconds))
            for i in range(1, n + 1)
        ]
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, help="folder of demo song snippets (never committed)")
    ap.add_argument("--synthetic", type=int, default=0, help="build N seeded synthetic songs instead")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if bool(args.src) == bool(args.synthetic):
        ap.error("give exactly one of --src and --synthetic")
    library = build_synthetic(args.synthetic) if args.synthetic else build_from_dir(args.src)
    if not len(library):
        print("no audio found", file=sys.stderr)
        return 1
    save_library(library, args.out)
    print(f"wrote {len(library)} tracks to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
