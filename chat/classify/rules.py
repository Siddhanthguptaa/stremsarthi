"""Lexicon matcher for Hinglish, Hindi, regional-language abuse, threats and sexual harassment.

Lexicons are TSV files in ``lexicons/`` (``term<TAB>category<TAB>severity<TAB>lang<TAB>match``).
Terms are normalised with the same function as messages, so the lexicon can be written in plain form.

Scoring (docs/specs/chat.md 5.1 step 3): max severity weight (1 -> 0.5, 2 -> 0.75, 3 -> 0.95),
+0.1 per extra distinct hit (cap 1.0), +0.05 when the message is aimed at the streamer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .normalize import TOKEN_RE, Normalized, lexicon_view, squeeze

LEXICON_DIR = Path(__file__).parent / "lexicons"
ABUSE_FILES = (
    "abuse_hinglish_latin.tsv",
    "abuse_devanagari.tsv",
    "abuse_regional.tsv",
    "threat.tsv",
    "sexual_harassment.tsv",
)
BENIGN_FILE = "benign_allow.tsv"
_WEIGHT = {1: 0.5, 2: 0.75, 3: 0.95}
_KIND_ORDER = {"threat": 3, "sexual": 2, "abuse": 1}

_DIRECTED = re.compile(
    r"(?<![a-z0-9])(?:tu|tera|teri|tere|tujhe|tujhko|tumhe|tum|tumhara|tumhari|behen|you|your|ur|u|streamer)(?![a-z0-9])"
    r"|तू|तेरा|तेरी|तेरे|तुझे|तुम|तुम्हारी|तुम्हारा"
)


_MENTION = re.compile(r"(?<![\w.])@\w")


@dataclass(frozen=True, slots=True)
class LexEntry:
    term: str  # lexicon form
    kind: str  # abuse | threat | sexual
    severity: int
    lang: str
    match: str  # word | substr | regex


@dataclass(frozen=True, slots=True)
class RuleResult:
    score: float
    severity: int  # 0 when nothing matched
    kind: str  # abuse | threat | sexual | "" when nothing matched
    hits: int
    directed: bool

    @property
    def matched(self) -> bool:
        return self.hits > 0


NO_MATCH = RuleResult(0.0, 0, "", 0, False)


def parse_tsv(path: Path) -> list[LexEntry]:
    entries: list[LexEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) < 5:
            raise ValueError(f"{path.name}: expected 5 tab-separated columns")
        term, kind, sev, lang, match = (c.strip() for c in cols[:5])
        if match not in ("word", "substr", "regex"):
            raise ValueError(f"{path.name}: bad match type {match!r}")
        entries.append(LexEntry(term, kind, int(sev), lang, match))
    return entries


def _boundary(alternatives: Iterable[str]) -> re.Pattern[str] | None:
    alts = sorted(set(alternatives), key=len, reverse=True)
    if not alts:
        return None
    return re.compile("(?<![^ ])(?:" + "|".join(re.escape(a) for a in alts) + ")(?![^ ])")


class RuleMatcher:
    """Compiled lexicon. Build once, ``match`` per message (sub-millisecond for typical chat)."""

    def __init__(
        self,
        entries: Iterable[LexEntry] | None = None,
        benign: Iterable[LexEntry] | None = None,
    ) -> None:
        if entries is None:
            entries = [e for name in ABUSE_FILES for e in parse_tsv(LEXICON_DIR / name)]
        if benign is None:
            benign = parse_tsv(LEXICON_DIR / BENIGN_FILE)
        self._tokens: dict[str, LexEntry] = {}  # squeezed single token -> entry
        self._wild: dict[int, list[LexEntry]] = {}  # token length -> entries (for ``ch*tiya``)
        phrases: dict[str, LexEntry] = {}
        substrs: dict[str, LexEntry] = {}
        self._regexes: list[tuple[re.Pattern[str], LexEntry]] = []
        for e in entries:
            term = lexicon_view(e.term)
            norm_entry = LexEntry(term, e.kind, e.severity, e.lang, e.match)
            if e.match == "regex":
                self._regexes.append((re.compile(term), norm_entry))
            elif e.match == "substr":
                substrs[squeeze(term)] = _stronger(substrs.get(squeeze(term)), norm_entry)
                if len(term) >= 5 and term.isascii() and " " not in term:
                    self._wild.setdefault(len(term), []).append(norm_entry)
            else:
                toks = TOKEN_RE.findall(term)
                if len(toks) == 1:
                    key = squeeze(toks[0])
                    self._tokens[key] = _stronger(self._tokens.get(key), norm_entry)
                    if len(toks[0]) >= 4 and toks[0].isascii():
                        self._wild.setdefault(len(toks[0]), []).append(norm_entry)
                elif toks:
                    key = " ".join(squeeze(t) for t in toks)
                    phrases[key] = _stronger(phrases.get(key), norm_entry)
        self._phrases = phrases
        self._phrase_re = _boundary(phrases)
        self._substrs = substrs
        self._substr_re = (
            re.compile("|".join(re.escape(s) for s in sorted(substrs, key=len, reverse=True)))
            if substrs
            else None
        )
        benign_terms = [lexicon_view(b.term) for b in benign]
        self._benign_re = (
            re.compile(
                "(?<![^ ])(?:"
                + "|".join(re.escape(t) for t in sorted(benign_terms, key=len, reverse=True))
                + ")(?![^ ])"
            )
            if benign_terms
            else None
        )

    def _scan(self, view: str) -> dict[int, LexEntry]:
        """Return the distinct entries found in one text view (keyed by ``id`` of the entry)."""
        found: dict[int, LexEntry] = {}
        if not view:
            return found
        if self._benign_re is not None:
            view = self._benign_re.sub(" ", view)
        toks = TOKEN_RE.findall(view)
        if not toks:
            return found
        squeezed = [squeeze(t) for t in toks]
        for tok, sq in zip(toks, squeezed, strict=True):
            entry = self._tokens.get(sq)
            if entry is not None:
                found[id(entry)] = entry
            elif "*" in tok:
                hit = self._wildcard(tok)
                if hit is not None:
                    found[id(hit)] = hit
        joined = " ".join(squeezed)
        if self._phrase_re is not None and " " in joined:
            for m in self._phrase_re.finditer(joined):
                entry = self._phrases[m.group(0)]
                found[id(entry)] = entry
        if self._substr_re is not None:
            for m in self._substr_re.finditer(squeeze(view)):
                entry = self._substrs[m.group(0)]
                found[id(entry)] = entry
        for rx, entry in self._regexes:
            if rx.search(view):
                found[id(entry)] = entry
        return found

    def _wildcard(self, tok: str) -> LexEntry | None:
        fixed = sum(c != "*" for c in tok)
        if len(tok) < 4 or fixed < 3:
            return None
        pattern = re.compile("".join("." if c == "*" else re.escape(c) for c in tok))
        for e in self._wild.get(len(tok), ()):
            if pattern.fullmatch(e.term):
                return e
        return None

    def match(self, norm: Normalized) -> RuleResult:
        found = self._scan(norm.text)
        if norm.roman:
            found.update(self._scan(norm.roman))
        if not found:
            return NO_MATCH
        entries = list(found.values())
        top = max(entries, key=lambda e: (e.severity, _KIND_ORDER.get(e.kind, 0)))
        score = _WEIGHT[top.severity] + 0.1 * (len(entries) - 1)
        directed = _DIRECTED.search(norm.text) is not None or _MENTION.search(norm.raw) is not None
        if directed:
            score += 0.05
        return RuleResult(min(1.0, round(score, 4)), top.severity, top.kind, len(entries), directed)


def _stronger(old: LexEntry | None, new: LexEntry) -> LexEntry:
    return new if old is None or new.severity > old.severity else old


@lru_cache(maxsize=1)
def default_matcher() -> RuleMatcher:
    return RuleMatcher()
