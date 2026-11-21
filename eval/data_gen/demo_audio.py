"""The audio track of the judge demo reel: commentary, two spoken SPECIMEN PII moments, a music bed, a mild curse.

    python -m eval.data_gen.demo_audio --out /tmp/streamsarthi-demo
    -> demo_reel.wav            48 kHz mono, 90 s, loops with the video (``sender --audio-file``)
    -> demo_reel_timeline.json  what is said when (for the demo script and the scoring of the demo run)

Speech is offline flite TTS through the same word composer the audio gate uses (``audio/tts_compose.py``), so every
word has an exact start and end. Flite has an English phonemiser only: the commentary is English (Hinglish through the
English phonemiser would be accepted, but plain English is the clearest on a demo laptop's speakers). The two PII
moments are SPECIMEN values that cannot belong to anyone: the phone number ``99999 12345`` (spoken as ten English
digits) and the UPI id ``demo.streamer.zz@ybl`` (spoken "demo dot streamer zz at ybl"). The music bed is 3 s of a
synthetic song from ``guard/music/synth.py`` (read only) that is in the demo fingerprint library (``demo_01``, seed 101),
so both the music detector and the fingerprint matcher can fire. The mild profanity sentence uses words from the
strike guard's lowest severity.

Segments are placed on the reel's scene schedule (``eval/sender/demo_scenes.py``): each starts at a fixed offset into
its scene, and is pushed later when the previous segment is still running, so nothing overlaps and the JSON holds the
times that really happened. With the ``tone`` engine (no ffmpeg flite) the same structure is produced from synthetic
bursts, which is only good for tests.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from ..sender.demo_scenes import DEMO_REEL_SECONDS, DEMO_UPI_ID, reel_schedule
from .audio.numbers import Tok
from .audio.tts_compose import SR, Composer, make_engine

OUT_SR = 48_000
DEFAULT_OUT = Path("/tmp/streamsarthi-demo")
WAV_NAME = "demo_reel.wav"
TIMELINE_NAME = "demo_reel_timeline.json"
DEMO_PHONE = "9999912345"
MUSIC_SEED = 101  # demo_01 of the demo fingerprint library (guard/music/build_library.py: seed 100 + index)
MUSIC_LIBRARY_SECONDS = 120.0
MUSIC_SLICE = (20.0, 23.0)  # seconds into that song
MUSIC_GAIN = 0.55
SPEECH_GAP = (0.07, 0.15)
DIGIT_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
_TTS_RESPELL = {
    "zz": "zee zee",
    "ybl": "why bee ell",
    "upi": "you pee eye",
    "otp": "oh tee pee",
    "pan": "pan",
}


@dataclass(frozen=True)
class Seg:
    """One thing to say: `after` is seconds into scene `scene` (the earliest start)."""

    kind: str  # speech | phone | upi | profanity
    scene: str
    after: float
    text: str  # what is said (words separated by spaces)
    value: str = ""  # the SPECIMEN value spoken, for PII segments
    groups: tuple[int, ...] = ()
    cue: str = ""  # PII segments: the ordinary words before the value (``text`` is then only the value)


SEGMENTS: tuple[Seg, ...] = (
    Seg(
        "speech",
        "demo_aadhaar_static",
        0.8,
        "hello friends welcome to the stream today we are testing the privacy shield",
    ),
    Seg(
        "speech",
        "demo_aadhaar_moving",
        0.8,
        "this sample aadhaar card is a specimen see how the shield hides it even when it moves",
    ),
    Seg("speech", "demo_pan", 0.8, "next a sample pan card also a specimen"),
    Seg("speech", "demo_upi_qr", 0.8, "this is a scan to pay code from a demo"),
    Seg("speech", "demo_plate", 0.8, "that is a car with a specimen number plate"),
    Seg("speech", "demo_signboard", 0.8, "and a hindi shop signboard"),
    Seg("speech", "demo_otp_phone", 0.5, "now a phone gets an otp message"),
    Seg(
        "phone",
        "demo_otp_phone",
        3.2,
        " ".join(DIGIT_WORDS[int(d)] for d in DEMO_PHONE),
        DEMO_PHONE,
        (5, 5),
        cue="my phone number is",
    ),  # fmt: skip
    Seg("speech", "demo_bgmi_lobby", 2.5, "in this game lobby the player id and the room id are hidden too"),
    Seg("speech", "demo_faces", 0.5, "here an enrolled face stays visible and a stranger is blurred"),
    Seg("upi", "demo_faces", 5.2, "demo dot streamer zz at ybl", DEMO_UPI_ID, cue="you can pay me at"),
    Seg("profanity", "demo_faces", 9.5, "oh damn that was a crap shot"),
    Seg("speech", "demo_calm", 7.0, "thanks for watching see you next time"),
)
MUSIC_AFTER = ("demo_calm", 2.5)  # the music bed starts 2.5 s into the calm scene
MUSIC_SECONDS = MUSIC_SLICE[1] - MUSIC_SLICE[0]


def _toks(text: str) -> list[Tok]:
    out = []
    for w in text.split():
        out.append(Tok(w, _TTS_RESPELL.get(w, w), w in DIGIT_WORDS))
    return out


def music_bed() -> np.ndarray:
    """3 s of ``demo_01`` (float32, 16 kHz) with 0.2 s fades; imported lazily because the synth is slow-ish."""
    from guard.music.synth import SR as SYNTH_SR  # noqa: PLC0415
    from guard.music.synth import synth_song  # noqa: PLC0415

    song = synth_song(MUSIC_SEED, MUSIC_LIBRARY_SECONDS)
    a, b = (int(s * SYNTH_SR) for s in MUSIC_SLICE)
    clip = song[a:b].astype(np.float32).copy()
    k = int(0.2 * SYNTH_SR)
    ramp = np.linspace(0.0, 1.0, k, dtype=np.float32)
    clip[:k] *= ramp
    clip[-k:] *= ramp[::-1]
    return np.asarray(MUSIC_GAIN * clip / max(1e-9, float(np.max(np.abs(clip)))), dtype=np.float32)


def build(
    engine_kind: str = "auto", voice: str | None = None, seed: int = 7, with_music: bool = True
) -> tuple[np.ndarray, dict[str, Any]]:
    """Return (float32 mono at 48 kHz, timeline dict)."""
    engine = make_engine(engine_kind)
    voice = voice or engine.voices[0]
    if voice not in engine.voices:
        raise ValueError(f"voice {voice!r} not in {engine.voices}")
    rng = random.Random(seed)
    schedule = {s["scene"]: s for s in reel_schedule()}
    engine.prewarm({(voice, t.tts) for seg in SEGMENTS for t in _toks(seg.cue + " " + seg.text)})
    total = int(DEMO_REEL_SECONDS * SR)
    mix = np.zeros(total + SR, dtype=np.float32)
    events: list[dict[str, Any]] = []
    cursor = 0.0  # end of the previous segment
    for i, seg in enumerate(SEGMENTS):
        comp = Composer(engine, voice, 1.0, rng)
        pii = seg.kind in ("phone", "upi")
        if seg.cue:
            comp.say(_toks(seg.cue), gap=SPEECH_GAP)
            comp.pause(0.14)
        comp.say(_toks(seg.text), kind=seg.kind if pii else None, gap=SPEECH_GAP,
                 group_gap=(0.22, 0.34), groups=seg.groups)  # fmt: skip
        pcm = comp.pcm(tail_s=0.0).astype(np.float32) / 32768.0
        t0 = max(schedule[seg.scene]["t0"] + seg.after, cursor + 0.25)
        n0 = int(round(t0 * SR))
        if n0 + len(pcm) > total:
            raise ValueError(f"segment {i} ends after the reel ({t0 + len(pcm) / SR:.1f} s)")
        mix[n0 : n0 + len(pcm)] += pcm
        cursor = t0 + len(pcm) / SR
        ev: dict[str, Any] = {
            "id": f"seg{i:02d}",
            "kind": seg.kind,
            "scene": seg.scene,
            "t0": round(t0, 3),
            "t1": round(cursor, 3),
            "text": (seg.cue + " " + seg.text).strip(),
            "pii": pii,
            "expect": {"phone": "mute", "upi": "mute", "profanity": "strike_guard_profanity"}.get(seg.kind, "none"),
        }  # fmt: skip
        if pii:
            ev["value"] = seg.value
            ev["pii_words"] = [  # the spoken value only (not the cue words), the span that must be muted
                {"w": w.text, "t0": round(t0 + w.t0, 3), "t1": round(t0 + w.t1, 3), "digit": w.digit}
                for w in comp.words
                if w.kind is not None
            ]
            ev["pii_t0"], ev["pii_t1"] = ev["pii_words"][0]["t0"], ev["pii_words"][-1]["t1"]
        events.append(ev)
    if with_music:
        scene, after = MUSIC_AFTER
        m0 = schedule[scene]["t0"] + after
        bed = music_bed()
        if any(e["t0"] < m0 + len(bed) / SR + 0.2 and m0 < e["t1"] + 0.2 for e in events):
            raise ValueError("music bed would overlap a spoken segment")
        n0 = int(round(m0 * SR))
        mix[n0 : n0 + len(bed)] += bed
        events.append(
            {
                "id": "music0", "kind": "music", "scene": scene, "t0": round(m0, 3),
                "t1": round(m0 + len(bed) / SR, 3), "text": f"synthetic song demo_01 (seed {MUSIC_SEED}), "
                f"{MUSIC_SLICE[0]:g}-{MUSIC_SLICE[1]:g} s", "pii": False, "expect": "music_guard_and_fingerprint",
            }
        )  # fmt: skip
    mix = mix[:total]
    peak = float(np.max(np.abs(mix)))
    if peak > 0.95:
        mix *= 0.95 / peak
    out = resample_poly(mix, OUT_SR // SR, 1).astype(np.float32)
    events.sort(key=lambda e: e["t0"])
    timeline = {
        "note": "SPECIMEN demo data only: the phone number and the UPI id belong to nobody.",
        "sample_rate": OUT_SR,
        "duration_s": round(len(out) / OUT_SR, 3),
        "engine": engine.name,
        "voice": voice,
        "scenes": list(schedule.values()),
        "events": events,
    }
    return out, timeline


def write(out_dir: Path, engine_kind: str = "auto", voice: str | None = None) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    audio, timeline = build(engine_kind, voice)
    wav = out_dir / WAV_NAME
    sf.write(str(wav), audio, OUT_SR, subtype="PCM_16")
    path = out_dir / TIMELINE_NAME
    path.write_text(json.dumps(timeline, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return wav, path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--engine", default="auto", choices=["auto", "flite", "tone"])
    ap.add_argument("--voice", default=None)
    args = ap.parse_args(argv)
    wav, path = write(args.out, args.engine, args.voice)
    print(f"wrote {wav}\nwrote {path}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
