"""Build the spoken-PII audio suite: 150 clips, about 500 SPECIMEN PII spans, truth by construction.

``build_suite(out, seed)`` writes ``<out>/clips/<id>.wav`` (16 kHz mono int16) and ``<out>/manifest.json``::

    {"generator_version", "seed", "engine", "limitations": [...], "totals": {...},
     "clips": [ClipTruth.to_dict(), ...]}

Same ``(generator_version, seed, engine)`` gives the same manifest hash (``manifest_hash``), including the
sha256 of every clip's samples. Buckets: ``core`` (a cue phrase before most PII, clean to mildly noisy audio),
``hard`` (no cue, fast digit runs, 10-15 dB babble/music/pink noise), ``control`` (no PII at all: chatter and
number-bearing distractors, to measure over-muting).
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from ..common import specimen
from ..common.identifiers import otp_code
from ..common.seeds import derive_seed, py_rng
from . import GENERATOR_VERSION, noise, numbers, phrases
from .numbers import Tok
from .truth import ClipTruth, SpanTruth
from .tts_compose import SR, Composer, make_engine

DEFAULT_SEED = 1234
DEFAULT_N = 150
LANG_COUNTS = {"hi_latn": 36, "hi_deva": 30, "en": 18, "kn": 16, "ta": 16, "te": 16, "bn": 18}
KIND_WEIGHTS = {"phone": 0.30, "otp": 0.22, "upi": 0.18, "address": 0.17, "aadhaar": 0.13}
LIMITATIONS = [
    "Offline TTS is English-only (ffmpeg flite voices awb/kal16/rms/slt). Hindi, Hinglish and the four regional "
    "languages are spoken by an English phonemiser from respelled text: the timing and the number of words are "
    "exact, the pronunciation is not native. espeak-ng is not installed; Sarvam TTS needs a key.",
    "Words are synthesised one by one and concatenated: prosody is flat, coarticulation is absent.",
    "Ground truth is the placement of each synthesised word (no alignment step), so word times are exact.",
    "A recogniser reading this audio would mostly fail on the regional languages; the gate therefore drives the "
    "audio pipeline with an oracle recogniser built from the truth words (see eval/phase3.py).",
]


def _wavs_name(i: int) -> str:
    return f"a{i:03d}"


def vocabulary() -> set[str]:
    """Every string the generator can ask the TTS for (to synthesise them in parallel up front)."""
    v: set[str] = {"zed zed", "specimen", "at", "the", "rate", "of", "apartments"}
    for per_lang in numbers.DIGITS.values():
        for variants in per_lang:
            v.update(t for _, t in variants)
    for table in (numbers.REPEAT, numbers.TRIPLE):
        for variants in table.values():
            v.update(t for _, t in variants)
    v.update(numbers.LETTERS.values())
    v.update(numbers.HANDLE_TTS.values())
    for h in numbers.HANDLE_TTS.values():
        v.update(h.split())
    cue_sources = [phrases._PHONE, phrases._OTP, phrases._UPI, phrases._AADHAAR, phrases._ADDRESS]
    texts: list[str] = []
    for table in cue_sources:
        for lst in table.values():
            texts.extend(lst)
    for lst in phrases._PARAPHRASE.values():
        texts.extend(lst)
    texts += phrases.FILLER_LATIN + phrases.FILLER_DEVA + phrases.DISTRACTORS_LATIN + phrases.DISTRACTORS_DEVA
    for lst in phrases.FILLER_REGIONAL.values():
        texts.extend(lst)
    texts += phrases.BUILDINGS + phrases.LANDMARKS + phrases.CITIES
    for d in phrases.ADDRESS_WORDS.values():
        texts.extend(d.values())
    for s in texts:
        v.update(t.tts for t in phrases.toks(s))
    return {s for s in v if s}


# --------------------------------------------------------------------------- utterance builders
def _say_digits(
    comp: Composer, digits: str, lang: str, rng: random.Random, chunks: tuple[int, ...], kind: str, span: int
) -> tuple[int, int]:
    first = len(comp.words)
    pos = 0
    for k, n in enumerate(chunks):
        part = digits[pos : pos + n]
        pos += n
        if k:
            comp.pause(rng.uniform(0.14, 0.30))
        comp.say(numbers.spell_digits(part, lang, rng), kind=kind, span=span, gap=comp.digit_gap)
    return first, len(comp.words) - 1


def _address_tokens(lang: str, rng: random.Random) -> tuple[list[Tok], str]:
    words = phrases.ADDRESS_WORDS.get(lang, phrases.ADDRESS_WORDS["en"])
    toks: list[Tok] = []
    flat_no = str(rng.randint(101, 989))
    toks += phrases.toks(words["flat"])
    toks += numbers.spell_digits(flat_no, lang, rng, p_repeat=0.2)
    building = rng.choice(phrases.BUILDINGS)
    toks += phrases.toks(building + " apartments")
    value = f"{flat_no} {building}"
    if rng.random() < 0.7:
        sector = str(rng.randint(2, 48))
        toks += phrases.toks(words["sector"]) + numbers.spell_digits(sector, lang, rng, p_repeat=0.0)
        value += f" sector {sector}"
    if rng.random() < 0.7:
        landmark = rng.choice(phrases.LANDMARKS)
        toks += phrases.toks(words["near"]) + phrases.toks(landmark)
        value += f" near {landmark}"
    city = rng.choice(phrases.CITIES)
    toks += phrases.toks(city)
    pin = specimen.pin_code(rng)
    toks += phrases.toks(words["pin"]) + numbers.spell_digits(pin, lang, rng, p_repeat=0.2)
    return toks, f"{value} {city} {pin}"


def _pii_utterance(
    kind: str, lang: str, cue_mode: str, comp: Composer, rng: random.Random, spans: list[SpanTruth]
) -> None:
    cue = phrases.cue_tokens(kind, lang, cue_mode, rng)
    if cue:
        comp.say(cue, kind=None)
        comp.pause(rng.uniform(0.10, 0.28))
    sid = len(spans)
    n_digits = 0
    if kind == "phone":
        value = specimen.phone_number(rng)
        first, last = _say_digits(comp, value, lang, rng, (5, 5), "phone", sid)
        n_digits = 10
    elif kind == "otp":
        value = otp_code(rng, rng.choice((4, 6, 6, 6)))
        chunks = (3, 3) if len(value) == 6 and rng.random() < 0.4 else (len(value),)
        first, last = _say_digits(comp, value, lang, rng, chunks, "otp", sid)
        n_digits = len(value)
    elif kind == "aadhaar":
        value = specimen.aadhaar_number(rng)
        first, last = _say_digits(comp, value, lang, rng, (4, 4, 4), "aadhaar", sid)
        n_digits = 12
    elif kind == "upi":
        handle = rng.choice(specimen.UPI_HANDLES)
        value = (
            specimen.upi_id(rng, handle) if rng.random() < 0.7 else f"{specimen.phone_number(rng)}@{handle}"
        )
        toks = numbers.spoken_upi(value, lang, rng)
        first, last = comp.say(toks, kind="upi", span=sid)
        n_digits = sum(t.digit for t in toks)
    elif kind == "address":
        toks, value = _address_tokens(lang, rng)
        first, last = comp.say(toks, kind="address", span=sid, gap=(0.06, 0.15))
        n_digits = sum(t.digit for t in toks)
    else:  # pragma: no cover
        raise ValueError(kind)
    w0, w1 = comp.words[first], comp.words[last]
    spans.append(SpanTruth(kind, w0.t0, w1.t1, first, last, cue_mode, n_digits, value))


def _filler(lang: str, rng: random.Random) -> list[Tok]:
    if lang == "hi_deva":
        t = phrases.toks(rng.choice(phrases.FILLER_DEVA))
    elif lang in phrases.FILLER_REGIONAL and rng.random() < 0.35:
        t = phrases.toks(rng.choice(phrases.FILLER_REGIONAL[lang]))
    else:
        t = phrases.toks(rng.choice(phrases.FILLER_LATIN))
    return t[: rng.randint(3, 6)]  # short chatter keeps clips near 30 s


def _distractor(lang: str, rng: random.Random) -> list[Tok]:
    if lang == "hi_deva" and rng.random() < 0.5:
        return phrases.toks(rng.choice(phrases.DISTRACTORS_DEVA))
    return phrases.toks(rng.choice(phrases.DISTRACTORS_LATIN))


# --------------------------------------------------------------------------- one clip
def _noise_plan(bucket: str, rng: random.Random) -> tuple[str, float | None]:
    if bucket == "hard":
        return rng.choice(
            [
                ("babble", 10.0),
                ("babble", 15.0),
                ("music", 12.0),
                ("music", 18.0),
                ("pink", 10.0),
                ("white", 15.0),
            ]
        )
    r = rng.random()
    if r < 0.55:
        return "none", None
    if r < 0.72:
        return rng.choice([("white", 25.0), ("pink", 25.0), ("pink", 30.0)])
    if r < 0.90:
        return "babble", 20.0
    return "music", 22.0


def _face_ranges(duration: float, rng: random.Random) -> list[tuple[float, float]]:
    if rng.random() > 0.55:
        return []
    out: list[tuple[float, float]] = []
    target = rng.uniform(0.5, 0.9) * duration
    pos = rng.uniform(0.0, 0.1 * duration)
    k = rng.randint(1, 3)
    for _ in range(k):
        length = target / k
        a, b = pos, min(duration, pos + length)
        out.append((round(a, 3), round(b, 3)))
        pos = b + rng.uniform(0.2, 0.05 * duration + 0.3)
        if pos >= duration:
            break
    return out


def build_clip(
    i: int, lang: str, bucket: str, seed: int, engine: Any, out_dir: Path, bits: list[np.ndarray]
) -> ClipTruth:
    rng = py_rng(seed, "clip", i)
    nrng = np.random.default_rng(derive_seed(seed, "noise", i))
    voice = rng.choice(engine.voices)
    fast = bucket == "hard" and rng.random() < 0.45
    speed = rng.choice((1.25, 1.3)) if fast else rng.choice((1.0, 1.1, 1.15, 1.2))
    comp = Composer(engine, voice, speed, rng)
    comp.digit_gap = (0.015, 0.05) if fast else (0.05, 0.13)
    comp.pause(rng.uniform(0.4, 0.9))
    spans: list[SpanTruth] = []
    if bucket == "control":
        plan = ["filler", "distract", "filler", "distract", "filler"]
        plan = rng.sample(plan, len(plan)) + (["distract"] if rng.random() < 0.5 else [])
        pii_kinds: list[str] = []
    else:
        n = rng.choice((2, 3, 4, 4, 5))
        kinds = rng.choices(list(KIND_WEIGHTS), weights=list(KIND_WEIGHTS.values()), k=n)
        plan = ["filler"]
        for _ in kinds:
            plan += ["pii"]
            if rng.random() < 0.55:
                plan.append(rng.choice(["filler", "distract", "distract"]))
        pii_kinds = kinds
    cue_weights = (0.25, 0.35, 0.40) if bucket == "hard" else (0.80, 0.20, 0.0)
    modes = ("lexicon", "paraphrase", "none")
    for step in plan:
        if step == "pii":
            kind = pii_kinds.pop(0)
            mode = rng.choices(modes, weights=cue_weights)[0]
            _pii_utterance(kind, lang, mode, comp, rng, spans)
        elif step == "filler":
            comp.say(_filler(lang, rng))
        else:
            comp.say(_distractor(lang, rng), hard_neg=True)
        comp.pause(rng.uniform(0.4, 0.9))
    clean = comp.pcm(tail_s=1.0)
    kind_n, snr = _noise_plan(bucket, rng)
    if kind_n == "none":
        pcm = clean
    else:
        n = len(clean)
        gen = {
            "white": lambda: noise.white(n, nrng),
            "pink": lambda: noise.pink(n, nrng),
            "music": lambda: noise.music(n, nrng),
            "babble": lambda: noise.babble(n, bits, nrng),
        }[kind_n]
        pcm = noise.mix(clean, gen(), float(snr))  # type: ignore[arg-type]
    name = _wavs_name(i)
    (out_dir / "clips").mkdir(parents=True, exist_ok=True)
    sf.write(str(out_dir / "clips" / f"{name}.flac"), pcm, SR, subtype="PCM_16", format="FLAC")
    for sid, s in enumerate(spans):
        for w in comp.words[s.first_word : s.last_word + 1]:
            assert w.span == sid
    duration = len(pcm) / SR
    return ClipTruth(
        clip_id=name,
        lang=lang,
        voice=voice,
        speed=speed,
        snr_db=snr,
        noise=kind_n,
        bucket=bucket,
        file=f"clips/{name}.flac",
        duration_s=round(duration, 4),
        pcm_sha256=hashlib.sha256(pcm.tobytes()).hexdigest(),
        words=comp.words,
        spans=spans,
        face_ranges=_face_ranges(duration, rng),
    )


# --------------------------------------------------------------------------- the suite
def clip_plan(n: int) -> list[tuple[str, str]]:
    """(lang, bucket) per clip: language counts scaled to ``n``, then ~8 % control and ~25 % hard clips."""
    langs: list[str] = []
    total = sum(LANG_COUNTS.values())
    for lang, c in LANG_COUNTS.items():
        langs += [lang] * max(1, round(c * n / total))
    langs = (langs + list(LANG_COUNTS))[:n] if len(langs) < n else langs[:n]
    buckets = ["core"] * n
    n_control, n_hard = max(1, round(0.08 * n)), max(1, round(0.25 * n))
    order = list(range(n))
    random.Random(7).shuffle(order)
    for j in order[:n_control]:
        buckets[j] = "control"
    for j in order[n_control : n_control + n_hard]:
        buckets[j] = "hard"
    return list(zip(langs, buckets, strict=True))


def totals(clips: list[ClipTruth]) -> dict[str, Any]:
    kinds = Counter(s.kind for c in clips for s in c.spans)
    return {
        "clips": len(clips),
        "duration_s": round(sum(c.duration_s for c in clips), 2),
        "pii_spans": sum(kinds.values()),
        "pii_spans_by_kind": dict(sorted(kinds.items())),
        "pii_words": sum(w.pii for c in clips for w in c.words),
        "digit_words_in_pii": sum(w.pii and w.digit for c in clips for w in c.words),
        "clips_by_lang": dict(sorted(Counter(c.lang for c in clips).items())),
        "clips_by_bucket": dict(sorted(Counter(c.bucket for c in clips).items())),
        "spans_by_cue": dict(sorted(Counter(s.cue for c in clips for s in c.spans).items())),
        "clips_with_face": sum(bool(c.face_ranges) for c in clips),
    }


def manifest_hash(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def build_suite(
    out_dir: Path, seed: int = DEFAULT_SEED, n_clips: int = DEFAULT_N, engine_kind: str = "auto"
) -> dict[str, Any]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(engine_kind, cache_path=out_dir / "wordcache.npz" if engine_kind != "tone" else None)
    voices = engine.voices
    vocab = vocabulary()
    engine.prewarm([(v, t) for v in voices for t in sorted(vocab)])
    bits = [engine.word(t, voices[0]) for t in ("number", "match", "chat", "bhai", "stream", "subscribe")]
    clips = [
        build_clip(i, lang, bucket, seed, engine, out_dir, bits)
        for i, (lang, bucket) in enumerate(clip_plan(n_clips))
    ]
    manifest = {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "engine": engine.name,
        "voices": list(voices),
        "sample_rate": SR,
        "limitations": LIMITATIONS,
        "totals": totals(clips),
        "clips": [c.to_dict() for c in clips],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def load_manifest(out_dir: Path) -> tuple[dict[str, Any], list[ClipTruth]]:
    m = json.loads((Path(out_dir) / "manifest.json").read_text(encoding="utf-8"))
    return m, [ClipTruth.from_dict(c) for c in m["clips"]]
