"""Profanity detection on the transcript stream (T5.2, spec 5.3).

Privacy: transcript text lives only in a rolling RAM window of normalised tokens (about 6 s). Nothing
here logs, stores or emits text; signals carry codes only (``profanity:L2``, ``slur:L3``).
"""

from __future__ import annotations

import re
import unicodedata
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from contracts.enums import Category
from contracts.events import RiskSignal
from contracts.speech import Transcript, Word

from .lexicon_data import BENIGN_ALLOW, DEVANAGARI, ENGLISH, LATIN_HINGLISH, REGIONAL

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))
_LEET = str.maketrans(
    {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"}
)
_LEET_TRIGGER = re.compile(r"[0-9@$!*#]")
_LATIN_LETTER = re.compile(r"[a-z]")
_INNER_JUNK = re.compile(r"[.\-_'*#’]")
_REPEAT = re.compile(r"([a-z])\1+")
_DEVA_NASAL_STOP = re.compile("[नम]्(?=[कखगघचछजझटठडढतथदधपफबभ])")
_DEVA_MAP = str.maketrans({"ँ": "ं", "ी": "ि", "ू": "ु", "़": None})
_VOWELS = frozenset("aeiou")
# Spelling families of the Hindi family-abuse compounds, on already normalised tokens (lowercase,
# repeats squeezed, "oo" folded to "u"). Vowels and the silent "h" are optional, o/u are
# interchangeable. Whole token only, so "chod", "bahen", "bench" and "bhosale" never match.
_CH = "ch[ou]d[aeiu]?"
_FAMILY_RULES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        rf"^bh?(?:a|e|ai|ay)?h?[ea]?n{_CH}$",  # sister: bahenchod, behnchod, bhenchod, bhnchod, bhainchod
        rf"^m[aeu]?dh?[ae]?r{_CH}$",  # mother: madarchod, maderchod, madrchod, mdrchod, madharchod
        r"^bh?o[sz][ae]?[dr][iae](?:k[ae]?|wal[ae])?$",  # bhosdike, bhosadike, bhosdi, bhosri, bosdiwale
        r"^ch[ou]ti(?:y|ya)?(?:a|e|pa)$",  # chutiya, chutiye, chutia, chutiyapa, chotiya
    )
)
_LEAVE_WORDS = frozenset({"do", "de", "dena", "diya", "dijiye", "dun", "dunga", "kar", "karo"})
# kept at the token edge because leetspeak may start a word
_LEAD_KEEP = frozenset("@$#*")
_TRAIL_KEEP = frozenset("*")

_SEVERITY_SIGNAL = {
    1: ("profanity", 0.3, 30.0, "profanity:L1"),
    2: ("profanity", 0.6, 30.0, "profanity:L2"),
    3: ("slur", 0.9, 120.0, "slur:L3"),
}

WINDOW_S = 6.0  # RAM window that lets a phrase split across STT segments still match
DUP_TOL_S = 0.45  # same token this close in time is the overlap re-emission of one word
MIN_GAP_S = 2.0  # spec 5.3: one signal per severity per 2 s


def _is_edge_junk(ch: str, keep: frozenset[str]) -> bool:
    if ch in keep:
        return False
    return ch.isspace() or unicodedata.category(ch)[0] in "PS"


def normalize_token(text: str) -> str:
    """Lowercase, NFKC, strip edges, undo leetspeak and elision tricks, squeeze repeated letters.

    Latin and Devanagari get script-specific folding; other scripts only get NFKC. Returns "" for
    tokens with nothing left (pure punctuation)."""
    t = unicodedata.normalize("NFKC", text).casefold().translate(_ZERO_WIDTH)
    i, j = 0, len(t)
    while i < j and _is_edge_junk(t[i], _LEAD_KEEP):
        i += 1
    while j > i and _is_edge_junk(t[j - 1], _TRAIL_KEEP):
        j -= 1
    t = t[i:j]
    if not t:
        return ""
    if any("ऀ" <= ch <= "ॿ" for ch in t):
        t = _DEVA_NASAL_STOP.sub("ं", t.translate(_DEVA_MAP))
        return t
    if _LATIN_LETTER.search(t):
        if len(_LATIN_LETTER.findall(t)) >= 2 and _LEET_TRIGGER.search(t):
            t = t.translate(_LEET)  # "*" and "#" are not mapped; they are dropped below
        return _REPEAT.sub(r"\1", _INNER_JUNK.sub("", t).replace("oo", "u"))
    return t


