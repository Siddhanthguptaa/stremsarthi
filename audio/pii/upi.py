"""Spoken UPI ids (docs/specs/audio.md 5.6): ``<name> at <handle>`` in its spoken and spelled forms."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Sequence

from audio.pii.merge import Cand, is_ambiguous
from audio.pii.numbers import Tok, Vocab, digit_value, is_letter_token
from contracts.enums import Category

NAME_MAX_TOKENS = 24  # a spelt-out id is long: letters and digits one by one
NAME_MAX_WORDS = 3  # ordinary words (the name itself) before the spelt part
NAME_GAP_S = 0.6
HANDLE_GAP_S = 0.8
# Words people say between the parts of an id ("rahul dot sharma"): part of the name, not a stop.
JOINERS = frozenset({"dot", "underscore", "dash", "hyphen", "point", "डॉट", "अंडरस्कोर", "डैश"})
# Words that are also bank or app names: "meet me at sbi" is not a UPI id, so these need a name before "at".
SHARED_HANDLES = frozenset(
    {"sbi", "upi", "paytm", "icici", "hdfc", "hdfcbank", "axisbank", "airtel", "jio", "kotak"}
    | {"एसबीआई", "पेटीएम", "आईसीआईसीआई", "एचडीएफसी", "एक्सिसबैंक", "एयरटेल", "जियो"}
)


def _marks_ok(text: str) -> bool:
    """Indic vowel signs are combining marks (``str.isalpha`` is False for them): count them as letters."""
    return bool(text) and all(ch.isalnum() or unicodedata.category(ch).startswith("M") for ch in text)


def _alpha(text: str) -> bool:
    return _marks_ok(text) and not text.isdigit()


def _match(toks: Sequence[Tok], i: int, phrases: tuple[tuple[str, ...], ...]) -> int:
    """Length of the longest phrase of ``phrases`` starting at ``i`` (0 if none)."""
    for ph in phrases:
        n = len(ph)
        if i + n <= len(toks) and all(ph[k] == "*" or toks[i + k].text == ph[k] for k in range(n)):
            if all(toks[i + k + 1].t0 - toks[i + k].t1 < HANDLE_GAP_S for k in range(n - 1)):
                return n
    return 0


def _handle(toks: Sequence[Tok], j: int, vocab: Vocab) -> tuple[int, bool] | None:
    """Known handle starting at token ``j`` as the concatenation of 1-4 tokens: (token count, known)."""
    cat = ""
    for k in range(4):
        if j + k >= len(toks):
            break
        if k and toks[j + k].t0 - toks[j + k - 1].t1 >= HANDLE_GAP_S:
            break
        t = toks[j + k].text
        if not _marks_ok(t):
            break
        cat += t
        if cat in vocab.handles:
            return k + 1, True
    return None


def _id_like(t: Tok, vocab: Vocab) -> bool:
    return is_letter_token(t) or t.text in JOINERS or digit_value(t.text, vocab) is not None


def find_upi(toks: Sequence[Tok], vocab: Vocab, cue_active_at: Callable[[float], bool]) -> list[Cand]:
    out: list[Cand] = []
    i = 0
    while i < len(toks):
        strong = _match(toks, i, vocab.at_strong)
        partial = 0 if strong else _match(toks, i, vocab.at_partial)
        fuzzy = 0 if strong or partial else _match(toks, i, vocab.at_fuzzy)
        n_mark = strong or partial or fuzzy or _match(toks, i, vocab.at_weak)
        if not n_mark:
            i += 1
            continue
        j = i + n_mark
        # name: letters and digits spoken one by one, plus up to three ordinary words, back to a pause or a stop word
        a, words, spelt = i, 0, 0
        while a > 0 and i - a < NAME_MAX_TOKENS:
            prev = toks[a - 1]
            if toks[a].t0 - prev.t1 >= NAME_GAP_S or prev.text in vocab.name_stop:
                break
            if _id_like(prev, vocab):
                spelt += 1
            elif words < NAME_MAX_WORDS and _alpha(prev.text):
                words += 1
            else:
                break
            a -= 1
        has_name = a < i
        cue = cue_active_at(toks[a].t0)
        h = _handle(toks, j, vocab) if j < len(toks) and toks[j].t0 - toks[j - 1].t1 < HANDLE_GAP_S else None
        known = h is not None
        last = j - 1
        if h is not None:
            last = j + h[0] - 1
            first_handle = toks[j].text
            if not strong and not partial and not cue and first_handle in SHARED_HANDLES and not spelt:
                i += 1
                continue
            if (partial or fuzzy) and not (cue or has_name):
                i += 1
                continue
            conf = 0.9
        else:
            if partial or (fuzzy and spelt < 2) or not (strong or fuzzy or cue) or j >= len(toks):
                i += 1
                continue
            if not (has_name or cue):
                i += 1
                continue
            nxt = toks[j]
            if len(nxt.text) < 2 or not _alpha(nxt.text) or nxt.text in vocab.name_stop:
                i += 1
                continue
            if nxt.t0 - toks[j - 1].t1 >= HANDLE_GAP_S:
                i += 1
                continue
            last = j
            conf = 0.75 if (strong or fuzzy) and has_name else 0.6
        if cue:
            conf = min(1.0, conf + 0.15)
        first = a if has_name else i
        span = toks[first : last + 1]
        out.append(
            Cand(
                Category.SPOKEN_UPI,
                toks[first].t0,
                toks[last].t1,
                conf,
                "upi_known_handle" if known else "upi_unknown_handle",
                any(t.approx for t in span),
                toks[first].widx0,
                toks[last].widx1,
                first,
                last,
                0,
                cue,
                is_ambiguous(conf),
            )
        )
        i = last + 1
    return out
