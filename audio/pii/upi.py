"""Spoken UPI ids (docs/specs/audio.md 5.6): ``<name> at <handle>`` in its spoken and spelled forms."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from audio.pii.merge import Cand, is_ambiguous
from audio.pii.numbers import Tok, Vocab
from contracts.enums import Category

NAME_MAX_TOKENS = 8
NAME_GAP_S = 0.6
HANDLE_GAP_S = 0.8
# Words that are also bank or app names: "meet me at sbi" is not a UPI id, so these need a name before "at".
SHARED_HANDLES = frozenset({"sbi", "upi", "paytm", "icici", "hdfc", "hdfcbank", "axisbank"})


def _match(toks: Sequence[Tok], i: int, phrases: tuple[tuple[str, ...], ...]) -> int:
    """Length of the longest phrase of ``phrases`` starting at ``i`` (0 if none)."""
    for ph in phrases:
        n = len(ph)
        if i + n <= len(toks) and all(toks[i + k].text == ph[k] for k in range(n)):
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
        if not t.isalnum():
            break
        cat += t
        if cat in vocab.handles:
            return k + 1, True
    return None


def find_upi(toks: Sequence[Tok], vocab: Vocab, cue_active_at: Callable[[float], bool]) -> list[Cand]:
    out: list[Cand] = []
    i = 0
    while i < len(toks):
        strong = _match(toks, i, vocab.at_strong)
        n_mark = strong or _match(toks, i, vocab.at_weak)
        if not n_mark:
            i += 1
            continue
        j = i + n_mark
        # name: up to 8 tokens before the marker, stopping at a pause or a filler word
        a = i
        while a > 0 and i - a < NAME_MAX_TOKENS:
            prev = toks[a - 1]
            if toks[a].t0 - prev.t1 >= NAME_GAP_S or prev.text in vocab.name_stop:
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
            if not strong and not cue and first_handle in SHARED_HANDLES and not has_name:
                i += 1
                continue
            conf = 0.9
        else:
            if not (strong or cue) or j >= len(toks):
                i += 1
                continue
            nxt = toks[j]
            if len(nxt.text) < 2 or not nxt.text.isalpha() or nxt.text in vocab.name_stop:
                i += 1
                continue
            if nxt.t0 - toks[j - 1].t1 >= HANDLE_GAP_S:
                i += 1
                continue
            last = j
            conf = 0.6
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