def _skeleton(key: str) -> str:
    return "".join(ch for ch in key if ch not in _VOWELS)


@dataclass(frozen=True, slots=True)
class Hit:
    start: int  # token indexes, inclusive
    end: int
    severity: int
    entry_id: int  # index into the lexicon, never the text


class ProfanityMatcher:
    """Whole-token and token n-gram (up to 3) matching with a benign allow list."""

    def __init__(
        self,
        entries: Sequence[tuple[str, int]] | None = None,
        benign: Sequence[str] | None = None,
    ) -> None:
        if entries is None:
            entries = (*LATIN_HINGLISH, *DEVANAGARI, *ENGLISH, *REGIONAL)
        self._phrases: dict[str, tuple[int, int]] = {}
        self._joined: dict[str, tuple[int, int]] = {}
        self._skeleton: dict[str, tuple[int, int]] = {}
        for eid, (term, sev) in enumerate(entries):
            words = [normalize_token(w) for w in term.split()]
            key = " ".join(w for w in words if w)
            if not key:
                continue
            self._put(self._phrases, key, sev, eid)
            if " " in key:
                continue
            latin = bool(_LATIN_LETTER.search(key)) and all(ch.isascii() for ch in key)
            if len(key) >= (6 if latin else 5):
                self._put(self._joined, key, sev, eid)
            if latin:
                skel = _skeleton(key)
                if len(skel) >= 3 and (len(key) >= 5 or skel == key):
                    self._put(self._skeleton, skel, sev, eid)
        self._benign: set[str] = set()
        for phrase in BENIGN_ALLOW if benign is None else benign:
            norm = " ".join(w for w in (normalize_token(x) for x in phrase.split()) if w)
            if norm:
                self._benign.add(norm)
        self._max_n = 3

    @staticmethod
    def _put(table: dict[str, tuple[int, int]], key: str, sev: int, eid: int) -> None:
        old = table.get(key)
        if old is None or sev > old[0]:
            table[key] = (sev, eid)

    def _protected(self, toks: Sequence[str]) -> set[int]:
        out: set[int] = set()
        for n in range(1, self._max_n + 1):
            for i in range(len(toks) - n + 1):
                if " ".join(toks[i : i + n]) in self._benign:
                    out.update(range(i, i + n))
        return out

    def match(self, toks: Sequence[str]) -> list[Hit]:
        """``toks`` are already normalised. Longest match first, tokens are consumed once."""
        protected = self._protected(toks)
        hits: list[Hit] = []
        i = 0
        while i < len(toks):
            found: tuple[int, tuple[int, int]] | None = None
            for n in range(min(self._max_n, len(toks) - i), 0, -1):
                chunk = toks[i : i + n]
                hit = self._phrases.get(" ".join(chunk))
                if hit is None and n >= 2:
                    hit = self._joined.get("".join(chunk))
                if hit is None and n == 1:
                    tok = chunk[0]
                    if tok.isascii() and tok.isalpha() and not (_VOWELS & set(tok)):
                        hit = self._skeleton.get(tok)
                if hit is None and n <= 2 and chunk[0].isascii():
                    word = "".join(chunk)
                    if any(rule.match(word) for rule in _FAMILY_RULES):
                        leave = n == 2 and i + n < len(toks) and toks[i + n] in _LEAVE_WORDS
                        if not leave:  # "bahen chod do" is "sister, leave it"
                            hit = (2, -1)
                if hit is not None and not any(j in protected for j in range(i, i + n)):
                    found = (n, hit)
                    break
            if found is None:
                i += 1
                continue
            n, (sev, eid) = found
            hits.append(Hit(i, i + n - 1, sev, eid))
            i += n
        return hits


