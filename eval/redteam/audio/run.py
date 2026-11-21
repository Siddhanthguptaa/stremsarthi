"""Audio red team: spoken PII said in awkward ways, pushed through ``audio.AudioPipeline`` and the engine's mute book.

Each case is one TTS-composed clip with ground-truth word times (eval/data_gen/audio tooling). The clip goes through the
real pipeline in virtual time (Silero VAD, STT race, PII rules) exactly as ``eval.phase3`` does it, and the leak is read
from the OUTPUT audio: a PII word leaks when under 90 % of it is below -50 dBFS after the engine's ``MuteBook`` applied
the spans that were ready in time. The recogniser is the phase-3 ORACLE (answers from the truth words with seeded errors),
so these numbers measure VAD, segmentation, PII rules, merging, fail-safe and mute timing under attack, not recognition.
Attacks that a real recogniser would hurt (overlap, whisper, ASR drops) are modelled by hiding words from the oracle.

Categories: digits_fillers, code_mixed_numbers, letter_spelling, read_across_pauses, whisper_noisy, fast_speech,
overlap_speakers, background_music, split_segments, upi_phonetic, stt_failure, asr_drops.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ...data_gen.audio import noise, numbers
from ...data_gen.audio.numbers import Tok
from ...data_gen.audio.phrases import cue_tokens, toks
from ...data_gen.audio.truth import ClipTruth, SpanTruth
from ...data_gen.audio.tts_compose import SR, Composer, FliteEngine, make_engine
from ...data_gen.common import specimen
from ...data_gen.common.identifiers import otp_code
from ...harness import metrics
from ...harness.report import Check, Table
from ...phase3 import (
    DELAY_S,
    HOP_S,
    OUT_RATE,
    OracleStt,
    SttModel,
    output_audio,
    pts_cover,
    score_clip,
)
from ..common import EXIT_ENV, EXIT_OK, finish, fmt_rate, new_report, rate

SEED = 7771
DEFECTS = ("late_spans", "no_failsafe")  # reference-build defects of eval.phase3: the run must FAIL with each
CEILING = 0.10  # word-level leak ceiling (gate 3 asks 90 % coverage); failure-injection categories use the stricter one
CEILING_FAILSAFE = 0.05
FAILSAFE_CATEGORIES = {"stt_failure"}
LETTER_TTS = dict(
    zip(
        "abcdefghijklmnopqrstuvwxyz",
        "ay bee see dee ee eff jee aitch eye jay kay el em en oh pee cue are ess tee you vee doubleyou ex why zed".split(),
        strict=True,
    )
)
FOR_WORDS = {
    "z": "zebra",
    "s": "sugar",
    "p": "parrot",
    "e": "elephant",
    "c": "cat",
    "i": "india",
    "m": "mango",
    "n": "nose",
}
HANDLES = ("oksbi", "okaxis", "ybl", "paytm", "ibl")
FILLERS = ("um", "uh", "matlab", "haan", "wait", "like")
ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "x x twenty thirty forty fifty sixty seventy eighty ninety".split()


def en_number(n: int) -> str:
    if n < 20:
        return ONES[n]
    return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])


# --------------------------------------------------------------------------- case plumbing
@dataclass
class SttPlan:
    api: SttModel = SttModel(0.03, 0.02, (0.4, 1.0))
    local: SttModel = SttModel(0.10, 0.05, (0.8, 1.4))
    api_fail: tuple[float, float, str] | None = None  # (clip-time from, to, kind: 429 | 500 | hang | empty)
    local_fail: tuple[float, float, str] | None = None
    hidden: set[int] = field(default_factory=set)  # truth word indices no recogniser hears (overlap, whisper)
    all_down: bool = False


@dataclass
class Case:
    id: str
    category: str
    technique: str
    clip: ClipTruth
    pcm: np.ndarray
    plan: SttPlan


class Ctx:
    """One clip being composed."""

    def __init__(self, eng: Any, voice: str, speed: float, rng: random.Random, lang: str) -> None:
        self.eng, self.voice, self.speed, self.rng, self.lang = eng, voice, speed, rng, lang
        self.comp = Composer(eng, voice, speed, rng)
        self.spans: list[SpanTruth] = []
        self.extra_words: list[Any] = []
        self.comp.pause(rng.uniform(0.4, 0.7))

    def cue(self, kind: str, mode: str = "lexicon") -> None:
        c = cue_tokens(kind, self.lang if self.lang in ("hi_latn", "en") else "hi_latn", mode, self.rng)
        if c:
            self.comp.say(c)
            self.comp.pause(self.rng.uniform(0.1, 0.25))

    def say(self, toks_: list[Tok], kind: str | None, span: int | None, **kw: Any) -> tuple[int, int]:
        return self.comp.say(toks_, kind=kind, span=span, **kw)

    def span_of(self, kind: str, first: int, last: int, n_digits: int, value: str = "") -> int:
        w0, w1 = self.comp.words[first], self.comp.words[last]
        self.spans.append(SpanTruth(kind, w0.t0, w1.t1, first, last, "lexicon", n_digits, value))
        return len(self.spans) - 1

    def digits(self, value: str, kind: str, sid: int, **kw: Any) -> None:
        self.say(numbers.spell_digits(value, self.lang, self.rng, p_arabic=0.0), kind, sid, **kw)


def _finish_span(ctx: Ctx, kind: str, first_word: int, n_digits: int) -> None:
    """Register one span covering words ``first_word`` .. last, and retro-label them with the span id."""
    sid = len(ctx.spans)
    last = len(ctx.comp.words) - 1
    for w in ctx.comp.words[first_word : last + 1]:
        if w.kind == kind:
            w.span = sid
    ctx.span_of(kind, first_word, last, n_digits)


def _phone_clip(ctx: Ctx, body: Callable[[Ctx, str, int], None], kind: str = "phone") -> None:
    ctx.cue(kind)
    value = specimen.phone_number(ctx.rng) if kind == "phone" else otp_code(ctx.rng, 6)
    first = len(ctx.comp.words)
    body(ctx, value, len(ctx.spans))
    _finish_span(ctx, kind, first, len(value))


# ----- technique bodies: (ctx, value, span id) -> speaks ``value``
def b_fill_every2(ctx: Ctx, v: str, sid: int) -> None:
    for i in range(0, len(v), 2):
        ctx.digits(v[i : i + 2], "phone", sid, gap=ctx.comp.digit_gap)
        if i + 2 < len(v):
            ctx.comp.pause(0.08)
            ctx.say([Tok(f := ctx.rng.choice(FILLERS), f)], None, None)
            ctx.comp.pause(0.08)


def b_fill_random(ctx: Ctx, v: str, sid: int) -> None:
    for i, d in enumerate(v):
        ctx.digits(d, "phone", sid)
        if i + 1 < len(v) and ctx.rng.random() < 0.45:
            ctx.say([Tok(f := ctx.rng.choice(FILLERS), f)], None, None)


def b_restart(ctx: Ctx, v: str, sid: int) -> None:
    ctx.digits(v[:4], "phone", sid)
    ctx.say(toks("sorry matlab ek minute"), None, None)
    ctx.digits(v, "phone", sid)


def b_mixed_lang(ctx: Ctx, v: str, sid: int) -> None:
    for d in v:
        lang = ctx.rng.choice(("hi_latn", "en", "hi_deva"))
        ctx.say([numbers.digit_tok(d, lang, ctx.rng, 0.0)], "phone", sid)


def b_pairs_en(ctx: Ctx, v: str, sid: int) -> None:
    for i in range(0, len(v), 2):
        word = en_number(int(v[i : i + 2]))
        ctx.say([Tok(w, w, True) for w in word.split()], "phone", sid)


def b_double_triple(ctx: Ctx, v: str, sid: int) -> None:
    forced = "".join(c * ctx.rng.choice((2, 3)) for c in v[:4])[:10].ljust(10, v[-1])
    ctx.say(numbers.spell_digits(forced, ctx.lang, ctx.rng, p_repeat=1.0, p_arabic=0.0), "phone", sid)


def b_pause(gap: tuple[float, float], group: int) -> Callable[[Ctx, str, int], None]:
    def body(ctx: Ctx, v: str, sid: int) -> None:
        for i in range(0, len(v), group):
            if i:
                ctx.comp.pause(ctx.rng.uniform(*gap))
            ctx.digits(v[i : i + group], "phone", sid)

    return body


def b_plain(ctx: Ctx, v: str, sid: int) -> None:
    ctx.digits(v, "phone", sid)


def b_split_chatter(ctx: Ctx, v: str, sid: int) -> None:
    ctx.digits(v[:5], "phone", sid)
    ctx.comp.pause(1.3)
    ctx.say(toks("bhai ek second chat dekh leta hu"), None, None)
    ctx.comp.pause(1.3)
    ctx.digits(v[5:], "phone", sid)


def b_split_gap(gap: float) -> Callable[[Ctx, str, int], None]:
    def body(ctx: Ctx, v: str, sid: int) -> None:
        ctx.digits(v[:5], "phone", sid)
        ctx.comp.pause(gap)
        ctx.digits(v[5:], "phone", sid)

    return body


def _upi_local(ctx: Ctx) -> tuple[str, str]:
    letters = "".join(ctx.rng.choice("zspecimn") for _ in range(3))
    return f"zzspecimen{letters}{ctx.rng.randint(10, 99)}", ctx.rng.choice(HANDLES)


def _spell(word: str, ctx: Ctx, sid: int, kind: str = "upi", for_words: bool = False) -> None:
    for ch in word:
        if ch.isdigit():
            ctx.say([numbers.digit_tok(ch, "en", ctx.rng, 0.0)], kind, sid)
        elif for_words and ch in FOR_WORDS:
            ctx.say(
                [Tok(ch, LETTER_TTS[ch]), Tok("for", "for"), Tok(FOR_WORDS[ch], FOR_WORDS[ch])], kind, sid
            )
        else:
            ctx.say([Tok(ch, LETTER_TTS[ch])], kind, sid)


def upi_clip(style: str) -> Callable[[Ctx], None]:
    def build(ctx: Ctx) -> None:
        ctx.cue("upi")
        local, handle = _upi_local(ctx)
        first, sid = len(ctx.comp.words), len(ctx.spans)
        if style == "letters":
            _spell(local, ctx, sid)
            ctx.say([Tok("at", "at")], "upi", sid)
            _spell(handle, ctx, sid)
        elif style == "for_words":
            _spell(local[:10], ctx, sid, for_words=True)
            _spell(local[10:], ctx, sid)
            ctx.say(toks("at the rate"), "upi", sid)
            ctx.say([Tok(handle, handle)], "upi", sid)
        elif style == "handle_letters":
            ctx.say([Tok(local, "zed zed specimen")], "upi", sid)
            _spell(local[10:], ctx, sid)
            ctx.say([Tok("at", "at")], "upi", sid)
            ctx.say([Tok("ok", "oh kay")], "upi", sid)
            _spell(handle[2:], ctx, sid)
        elif style == "dot_underscore":
            ctx.say([Tok("zz", "zed zed"), Tok("dot", "dot"), Tok("specimen", "specimen")], "upi", sid)
            ctx.say([Tok("underscore", "underscore")], "upi", sid)
            _spell(local[10:], ctx, sid)
            ctx.say(toks("at"), "upi", sid)
            ctx.say([Tok(handle, handle)], "upi", sid)
        _finish_span(ctx, "upi", first, sum(c.isdigit() for c in local))

    return build


# --------------------------------------------------------------------------- post-processing of the mixed clip
def _scale_to_dbfs(x: np.ndarray, active_dbfs: float) -> np.ndarray:
    g = 10 ** (active_dbfs / 20.0) * 32768.0 / noise.active_rms(x)
    return np.rint(np.clip(x.astype(np.float64) * g, -32768, 32767)).astype(np.int16)


def post_noise(
    kind: str, snr: float, level: float | None = None
) -> Callable[[np.ndarray, Ctx, np.random.Generator], np.ndarray]:
    def f(x: np.ndarray, ctx: Ctx, nrng: np.random.Generator) -> np.ndarray:
        if level is not None:
            x = _scale_to_dbfs(x, level)
        n = len(x)
        bits = [ctx.eng.word(t, ctx.voice) for t in ("number", "match", "chat", "bhai", "stream")]
        gen = {
            "white": lambda: noise.white(n, nrng),
            "pink": lambda: noise.pink(n, nrng),
            "music": lambda: noise.music(n, nrng),
            "babble": lambda: noise.babble(n, bits, nrng),
        }[kind]()
        return noise.mix(x, gen, snr)

    return f


@dataclass
class Tech:
    category: str
    name: str
    build: Callable[[Ctx], None]
    speed: float = 1.1
    gap: tuple[float, float] = (0.05, 0.13)
    post: Callable[[np.ndarray, Ctx, np.random.Generator], np.ndarray] | None = None
    plan: Callable[[Ctx, ClipTruth, random.Random], SttPlan] | None = None
    overlap: str = ""  # '', 'chatter', 'digits', 'louder'


def phone(body: Callable[[Ctx, str, int], None], kind: str = "phone") -> Callable[[Ctx], None]:
    return lambda ctx: _phone_clip(ctx, body, kind)


def _first_span_window(clip: ClipTruth, half: float = 1.0) -> tuple[float, float]:
    s = clip.spans[0]
    mid = (s.t0 + s.t1) / 2
    return max(0.0, mid - half), mid + half


def p_fail(
    api: str | None, local: str | None, half: float = 1.2
) -> Callable[[Ctx, ClipTruth, random.Random], SttPlan]:
    def f(ctx: Ctx, clip: ClipTruth, rng: random.Random) -> SttPlan:
        a, b = _first_span_window(clip, half)
        return SttPlan(api_fail=(a, b, api) if api else None, local_fail=(a, b, local) if local else None)

    return f


def p_models(api: SttModel, local: SttModel) -> Callable[[Ctx, ClipTruth, random.Random], SttPlan]:
    return lambda ctx, clip, rng: SttPlan(api=api, local=local)


def p_down() -> Callable[[Ctx, ClipTruth, random.Random], SttPlan]:
    return lambda ctx, clip, rng: SttPlan(api_fail=(0, 1e9, "500"), local_fail=(0, 1e9, "500"), all_down=True)


def p_primary_down() -> Callable[[Ctx, ClipTruth, random.Random], SttPlan]:
    return lambda ctx, clip, rng: SttPlan(api_fail=(0, 1e9, "429"))


TECHNIQUES: list[Tech] = [
    Tech("digits_fillers", "fillers_every_2_digits", phone(b_fill_every2)),
    Tech("digits_fillers", "fillers_random", phone(b_fill_random)),
    Tech("digits_fillers", "restart_correction", phone(b_restart)),
    Tech("code_mixed_numbers", "mixed_language_digits", phone(b_mixed_lang)),
    Tech("code_mixed_numbers", "english_pair_words", phone(b_pairs_en)),
    Tech("code_mixed_numbers", "double_triple_heavy", phone(b_double_triple)),
    Tech("letter_spelling", "upi_letter_by_letter", upi_clip("letters")),
    Tech("letter_spelling", "upi_z_for_zebra", upi_clip("for_words")),
    Tech("read_across_pauses", "pair_gaps_1p5s", phone(b_pause((1.3, 1.7), 2))),
    Tech("read_across_pauses", "halves_gap_3s", phone(b_pause((2.8, 3.2), 5))),
    Tech("read_across_pauses", "every_digit_gap_0p9s", phone(b_pause((0.8, 1.0), 1))),
    Tech("whisper_noisy", "whisper_quiet", phone(b_plain), post=post_noise("pink", 12.0, -42.0)),
    Tech("whisper_noisy", "white_noise_0db", phone(b_plain), post=post_noise("white", 0.0)),
    Tech("whisper_noisy", "babble_0db", phone(b_plain), post=post_noise("babble", 0.0)),
    Tech("whisper_noisy", "far_field_quiet", phone(b_plain), post=post_noise("white", 18.0, -50.0)),
    Tech("fast_speech", "speed_1p6", phone(b_plain), speed=1.6, gap=(0.0, 0.02)),
    Tech("fast_speech", "speed_2p0", phone(b_plain), speed=2.0, gap=(0.0, 0.01)),
    Tech("fast_speech", "speed_2p4", phone(b_plain), speed=2.4, gap=(0.0, 0.005)),
    Tech("overlap_speakers", "overlap_chatter", phone(b_plain), overlap="chatter"),
    Tech("overlap_speakers", "overlap_other_digits", phone(b_plain), overlap="digits"),
    Tech("overlap_speakers", "overlap_louder_6db", phone(b_plain), overlap="louder"),
    Tech("background_music", "music_snr_8db", phone(b_plain), post=post_noise("music", 8.0)),
    Tech("background_music", "music_snr_0db", phone(b_plain), post=post_noise("music", 0.0)),
    Tech("background_music", "music_snr_minus6db", phone(b_plain), post=post_noise("music", -6.0)),
    Tech("split_segments", "split_gap_2p5s", phone(b_split_gap(2.5))),
    Tech("split_segments", "split_with_chatter", phone(b_split_chatter)),
    Tech("split_segments", "otp_halves_gap_4s", phone(b_split_gap(4.0), "otp")),
    Tech("upi_phonetic", "handle_spelled_ok_s_b_i", upi_clip("handle_letters")),
    Tech("upi_phonetic", "dot_underscore_at", upi_clip("dot_underscore")),
    Tech("stt_failure", "primary_429_mid_sentence", phone(b_plain), plan=p_fail("429", None)),
    Tech("stt_failure", "primary_hang_mid_sentence", phone(b_plain), plan=p_fail("hang", None)),
    Tech("stt_failure", "empty_transcripts_mid_sentence", phone(b_plain), plan=p_fail("empty", "empty")),
    Tech("stt_failure", "both_fail_mid_sentence", phone(b_plain), plan=p_fail("500", "429", 2.0)),
    Tech("stt_failure", "primary_down_whole_clip", phone(b_plain), plan=p_primary_down()),
    Tech("stt_failure", "both_down_whole_clip", phone(b_plain), plan=p_down()),
    Tech("asr_drops", "drop_10pct", phone(b_plain), plan=p_models(SttModel(0.10, 0.0), SttModel(0.10, 0.0))),
    Tech("asr_drops", "drop_25pct", phone(b_plain), plan=p_models(SttModel(0.25, 0.0), SttModel(0.25, 0.0))),
    Tech("asr_drops", "drop_50pct", phone(b_plain), plan=p_models(SttModel(0.50, 0.0), SttModel(0.50, 0.0))),
    Tech(
        "asr_drops", "mishear_30pct", phone(b_plain), plan=p_models(SttModel(0.0, 0.30), SttModel(0.0, 0.30))
    ),
]


def build_case(idx: int, t: Tech, rep: int, eng: Any, seed: int) -> Case:
    rng = random.Random(seed * 1000 + idx * 17 + rep)
    nrng = np.random.default_rng(seed + idx * 31 + rep)
    lang = ("hi_latn", "en")[rep % 2]
    voice = rng.choice(eng.voices)
    ctx = Ctx(eng, voice, t.speed, rng, lang)
    ctx.comp.digit_gap = t.gap
    t.build(ctx)
    ctx.comp.pause(rng.uniform(0.5, 0.9))
    pcm = ctx.comp.pcm(tail_s=1.0)
    words = list(ctx.comp.words)
    hidden: set[int] = set()
    if t.overlap:
        s = ctx.spans[0]
        other = [v for v in eng.voices if v != voice][0]
        comp_b = Composer(eng, other, 1.1, random.Random(rng.random()))
        comp_b.pause(s.t0 + (s.t1 - s.t0) * 0.35)
        if t.overlap == "digits":
            comp_b.say(
                numbers.spell_digits(specimen.phone_number(rng)[:6], "en", rng, p_arabic=0.0), hard_neg=True
            )
        else:
            comp_b.say(toks("bhai ye dekh kya zabardast spot hai yahan se push karte hain"), hard_neg=True)
        b = comp_b.pcm(tail_s=0.2).astype(np.float64)
        n = max(len(pcm), len(b))
        a = np.zeros(n)
        a[: len(pcm)] = pcm
        gain = 2.0 if t.overlap == "louder" else 1.0
        a[: len(b)] += b * gain
        pk = float(np.max(np.abs(a))) or 1.0
        pcm = np.rint(a * (30000.0 / pk) if pk > 30000.0 else a).astype(np.int16)
        words += comp_b.words
        words.sort(key=lambda w: w.t0)
        # a recogniser confuses the target words that the second speaker talks over (modelled: half of them are lost)
        hidden = {
            i
            for i, w in enumerate(words)
            if w.pii and any(o.t0 < w.t1 and o.t1 > w.t0 for o in comp_b.words) and rng.random() < 0.5
        }
        for sp in ctx.spans:  # word indices moved: re-point the span truth at the sorted list
            pii_ix = [i for i, w in enumerate(words) if w.span == 0 and w.pii]
            sp.first_word, sp.last_word = pii_ix[0], pii_ix[-1]
    if t.post is not None:
        pcm = t.post(pcm, ctx, nrng)
    clip = ClipTruth(
        clip_id=f"x{idx:02d}r{rep}", lang=lang, voice=voice, speed=t.speed, snr_db=None, noise="none", bucket="redteam",
        file="", duration_s=round(len(pcm) / SR, 4), pcm_sha256="", words=words, spans=ctx.spans, face_ranges=[],
    )  # fmt: skip
    plan = t.plan(ctx, clip, rng) if t.plan else SttPlan()
    plan.hidden = hidden or plan.hidden
    return Case(f"rt-{t.category}-{t.name}-{rep}", t.category, t.name, clip, pcm, plan)


# --------------------------------------------------------------------------- run one case through the real pipeline
class FlakyOracle(OracleStt):
    """The phase-3 oracle recogniser plus a failure window (by clip time of the segment start)."""

    def __init__(
        self,
        words: list[Any],
        provider: str,
        model: SttModel,
        seed: int,
        fail: tuple[float, float, str] | None,
    ) -> None:
        super().__init__(words, provider, model, seed, None)
        self.window = fail

    async def transcribe(self, pcm16k: Any, pts_start: float, lang_hint: str | None) -> Any:
        w = self.window
        end = pts_start + len(pcm16k) / 16_000
        if w is not None and pts_start < w[1] and end > w[0]:
            from audio.stt.base import SttProviderError, SttRateLimited  # noqa: PLC0415
            from contracts.speech import Transcript  # noqa: PLC0415

            await asyncio.sleep(self.rng.uniform(*self.model.latency))
            self.calls += 1
            if w[2] == "429":
                raise SttRateLimited("rate limited", 1.0)
            if w[2] == "500":
                raise SttProviderError("down", 500)
            if w[2] == "hang":
                await asyncio.sleep(3600.0)
            return Transcript(
                pts_start, end, "", (), "hi", self.provider, 0.5
            )  # "empty": ASR returned nothing
        return await super().transcribe(pcm16k, pts_start, lang_hint)


def run_pipeline_case(case: Case, seed: int) -> Any:
    from audio.orchestrator.pipeline import AudioPipeline  # noqa: PLC0415
    from audio.orchestrator.race import SttRace  # noqa: PLC0415
    from audio.pii.detector import SpokenPiiDetector  # noqa: PLC0415
    from audio.pii.rules import SpokenRules  # noqa: PLC0415
    from audio.segmenter import Segmenter  # noqa: PLC0415
    from audio.testing import feed_realtime, run_virtual  # noqa: PLC0415
    from audio.vad import EnergyFrameModel  # noqa: PLC0415
    from contracts.config import Config  # noqa: PLC0415
    from contracts.enums import Topic  # noqa: PLC0415
    from contracts.fakes import InProcessBus  # noqa: PLC0415

    from ...phase3 import ClipRun  # noqa: PLC0415

    cfg = Config(delay_seconds=DELAY_S)
    vad = "silero"
    try:
        seg = Segmenter.from_config(cfg)
    except Exception:  # missing Silero weights: the product falls back to the energy detector too
        seg, vad = Segmenter.from_config(cfg, EnergyFrameModel()), "energy"
    heard = [w for i, w in enumerate(case.clip.words) if i not in case.plan.hidden]
    state: dict[str, Any] = {}

    async def main(loop: Any) -> None:
        bus = InProcessBus()
        sar = FlakyOracle(heard, "sarvam", case.plan.api, seed, case.plan.api_fail)
        loc = FlakyOracle(heard, "local", case.plan.local, seed + 1, case.plan.local_fail)
        race = SttRace(
            {"sarvam": sar, "local": loc},
            order=("sarvam", "local"),
            hedge_s=cfg.audio.race_hedge_s,
            clock=loop.time,
        )  # type: ignore[dict-item]
        det = SpokenPiiDetector(
            SpokenRules(watch_window_s=cfg.audio.watch_window_s, address_mute=cfg.audio.address_mute)
        )
        pipe = AudioPipeline(cfg, bus, seg, race, det, clock=loop.time)
        t0 = loop.time()
        await feed_realtime(pipe, case.pcm, pts0=0.0)
        await asyncio.sleep(DELAY_S + 1.0)
        await pipe.drain()
        state.update(t0=t0, bus=bus, pipe=pipe)

    run_virtual(main)
    t0, bus, pipe = state["t0"], state["bus"], state["pipe"]
    spans = [
        {
            "id": e.span_id,
            "t0": e.pts_start,
            "t1": e.pts_end,
            "ready": e.completed_at - t0,
            "gran": e.granularity,
            "cat": e.category.value,
        }
        for tp, e in bus.history
        if tp is Topic.MUTE_SPAN
    ]
    return ClipRun(spans, [], {}, list(pipe.latencies), dict(pipe.stats), pipe.leaked_s, 0.0, vad)


def score_case(case: Case, seed: int, defect: str, reference: bool) -> dict[str, Any]:
    from ...phase3 import reference_run  # noqa: PLC0415

    if reference:
        run = reference_run(case.clip, "api_down" if case.plan.all_down else "api", defect)
    else:
        run = run_pipeline_case(case, seed)
    out, late = output_audio(case.pcm, run.spans)
    env = metrics.rms_dbfs_envelope(out, OUT_RATE, HOP_S)
    sc = score_clip(case.clip, env)
    words = leaked = digits = digits_leaked = 0
    reasons: Counter[str] = Counter()
    sp_leak: dict[int, bool] = {}
    for w, ok in sc["rows"]:
        if not w.pii:
            continue
        words += 1
        digits += int(w.digit)
        if w.span is not None:
            sp_leak.setdefault(w.span, False)
        if not ok:
            leaked += 1
            digits_leaked += int(w.digit)
            if w.span is not None:
                sp_leak[w.span] = True
            cov = pts_cover(run.spans, w.t0, w.t1)
            reasons[
                "no_span"
                if cov < 0.05
                else ("partial_span" if cov < metrics.MUTE_MIN_FRACTION else "late_span")
            ] += 1
    return {
        "id": case.id, "category": case.category, "technique": case.technique, "dur_s": case.clip.duration_s,
        "pii_words": words, "leaked_words": leaked, "digit_words": digits, "digit_leaked": digits_leaked,
        "spans": len(sp_leak), "spans_leaked": sum(sp_leak.values()), "reasons": dict(reasons),
        "over_mute": (sc["sec_muted"] / sc["sec_total"]) if sc["sec_total"] else None,
        "vad": run.vad, "late_spans": late, "failsafe_spans": run.stats.get("failsafe", 0), "n_spans": len(run.spans),
    }  # fmt: skip


# --------------------------------------------------------------------------- orchestration
def make_engine_cached(kind: str, wordcache: Path) -> Any:
    eng = make_engine(kind, None)
    if isinstance(eng, FliteEngine) and wordcache.exists():
        with np.load(wordcache, allow_pickle=False) as z:
            for key in z.files:
                voice, _, text = key.partition("|")
                eng.cache[(voice, text)] = z[key]
    if isinstance(eng, FliteEngine):
        eng.prewarm = lambda items: None  # type: ignore[method-assign]  # never write the shared cache
    return eng


def build_report_obj(rows: list[dict[str, Any]], phase: str, header: dict[str, Any]) -> Any:
    rep = new_report(phase, header)
    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_tech: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
        by_tech[(r["category"], r["technique"])].append(r)

    def agg(rs: list[dict[str, Any]]) -> dict[str, Any]:
        reasons: Counter[str] = Counter()
        for r in rs:
            reasons.update(r["reasons"])
        return rate(sum(r["leaked_words"] for r in rs), sum(r["pii_words"] for r in rs)) | {
            "clips": len(rs),
            "spans_leaked": sum(r["spans_leaked"] for r in rs),
            "spans": sum(r["spans"] for r in rs),
            "digits": rate(sum(r["digit_leaked"] for r in rs), sum(r["digit_words"] for r in rs)),
            "reasons": dict(reasons),
        }

    cat_rows = []
    for c, rs in by_cat.items():
        a = agg(rs)
        ceil = CEILING_FAILSAFE if c in FAILSAFE_CATEGORIES else CEILING
        cat_rows.append(
            [
                c,
                a["clips"],
                a["n"],
                a["k"],
                fmt_rate(a),
                f"{a['spans_leaked']}/{a['spans']}",
                f"<= {ceil:.0%}",
            ]
        )
        rep.checks.append(
            Check(
                f"miss rate: audio {c} (PII words leaked)",
                a["rate"],
                "<=",
                ceil,
                note=f"{a['k']}/{a['n']} words, {a['spans_leaked']}/{a['spans']} spans leaked",
            )
        )
    rep.tables.append(
        Table(
            "Leak rate by category (PII words not muted in the output audio)",
            ["category", "clips", "PII words", "leaked", "leak rate", "spans with a leak", "ceiling"],
            cat_rows,
        )
    )
    tech_rows = []
    leak_list = []
    for (c, t), rs in by_tech.items():
        a = agg(rs)
        tech_rows.append(
            [f"{c}/{t}", a["clips"], a["n"], a["k"], a["rate"], a["digits"]["rate"], json.dumps(a["reasons"])]
        )
        for r in rs:
            if r["leaked_words"]:
                leak_list.append(
                    [
                        r["id"],
                        f"{r['leaked_words']}/{r['pii_words']}",
                        json.dumps(r["reasons"]),
                        r["late_spans"],
                        r["failsafe_spans"],
                    ]
                )
    rep.tables.append(
        Table(
            "Leak rate by technique",
            [
                "category/technique",
                "clips",
                "PII words",
                "leaked",
                "word leak rate",
                "digit leak rate",
                "why (no_span: nothing muted; partial_span; late_span: ready after the samples left)",
            ],
            tech_rows,
        )
    )
    rep.tables.append(
        Table(
            f"Leaking clips ({len(leak_list)})",
            ["case id", "leaked/PII words", "why", "late spans", "failsafe spans"],
            leak_list,
        )
    )
    overs = [r["over_mute"] for r in rows if r["over_mute"] is not None]
    rep.tables.append(
        Table(
            "Context",
            ["metric", "value"],
            [
                ["clips", len(rows)],
                ["audio seconds", round(sum(r["dur_s"] for r in rows), 1)],
                ["mean over-mute of non-PII speech", round(float(np.mean(overs)), 3) if overs else None],
                ["VAD", sorted({r["vad"] for r in rows})],
            ],
        )
    )
    rep.data = {
        "rows": rows,
        "categories": {c: agg(rs) for c, rs in by_cat.items()},
        "techniques": {f"{c}/{t}": agg(rs) for (c, t), rs in by_tech.items()},
    }
    rep.notes += [
        "THE RECOGNISER IS THE PHASE-3 ORACLE: it answers from the truth words with seeded errors, so these numbers measure VAD, segmentation, PII rules, merging, the fail-safe and mute timing under attack, not recognition. Overlap, whisper and ASR-drop attacks are modelled by hiding words from the oracle.",
        "The offline TTS is English flite voices: Hindi and Hinglish are respelled; no native Indic phonetics. Compound Hindi number words and whispered or far-field speech are not real here (level and noise are applied to the English TTS).",
        "Leak = under 90 % of a PII word is below -50 dBFS in the 48 kHz output after engine.audio_mute.MuteBook applied the spans that were ready in time (delay 4.0 s). One leaked digit counts as one leaked word; the 'spans with a leak' column is the number-level view.",
        "Failure-injection windows are placed on the clip time of the first PII span; 'hang' never answers, '429' and '500' raise the provider errors, 'empty' returns a transcript with no words.",
    ]
    return rep


def run(
    *, per_technique: int, seed: int, engine: str, wordcache: Path, out: Path, report_dir: Path | None,
    categories: list[str] | None, techniques: list[str] | None, defect: str, reference: bool,
    record_only: bool, phase: str, show: str | None,
) -> int:  # fmt: skip
    try:
        eng = make_engine_cached(engine, wordcache)
    except (RuntimeError, OSError) as exc:
        print(f"ENV ERROR: no TTS engine ({exc})")
        return EXIT_ENV
    sel = [
        (i, t)
        for i, t in enumerate(TECHNIQUES)
        if (not categories or t.category in categories) and (not techniques or t.name in techniques)
    ]
    if show:
        for i, t in sel:
            for rep_i in range(per_technique):
                c = build_case(i, t, rep_i, eng, seed)
                if c.id == show:
                    print(
                        f"{c.id} voice={c.clip.voice} speed={c.clip.speed} dur={c.clip.duration_s}s spans={[(s.kind, round(s.t0, 2), round(s.t1, 2)) for s in c.clip.spans]} plan={c.plan}"
                    )
                    return EXIT_OK
        print(f"no case {show}")
        return EXIT_ENV
    t0 = time.time()
    rows: list[dict[str, Any]] = []
    try:
        for i, t in sel:
            for rep_i in range(per_technique):
                case = build_case(i, t, rep_i, eng, seed)
                rows.append(score_case(case, seed + i * 7 + rep_i, defect, reference))
                print(
                    f"  {case.id}: leaked {rows[-1]['leaked_words']}/{rows[-1]['pii_words']} PII words",
                    flush=True,
                )
    except ImportError as exc:
        print(f"ENV ERROR: cannot import the audio/engine modules ({exc.name})")
        return EXIT_ENV
    if not rows:
        print("ENV ERROR: no cases selected")
        return EXIT_ENV
    header = {
        "suite": "redteam-audio",
        "seed": seed,
        "per_technique": per_technique,
        "clips": len(rows),
        "tts_engine": eng.name,
        "reference": reference,
        "defect": defect,
        "wall_s": round(time.time() - t0, 1),
    }
    rep = build_report_obj(rows, phase, header)
    out.mkdir(parents=True, exist_ok=True)
    return finish(rep, report_dir, record_only)
