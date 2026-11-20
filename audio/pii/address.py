"""Spoken address cues (docs/specs/audio.md 5.6): a cue covers the tokens that follow it."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from audio.pii.merge import Cand
from audio.pii.numbers import Tok, Vocab
from contracts.enums import Category

MAX_TOKENS = 12
PAUSE_S = 0.8
PRE_TOKENS = 2  # weak cues ("shastri nagar", "mg road") also cover the name before them
WEAK_CONTEXT_TOKENS = 12


def _phrase_at(toks: Sequence[Tok], i: int, phrases: tuple[tuple[str, ...], ...]) -> int:
    for ph in phrases:
        n = len(ph)
        if i + n <= len(toks) and all(toks[i + k].text == ph[k] for k in range(n)):
            return n
    return 0


def find_address(
    toks: Sequence[Tok],
    vocab: Vocab,
    cue_active_at: Callable[[float], bool],
    is_number: Callable[[str], bool],
) -> list[Cand]:
    """Address regions. ``address``/``pata`` open a region; strong cues (``flat number``, ``gali``) cover the
    following up to 12 tokens or until a pause of 0.8 s; weak cues (``road``, ``near``) need a watch window
    or another address cue / number nearby, so ordinary speech about roads is not muted."""
    n = len(toks)
    hits: list[tuple[int, int, str]] = []  # (start, end, kind)
    i = 0
    while i < n:
        k = _phrase_at(toks, i, vocab.cue_address_open)
        kind = "open"
        if not k:
            k, kind = _phrase_at(toks, i, vocab.addr_strong), "strong"
        if not k:
            k, kind = _phrase_at(toks, i, vocab.addr_weak), "weak"
        if k:
            hits.append((i, i + k - 1, kind))
            i += k
        else:
            i += 1
    out: list[Cand] = []
    for idx, (s, e, kind) in enumerate(hits):
        if kind == "weak":
            near = any(
                other is not hits[idx] and abs(other[0] - s) <= WEAK_CONTEXT_TOKENS for other in hits
            ) or any(is_number(t.text) for t in toks[max(0, s - 4) : min(n, e + 5)])
            if not (cue_active_at(toks[s].t0) or near):
                continue
        first = s
        if kind == "weak":
            while first > 0 and s - first < PRE_TOKENS and toks[first].t0 - toks[first - 1].t1 < PAUSE_S:
                first -= 1
        last = e
        while last + 1 < n and last - e < MAX_TOKENS and toks[last + 1].t0 - toks[last].t1 < PAUSE_S:
            last += 1
        cue = cue_active_at(toks[s].t0) or kind == "open"
        conf = min(1.0, 0.6 + (0.15 if cue else 0.0))
        span = toks[first : last + 1]
        out.append(
            Cand(
                Category.SPOKEN_ADDRESS,
                toks[first].t0,
                toks[last].t1,
                conf,
                "address_" + kind,
                any(t.approx for t in span),
                toks[first].widx0,
                toks[last].widx1,
                first,
                last,
                0,
                cue,
                False,  # address_mute decides: a cue-backed address is muted by rule, never sent to the LLM
            )
        )
    return out
