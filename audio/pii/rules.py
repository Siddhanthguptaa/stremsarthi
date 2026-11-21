# Adapted from PrivaStream (Apache-2.0): src/models/audio/training/src/pii_detector.py
#   (deterministic PII layer: digit-run checks with the Luhn checksum, keyword-triggered capture).
# Changes: runs over timed spoken tokens (Hindi, English and regional number words) instead of text characters;
# categories, confidences and the watch window follow docs/specs/audio.md 5.6; no DeBERTa, no fake time estimate.
# The upstream licence text is in audio/pii/LICENSE-privastream-Apache-2.0.
"""Rules layer of the spoken-PII detector (docs/specs/audio.md 5.6). Deterministic and CPU-only.

Per window: tokens, number runs, cue words (the watch window), UPI ids and address regions become ``Cand``
spans; a number run is classified by its digit count. The result depends only on the window, so it can be
recomputed on every new segment (a number split across segments simply becomes one longer run).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from audio.pii.address import find_address
from audio.pii.merge import Cand, is_ambiguous, merge_candidates, passes_luhn
from audio.pii.numbers import (
    NumRun,
    Tok,
    Vocab,
    digit_value,
    id_runs,
    is_letter_token,
    load_vocab,
    number_runs,
    prepare,
)
from audio.pii.upi import find_upi
from contracts.enums import Category
from contracts.speech import TranscriptWindow

CUE_BONUS = 0.15
MOBILE_FIRST = "6789"
SPELL_GAP_S = 0.6


@dataclass(frozen=True, slots=True)
class Cue:
    t0: float
    t1: float


def find_cues(toks: Sequence[Tok], vocab: Vocab) -> list[Cue]:
    phrases = vocab.cue_generic + vocab.cue_address_open
    cues: list[Cue] = []
    i = 0
    while i < len(toks):
        hit = 0
        for ph in phrases:
            n = len(ph)
            if i + n <= len(toks) and all(toks[i + k].text == ph[k] for k in range(n)):
                hit = n
                break
        if hit:
            cues.append(Cue(toks[i].t0, toks[i + hit - 1].t1))
            i += hit
        else:
            i += 1
    return cues


class SpokenRules:
    def __init__(
        self,
        watch_window_s: float = 6.0,
        address_mute: bool = True,
        number_gap_s: float = 2.0,
        vocab: Vocab | None = None,
    ) -> None:
        self.watch_window_s = watch_window_s
        self.address_mute = address_mute
        self.number_gap_s = number_gap_s
        self.vocab = vocab or load_vocab()

    # ------------------------------------------------------------------ cues
    @staticmethod
    def cue_active(cues: Sequence[Cue], t: float, window_s: float) -> bool:
        """A cue ended no later than ``t`` and at most ``window_s`` before it."""
        return any(c.t1 <= t + 0.05 and t - c.t1 <= window_s for c in cues)

    # ------------------------------------------------------------------ numbers
    def classify_run(self, run: NumRun, cue: bool) -> tuple[Category, float, str, bool] | None:
        """(category, confidence, reason, provisional) for a number run, or ``None`` when it is not PII.

        ``provisional`` marks a guess from the shape of the speech alone: three or more digits spoken one by
        one (not counting up or down, not a quantity such as ``sau``). Such a run is muted as soon as it is
        heard, before the rest of the number can be: waiting for six digits would leak the first ones."""
        d = run.digits
        core = d
        mobile = MOBILE_FIRST + "?"
        if len(d) == 12 and d.startswith("91") and d[2] in mobile:
            core = d[2:]
        elif len(d) == 11 and d[0] == "0" and d[1] in mobile:
            core = d[1:]
        n = len(core)
        if n == 10 and core[0] in mobile:
            return Category.SPOKEN_PHONE, min(1.0, 0.9 + (CUE_BONUS if cue else 0.0)), "num_phone_10", False
        if n >= 10:
            conf = 0.8 + (CUE_BONUS if cue else 0.0) + (0.1 if passes_luhn(d) else 0.0)
            return Category.SPOKEN_OTHER_PII, min(1.0, conf), f"num_long_{min(n, 20)}", False
        if 6 <= n <= 9:  # PIN, OTP, short account number: mute with a cue, otherwise ask the LLM
            return (
                Category.SPOKEN_OTHER_PII,
                0.8 if cue else 0.5,
                "num_6_9_cue" if cue else "num_6_9",
                False,
            )
        if run.compound:
            return None  # "teen sau": a quantity
        if cue and n >= 4:
            return Category.SPOKEN_OTHER_PII, 0.6, "num_4_5_cue", False
        if run.singles >= 3 and not run.counting:
            return Category.SPOKEN_OTHER_PII, 0.5, "num_run", True
        if cue and run.singles >= 2 and not run.counting:
            return Category.SPOKEN_OTHER_PII, 0.55, "num_run_cue", True
        return None

    def number_cands(self, toks: Sequence[Tok], cues: Sequence[Cue]) -> list[Cand]:
        out: list[Cand] = []
        for run in number_runs(toks, self.number_gap_s, self.vocab):
            cue = self.cue_active(cues, run.t0, self.watch_window_s)
            res = self.classify_run(run, cue)
            if res is None:
                continue
            cat, conf, reason, prov = res
            span = toks[run.tok0 : run.tok1 + 1]
            out.append(
                Cand(
                    cat,
                    run.t0,
                    run.t1,
                    conf,
                    reason,
                    any(t.approx for t in span),
                    toks[run.tok0].widx0,
                    toks[run.tok1].widx1,
                    run.tok0,
                    run.tok1,
                    run.n,
                    cue,
                    is_ambiguous(conf)
                    and not prov,  # a shape guess is muted, not left to an LLM that sees a fragment
                    prov,
                )  # fmt: skip
            )
        return out

    # ------------------------------------------------------------------ spelt-out ids
    def spelled_cands(self, toks: Sequence[Tok], cues: Sequence[Cue]) -> list[Cand]:
        """Letters (and digits) spoken one by one: an id, an e-mail or a PAN being dictated. Muted as soon as
        it is heard; a following ``at <handle>`` makes it a UPI id (see ``find_upi``)."""
        v = self.vocab
        out: list[Cand] = []
        for r in id_runs(toks, SPELL_GAP_S, v):
            cue = self.cue_active(cues, toks[r.tok0].t0, self.watch_window_s)
            # three spoken letters in a row are a spelling; with a cue (upi, gpay, id ...) two are enough
            if not (r.letters >= 3 or (r.letters >= 2 and (cue or r.letters + r.digits >= 4))):
                continue
            first = r.tok0
            words = 0
            while (
                words < 2
                and first > 0
                and toks[first].t0 - toks[first - 1].t1 < SPELL_GAP_S
                and toks[first - 1].text.isalpha()
                and len(toks[first - 1].text) >= 2
                and toks[first - 1].text not in v.name_stop
                and not is_letter_token(toks[first - 1])
            ):
                first -= 1  # the name that precedes the spelt part ("rahul nine eight")
                words += 1
            span = toks[first : r.tok1 + 1]
            out.append(
                Cand(
                    Category.SPOKEN_OTHER_PII,
                    toks[first].t0,
                    toks[r.tok1].t1,
                    0.5,
                    "spelled_id",
                    any(t.approx for t in span),
                    toks[first].widx0,
                    toks[r.tok1].widx1,
                    first,
                    r.tok1,
                    r.digits,
                    cue,
                    False,  # muted outright: an LLM would see only a fragment of the id
                    True,
                )  # fmt: skip
            )
        return out

    # ------------------------------------------------------------------ window
    def analyse(self, window: TranscriptWindow) -> tuple[list[Tok], list[Cand]]:
        """Merged candidates touching the newest segment, and the token list they index into."""
        toks = prepare(window.words)
        if not toks:
            return toks, []
        v = self.vocab
        cues = find_cues(toks, v)

        def active(t: float) -> bool:
            return self.cue_active(cues, t, self.watch_window_s)

        def is_num(text: str) -> bool:
            return digit_value(text, v) is not None

        cands = self.number_cands(toks, cues)
        cands += self.spelled_cands(toks, cues)
        cands += find_upi(toks, v, active)
        if self.address_mute:
            cands += find_address(toks, v, active, is_num)
        merged = merge_candidates(cands)
        return toks, [c for c in merged if c.widx1 >= window.new_from_index]
