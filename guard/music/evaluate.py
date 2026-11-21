"""Acceptance numbers for T5.1 on SYNTHETIC audio (run: python -m guard.music.evaluate [--fallback]).

Music clips are seeded synthetic songs; speech clips are the TTS clips of ``eval/data/generated/audio``
(never real people). Nothing here says anything about accuracy on real commercial music.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from .detector import SR, MusicDetector, make_scorer
from .fingerprint import FingerprintMatcher, Library
from .library import load_library
from .service import MusicGuard
from .spectral import SpectralScorer
from .synth import synth_song

ROOT = Path(__file__).resolve().parents[2]
SPEECH_DIR = ROOT / "eval" / "data" / "generated" / "audio"


def speech_clips(n: int, seconds: float, offset: int = 0) -> list[np.ndarray]:
    import soundfile as sf

    manifest = json.loads((SPEECH_DIR / "manifest.json").read_text(encoding="utf-8"))
    ids = [c["clip_id"] for c in manifest["clips"] if c["noise"] != "music"]
    out = []
    for cid in ids[offset:]:
        x, _ = sf.read(str(SPEECH_DIR / "clips" / f"{cid}.flac"), dtype="float32")
        if len(x) >= seconds * SR + 2 * SR:
            out.append(x[2 * SR : int((2 + seconds) * SR)])
        if len(out) == n:
            break
    return out


def active_rms(x: np.ndarray) -> float:
    fr = x[: len(x) // 320 * 320].reshape(-1, 320)
    r = np.sqrt((fr.astype(np.float64) ** 2).mean(axis=1))
    return float(np.sqrt((r[r > r.max() * 0.0056] ** 2).mean()) + 1e-9)


def mix(speech: np.ndarray, music: np.ndarray, snr_db: float) -> np.ndarray:
    """Speech over music at ``snr_db`` (speech active level minus music level); 0 dB = equal loudness."""
    m = music[: len(speech)]
    g = active_rms(speech) / (np.sqrt(np.mean(m.astype(np.float64) ** 2)) * 10 ** (snr_db / 20))
    y = speech + g * m
    return (y / max(1.0, float(np.max(np.abs(y))))).astype(np.float32)


def pink(n: int, rng: np.random.Generator) -> np.ndarray:
    x = np.fft.rfft(rng.standard_normal(n))
    f = np.arange(len(x), dtype=float)
    f[0] = 1
    y = np.fft.irfft(x / np.sqrt(f), n)
    return (y / y.std()).astype(np.float32)


def add_noise(x: np.ndarray, snr_db: float, rng: np.random.Generator) -> np.ndarray:
    return (x + pink(len(x), rng) * np.sqrt(np.mean(x.astype(np.float64) ** 2)) / 10 ** (snr_db / 20)).astype(
        np.float32
    )


def mp3_128k(x: np.ndarray) -> np.ndarray:
    """Round trip through ffmpeg libmp3lame at 128 kbps; returns the input unchanged if ffmpeg is absent."""
    import soundfile as sf

    with tempfile.TemporaryDirectory() as d:
        wav, mp3, back = Path(d, "a.wav"), Path(d, "a.mp3"), Path(d, "b.wav")
        sf.write(str(wav), x, SR)
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(wav),
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "128k",
                    str(mp3),
                ],
                check=True,
            )
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-ar", str(SR), "-ac", "1", str(back)],
                check=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return x
        y, _ = sf.read(str(back), dtype="float32")
    return y[: len(x)]


def tempo_change(x: np.ndarray, factor: float) -> np.ndarray:
    """Play faster by ``factor`` (changes pitch too: a plain resample)."""
    idx = np.arange(0, len(x) - 1, factor)
    return np.interp(idx, np.arange(len(x)), x).astype(np.float32)


def _on_at_end(scorer: object, x: np.ndarray) -> bool:
    d = MusicDetector(scorer)
    on = False
    for i in range(0, len(x), SR):  # 1 s hops
        for r in d.feed(x[i : i + SR]):
            on = on or r.on
    return on


def eval_detection(scorer: object) -> dict[str, object]:
    speech = speech_clips(20, 12.0)
    speech2 = speech_clips(20, 12.0, offset=60)
    music = [synth_song(500 + i, 12.0) for i in range(20)]
    rec = float(np.mean([_on_at_end(scorer, m) for m in music]))
    fp = float(np.mean([_on_at_end(scorer, s) for s in speech]))
    mixes = {
        snr: float(
            np.mean([_on_at_end(scorer, mix(s, m, snr)) for s, m in zip(speech2, music, strict=False)])
        )
        for snr in (10, 0)
    }
    # latency: 4 s of speech, then music; time from the music start to the gate opening
    lats = []
    for i in range(10):
        x = np.concatenate([speech[i][: 4 * SR], music[i]])
        d = MusicDetector(scorer)
        for k in range(0, len(x), SR // 2):
            hit = [r for r in d.feed(x[k : k + SR // 2]) if r.on]
            if hit:
                lats.append(hit[0].t_end - 4.0)
                break
    return {
        "backend": getattr(scorer, "backend", "?"),
        "music_recall": rec,
        "speech_false_positive": fp,
        "music_under_speech_on_rate_by_speech_to_music_db": mixes,
        "on_latency_s_median": float(np.median(lats)) if lats else None,
        "on_latency_s_max": float(np.max(lats)) if lats else None,
        "n": {"music": len(music), "speech": len(speech)},
    }


def eval_fingerprint(
    library: Library, seconds: float = 5.0, backends: tuple[str, ...] = ("chromaprint", "landmark")
) -> dict[str, object]:
    rng = np.random.default_rng(7)
    songs = {f"demo_{i:02d}": synth_song(100 + i, 120.0) for i in range(1, 6)}
    matcher = FingerprintMatcher(library, backends=backends)
    ok = tot = 0
    wrong = 0
    ms = []
    for tid, s in songs.items():
        for _ in range(12):
            a = int(rng.integers(0, len(s) - int(seconds * SR) - 1))
            q = mp3_128k(add_noise(s[a : a + int(seconds * SR)], 10.0, rng))
            t = time.perf_counter()
            m = matcher.match(q)
            ms.append((time.perf_counter() - t) * 1000)
            tot += 1
            ok += m is not None and m.track_id == tid
            wrong += m is not None and m.track_id != tid
    unrelated = [add_noise(synth_song(700 + i, 8.0)[: int(seconds * SR)], 10.0, rng) for i in range(40)]
    unrelated += [s[: int(seconds * SR)] for s in speech_clips(20, seconds)]
    false = sum(matcher.match(q) is not None for q in unrelated)
    tempo = 0
    for tid, s in songs.items():
        q = tempo_change(s[10 * SR : 10 * SR + int(seconds * 1.15 * SR) + 10], 1.15)[: int(seconds * SR)]
        m = matcher.match(q)
        tempo += m is not None and m.track_id == tid
    return {
        "backends": list(backends),
        "query_s": seconds,
        "noisy_mp3_recall": ok / tot,
        "wrong_id": wrong,
        "false_match_unrelated": f"{false}/{len(unrelated)}",
        "match_ms_median": float(np.median(ms)),
        "match_ms_max": float(np.max(ms)),
        "tempo_plus_15pct_matches": f"{tempo}/5",
        "queries": tot,
    }


def eval_end_to_end(library: Library) -> dict[str, object]:
    """Speech for 4 s, then a library song: seconds from the music start to the music and fingerprint signals."""
    song = synth_song(103, 120.0)
    speech = speech_clips(1, 4.0)[0]
    x = np.concatenate([speech, song[30 * SR : 50 * SR]]).astype(np.float32)
    g = MusicGuard(library=library, wall_clock=lambda: "t")
    first: dict[str, float] = {}
    poll_ms = []
    for k in range(0, len(x), SR):
        g.push(x[k : k + SR])
        t = time.perf_counter()
        sigs = g.poll()
        poll_ms.append((time.perf_counter() - t) * 1000)
        for s in sigs:
            first.setdefault(s.label if s.source == "fingerprint" else s.source, float(s.pts) - 4.0)
    return {
        "first_signal_s_after_music_start": first,
        "poll_ms_per_1s_median": float(np.median(poll_ms)),
        "poll_ms_max": float(np.max(poll_ms)),
    }


def main() -> None:
    fallback = "--fallback" in sys.argv
    scorer = SpectralScorer() if fallback else make_scorer()
    library = load_library()
    report = {
        "detection": eval_detection(scorer),
        "fingerprint_5s": eval_fingerprint(library, 5.0),
        "fingerprint_8s": eval_fingerprint(library, 8.0),
        "fingerprint_landmark_only_5s": eval_fingerprint(library, 5.0, ("landmark",)),
        "end_to_end": eval_end_to_end(library),
        "synthetic": True,
    }
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