@dataclass(slots=True)
class _Tok:
    norm: str
    t0: float
    t1: float
    new: bool = True


def _utc_iso(_pts: float | None) -> str:
    return datetime.now(UTC).isoformat()


def words_from_transcript(tr: Transcript) -> list[tuple[str, float, float]]:
    """(text, t0, t1) per word. Falls back to the segment text with evenly spread times."""
    out: list[tuple[str, float, float]] = []
    for w in tr.words:
        parts = w.text.split()
        for k, part in enumerate(parts):
            span = (w.t1 - w.t0) / max(len(parts), 1)
            out.append((part, w.t0 + k * span, w.t0 + (k + 1) * span))
    if out or not tr.text.strip():
        return out
    parts = tr.text.split()
    span = (tr.pts_end - tr.pts_start) / max(len(parts), 1)
    return [(p, tr.pts_start + k * span, tr.pts_start + (k + 1) * span) for k, p in enumerate(parts)]


class ProfanityDetector:
    """Stateful stream adapter: feed transcripts or words, get ``RiskSignal``s.

    Deterministic: uses only the word timestamps (stream time), never the wall clock, except for the
    ``wall_ts`` string which comes from ``wall_ts_fn``."""

    def __init__(
        self,
        matcher: ProfanityMatcher | None = None,
        *,
        enabled: bool = True,
        wall_ts_fn: Callable[[float | None], str] = _utc_iso,
        min_gap_s: float = MIN_GAP_S,
    ) -> None:
        self.matcher = matcher or ProfanityMatcher()
        self.enabled = enabled
        self._wall = wall_ts_fn
        self._min_gap = min_gap_s
        self._window: deque[_Tok] = deque()
        self._last_emit: dict[int, float] = {}

    def feed_transcript(self, tr: Transcript) -> list[RiskSignal]:
        return self.feed(words_from_transcript(tr))

    def feed_words(self, words: Sequence[Word]) -> list[RiskSignal]:
        return self.feed([(w.text, w.t0, w.t1) for w in words])

    def feed(self, words: Sequence[tuple[str, float, float]]) -> list[RiskSignal]:
        if not self.enabled:
            return []
        added = False
        for text, t0, t1 in words:
            norm = normalize_token(text)
            if not norm:
                continue
            if any(e.norm == norm and abs(e.t0 - t0) <= DUP_TOL_S for e in self._window):
                continue  # overlap re-emission of a word already seen
            self._window.append(_Tok(norm, t0, t1))
            added = True
        if not added:
            return []
        newest = max(e.t1 for e in self._window)
        toks = sorted((e for e in self._window if e.t1 >= newest - WINDOW_S), key=lambda e: e.t0)
        self._window = deque(toks)
        hits = self.matcher.match([t.norm for t in toks])
        signals: list[RiskSignal] = []
        for hit in hits:
            if not toks[hit.end].new:
                continue  # already reported when its last word arrived
            t0 = toks[hit.start].t0
            last = self._last_emit.get(hit.severity)
            if last is not None and 0.0 <= t0 - last < self._min_gap:
                continue
            source, score, ttl, label = _SEVERITY_SIGNAL[hit.severity]
            self._last_emit[hit.severity] = t0
            signals.append(
                RiskSignal(
                    wall_ts=self._wall(t0),
                    pts=t0,
                    source=source,  # type: ignore[arg-type]
                    category=Category.PROFANITY,
                    score=score,
                    ttl_s=ttl,
                    label=label,
                )
            )
        for tok in toks:
            tok.new = False
        return signals

    def reset(self) -> None:
        self._window.clear()
        self._last_emit.clear()
