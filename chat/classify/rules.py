"""Lexicon matcher for Hinglish, Hindi, regional-language abuse, threats and sexual harassment.

Lexicons are TSV files in ``lexicons/`` (``term<TAB>category<TAB>severity<TAB>lang<TAB>match``).
Terms are normalised with the same function as messages, so the lexicon can be written in plain form.

Scoring (docs/specs/chat.md 5.1 step 3): max severity weight (1 -> 0.5, 2 -> 0.75, 3 -> 0.95),
+0.1 per extra distinct hit (cap 1.0), +0.05 when the message is aimed at the streamer.

Matching is phonetic for Latin words (``fold_token``: ``chootiya``, ``chutia`` and ``chutiya`` are one key; words
of three letters or fewer must match exactly), tolerant of in-word separators (``ch_akke``) and of ``*`` standing
for a letter, and runs on text with links removed.

An optional sixth TSV column marks words that are often harmless, so they count only in context:

* ``frame``   (``kutta``, ``saala``, ``kamina``): only when the message is aimed at someone (``tu``, ``tera``,
              ``abe``, ``@name``), is a comparison (``jaisa``, ``ki tarah``), or has another abusive hit;
* ``mention`` (``bc``, ``mc``, ``lund``, ``chutia``): not when the message is explaining the word or naming a
              place (``matlab``, ``full form``, ``university``, ``Ranchi``), nor for ``mc ne ...`` (a noun use);
* ``game``    (``i will destroy you``): violent wording that is only game talk when the message is about a game
              and has no real-life cue (``ghar``, ``family``, ``irl``, ``address``).

After the hits are known, two context checks can mark the result ``soft`` (see ``context.py``): ``reported``
(quoted, reported or discussed abuse) and ``banter`` (friendly teasing with mild words). The cascade does not
hard-flag a soft result.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .context import has_game_cue, is_banter, is_reported
from .normalize import TOKEN_RE, Normalized, fold_token, lexicon_view, squeeze

LEXICON_DIR = Path(__file__).parent / "lexicons"
ABUSE_FILES = (
    "abuse_hinglish_latin.tsv",
    "abuse_devanagari.tsv",
    "abuse_regional.tsv",
    "threat.tsv",
    "sexual_harassment.tsv",
    "identity.tsv",
)
BENIGN_FILE = "benign_allow.tsv"
_WEIGHT = {1: 0.5, 2: 0.75, 3: 0.95}
_KIND_ORDER = {"threat": 4, "identity": 3, "sexual": 2, "abuse": 1}

_DIRECTED = re.compile(
    r"(?<![a-z0-9])(?:tu|tera|teri|tere|tujhe|tujhko|tumhe|tum|tumhara|tumhari|behen|you|your|ur|u|streamer|"
    r"nuvvu|nuv|ninnu|nee|neenu|ninna|nin|nimma|neevu|nimge|unna|unnai|unaku|unga|ungalai|nee|tui|tor|tora|tomake|tomar)(?![a-z0-9])"
    r"|तू|तेरा|तेरी|तेरे|तुझे|तुम|तुम्हारी|तुम्हारा|నువ్వు|నీ|నిన్ను|ನೀನು|ನಿನ್ನ|ನಿನ್ನನ್ನು|ನೀವು|நீ|உன்|உன்னை|তুই|তোর|তোকে"
)


_MENTION = re.compile(r"(?<![\w.])@\w")
# "Aimed at someone": the directed words plus vocatives and comparisons.
# The message is about the speaker ("ullu ki tarah jaag raha hu"): a comparison is then not aimed at anyone.
_SELF_REF = re.compile(
    r"(?<![a-z0-9])(?:main|mai|hu|hun|hoon|mera|meri|mere|mujhe|maine|mujhko|i|my|me|myself)(?![a-z0-9])"
)
_VOCATIVE = re.compile(r"(?<![a-z0-9])(?:abe+|abey|oye|ae|hai\s+tu|ho\s+tum)(?![a-z0-9])|अबे|ओए")
_FRAME = re.compile(
    r"(?<![a-z0-9])(?:abe+|abey|oye|ae|hai\s+tu|ho\s+tum|jais[aei]|ki\s+tara[fh]|ki\s+trah)(?![a-z0-9])"
    r"|अबे|ओए|जैसा|जैसे|की तरह"
)
# Explaining a word, or naming a place: the ambiguous word is being mentioned, not used.
_NEUTRAL = re.compile(
    r"(?<![a-z0-9])(?:matlab|meaning|means|mean|stands?\s+for|full\s+form|kehte|kehlata|kehlati|kehlate|called|"
    r"abbreviation|university|college|sweden|city|town|district|ilaka|locality|ranchi|nagpur|assam|"
    r"kingdom|community|village|gaon|shahar)(?![a-z0-9])|मतलब|कहते"
)
# The dog words used as a predicate or a noun for a person ("... kutte hai", "kaisa kutta banda").
_FRAME_ADJ = re.compile(
    r"(?<![a-z0-9])(?:kut{1,2}[aeiy]+|kuttiy?a|kutiya)\s+(?:hai|hain|ho|banda|bandi|insaan|admin|player|streamer|noob)(?![a-z0-9])|"
    r"(?<![a-z0-9])(?:kaisa|kaisi|kitna|kitni)\s+(?:kut{1,2}[aeiy]+|saal[aei]|kamin[aei])(?![a-z0-9])|"
    r"(?<![a-z0-9])(?:kut{1,2}[aeiy]+|saal[aei]|kamin[aei])\s+(?:kahin|kahi|kahin\s+ka|kahi\s+ka)(?![a-z0-9])|"
    r"कहीं का"
)
# News and policy talk about sexual violence ("rape case", "law against ...", "victim"): the word is the topic.
_TOPIC_NEUTRAL = re.compile(
    r"(?<![a-z0-9])(?:cases?|victims?|survivors?|laws?|news|khabar|khilaf|against|accused|fir|court|verdict|justice|awareness|"
    r"incident|shocking|convicted|culture|statistics|reports?)(?![a-z0-9])|मामला|केस|खबर|कानून"
)
# A group-wide frame: lets an identity word count ("ye mulle log", "sab ... log").
_GROUP_MARK = re.compile(
    r"(?<![a-z0-9])(?:log|logon|logo|people|sab|saare|sabhi|sare|all|these|those|every|har|inn|ye\s+sab)(?![a-z0-9])"
)
# Cricket talk: "chakka" is a six.
_CRICKET = re.compile(
    r"(?<![a-z0-9])(?:cricket|match|ipl|over|overs|ball|balls|boundary|boundaries|six|sixes|four|fours|chauka|chauke|batsman|bowler|"
    r"innings|wicket|wickets|runs?|dhoni|kohli|rohit|sachin|t20|odi|test|kheli|maara|mara|maare|lagaya|lagayi|lagaye|laga|"
    r"maar|maarte|marte|chhudaye|chuda|barish|score|captain|pitch|stadium|world\s+cup)(?![a-z0-9])"
)
_NOUN_USE = re.compile(r"^(?:mc|bc)\s+(?:ne|ka|ki|ke|ko)(?![a-z0-9])")


def _vocative_or_comparison(text: str, directed: bool) -> bool:
    if _VOCATIVE.search(text) is not None:
        return True
    return _FRAME.search(text) is not None and (directed or _SELF_REF.search(text) is None)


@dataclass(frozen=True, slots=True)
class LexEntry:
    term: str  # lexicon form
    kind: str  # abuse | threat | sexual
    severity: int
    lang: str
    match: str  # word | substr | regex
    ctx: str = ""  # "" | frame | mention


@dataclass(frozen=True, slots=True)
class RuleResult:
    score: float
    severity: int  # 0 when nothing matched
    kind: str  # abuse | threat | sexual | identity | "" when nothing matched
    hits: int
    directed: bool
    soft: str = ""  # "" | reported | banter: the hit is quoted, reported or friendly (see context.py)

    @property
    def matched(self) -> bool:
        return self.hits > 0


NO_MATCH = RuleResult(0.0, 0, "", 0, False)
_GAME_ONLY = "game"


def parse_tsv(path: Path) -> list[LexEntry]:
    entries: list[LexEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) < 5:
            raise ValueError(f"{path.name}: expected 5 tab-separated columns")
        term, kind, sev, lang, match = (c.strip() for c in cols[:5])
        ctx = cols[5].strip() if len(cols) > 5 else ""
        if match not in ("word", "substr", "regex"):
            raise ValueError(f"{path.name}: bad match type {match!r}")
        if ctx not in ("", "frame", "mention", "game", "topic", "cricket"):
            raise ValueError(f"{path.name}: bad context {ctx!r}")
        entries.append(LexEntry(term, kind, int(sev), lang, match, ctx))
    return entries


def _boundary(alternatives: Iterable[str]) -> re.Pattern[str] | None:
    alts = sorted(set(alternatives), key=len, reverse=True)
    if not alts:
        return None
    return re.compile("(?<![^ ])(?:" + "|".join(re.escape(a) for a in alts) + ")(?![^ ])")


def _is_short(tok: str) -> bool:
    return tok.isascii() and len(tok) <= 4


def _key(tok: str) -> str:
    """Matching key of one token: exact for short ASCII words (4 letters or fewer: ``chut``, ``lund``, ``bc``),
    phonetic for longer ones, squeezed for Indic."""
    if not tok.isascii():
        return squeeze(tok)
    return tok if len(tok) <= 4 else fold_token(tok)


def _fold_nofinal(text: str) -> str:
    """``fold_token`` without the final-vowel merge (used for substring keys and views)."""
    return fold_token(text + "_")[:-1] if text.isascii() else squeeze(text)


def _sub_key(text: str) -> str:
    """Key for substring matching: phonetic, last vowel canonicalised to ``a``."""
    k = _fold_nofinal(text)
    return k[:-1] + "a" if k and k[-1] in "aeio" else k


_WILD_FILL = ("", "a", "e", "i", "o", "u", "h")


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
        self._tokens: dict[str, LexEntry] = {}  # phonetic key (words of 4+ letters) -> entry
        self._exact: dict[str, LexEntry] = {}  # words of up to 3 letters, matched as written
        phrases: dict[str, LexEntry] = {}
        substrs: dict[str, LexEntry] = {}
        self._regexes: list[tuple[re.Pattern[str], LexEntry]] = []
        for e in entries:
            if e.match == "regex":
                term = e.term.lower()
                self._regexes.append(
                    (re.compile(term), LexEntry(term, e.kind, e.severity, e.lang, e.match, e.ctx))
                )
                continue
            term = lexicon_view(e.term)
            norm_entry = LexEntry(term, e.kind, e.severity, e.lang, e.match, e.ctx)
            if e.match == "substr":
                key = _sub_key(term)
                substrs[key] = _stronger(substrs.get(key), norm_entry)
            else:
                toks = TOKEN_RE.findall(term)
                if len(toks) == 1:
                    table = self._exact if _is_short(toks[0]) else self._tokens
                    key = _key(toks[0])
                    table[key] = _stronger(table.get(key), norm_entry)
                elif toks:
                    key = " ".join(_key(t) for t in toks)
                    phrases[key] = _stronger(phrases.get(key), norm_entry)
        self._phrases = phrases
        # Words joined from single letters ("b.h.e.n.c.h.o.d tu"): the word boundary is lost, so look for the words
        # inside the run. Only runs that came from letter-by-letter spelling are searched, never ordinary words.
        blob_map: dict[str, LexEntry] = {}
        for k, e in {**self._tokens, **self._exact}.items():
            if len(k) >= 4 and k.isascii() and k.isalpha():
                blob_map[k[:-1] if k[-1] in "aeio" and len(k) >= 5 else k] = e
            elif len(k) >= 3 and not k.isascii():
                blob_map[k] = e
        self._blob_map = blob_map
        self._blob_re = (
            re.compile("|".join(re.escape(k) for k in sorted(blob_map, key=len, reverse=True)))
            if blob_map
            else None
        )
        self._phrase_re = _boundary(phrases)
        self._substrs = substrs
        self._substr_re = (
            re.compile(
                "|".join(
                    (re.escape(k[:-1]) + "[aeio]") if k.endswith("a") else re.escape(k)
                    for k in sorted(substrs, key=len, reverse=True)
                )
            )
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
        keys: list[str] = []
        for tok in toks:
            if "*" in tok:
                hit = self._wildcard(tok)
                keys.append(tok)
                if hit is not None:
                    found[id(hit)] = hit
                continue
            key = _key(tok)
            keys.append(key)
            if _is_short(tok):
                entry = self._exact.get(key) or self._exact.get(squeeze(tok))  # "mmc" is an elongated "mc"
            else:
                entry = self._tokens.get(key)
                if entry is None and len(key) == 4:  # "choot" folds to "chut"
                    entry = self._exact.get(key)
            if entry is not None:
                found[id(entry)] = entry
        # "bhos di", "chu tiya": a word cut in two by a space; two neighbours that make a listed word together
        for left, right in zip(toks, toks[1:], strict=False):
            if left.isascii() and right.isascii():
                ok = len(left) >= 3 and len(right) >= 3 and left.isalpha() and right.isalpha()
            else:  # Indic fragments are short: "गां डू"
                ok = len(left) >= 2 and len(right) >= 2 and not left.isascii() and not right.isascii()
            if ok:
                entry = self._tokens.get(_key(left + right))
                if entry is not None:
                    found[id(entry)] = entry
        joined = " ".join(keys)
        if self._phrase_re is not None and " " in joined:
            for m in self._phrase_re.finditer(joined):
                entry = self._phrases[m.group(0)]
                found[id(entry)] = entry
        if self._substr_re is not None:
            folded = [_fold_nofinal(t) if t.isascii() and "*" not in t else t for t in toks]
            # "madar chod": two neighbouring words written apart also count as one word
            pairs = [a + b for a, b in zip(folded, folded[1:], strict=False) if len(a) >= 3 and len(b) >= 3]
            sub = " ".join(folded + pairs)
            for m in self._substr_re.finditer(sub):
                hit = m.group(0)
                entry = self._substrs[hit[:-1] + "a" if hit[-1] in "aeio" else hit]
                found[id(entry)] = entry
        for rx, entry in self._regexes:
            if rx.search(view):
                found[id(entry)] = entry
        return found

    def _scan_blob(self, blob: str) -> dict[int, LexEntry]:
        found: dict[int, LexEntry] = {}
        if self._blob_re is None:
            return found
        for m in self._blob_re.finditer(_fold_nofinal(blob)):
            entry = self._blob_map[m.group(0)]
            found[id(entry)] = entry
        return found

    def _wildcard(self, tok: str) -> LexEntry | None:
        """``cho*tiya``: try the star as nothing or a vowel, then look the word up phonetically."""
        fixed = sum(c != "*" for c in tok)
        if len(tok) < 4 or fixed < 3:
            return None
        for fill in _WILD_FILL:
            cand = tok.replace("*", fill)
            if len(cand) <= 3:
                continue
            hit = self._tokens.get(_key(cand))
            if hit is not None:
                return hit
            if self._substr_re is not None:
                m = self._substr_re.search(_fold_nofinal(cand))
                if m is not None:
                    key = m.group(0)
                    return self._substrs[key[:-1] + "a" if key[-1] in "aeio" else key]
        return None

    def has_hit(self, lexicon_text: str) -> bool:
        """True when ``lexicon_text`` (already in lexicon form) contains an abusive word used as abuse."""
        found = self._scan(lexicon_text)
        if not found:
            return False
        directed = _DIRECTED.search(lexicon_text) is not None
        return bool(self._apply_context(list(found.values()), lexicon_text, directed))

    def match(self, norm: Normalized) -> RuleResult:
        found = self._scan(norm.text)
        if norm.glued:
            found.update(self._scan(norm.glued))
        if norm.roman:
            found.update(self._scan(norm.roman))
        for blob in norm.blobs:
            found.update(self._scan_blob(blob))
        if not found:
            return NO_MATCH
        directed = _DIRECTED.search(norm.text) is not None or _MENTION.search(norm.raw) is not None
        entries = self._apply_context(list(found.values()), norm.text, directed)
        if not entries:
            return NO_MATCH
        top = max(entries, key=lambda e: (e.severity, _KIND_ORDER.get(e.kind, 0)))
        score = _WEIGHT[top.severity] + 0.1 * (len(entries) - 1)
        if directed:
            score += 0.05
        soft = ""
        if is_reported(norm.text, norm.link_text, self):
            soft = "reported"
        elif is_banter(
            norm.text, norm.link_text, [(e.term, e.kind, e.severity, bool(e.ctx)) for e in entries], directed
        ):
            soft = "banter"
        return RuleResult(min(1.0, round(score, 4)), top.severity, top.kind, len(entries), directed, soft)

    @staticmethod
    def _apply_context(entries: list[LexEntry], text: str, directed: bool) -> list[LexEntry]:
        """Drop the context-dependent words that are used harmlessly here (see the module docstring)."""
        if any(e.ctx == "game" for e in entries):
            if has_game_cue(text):
                entries = [e for e in entries if e.ctx != "game"]
                if not entries:
                    return entries
            else:
                entries = [
                    LexEntry(e.term, e.kind, e.severity, e.lang, e.match, "") if e.ctx == "game" else e
                    for e in entries
                ]
        firm = [e for e in entries if not e.ctx]
        soft = [e for e in entries if e.ctx]
        if not soft:
            return entries
        neutral = _NEUTRAL.search(text) is not None or _NOUN_USE.search(text) is not None
        framed = (
            directed
            or _vocative_or_comparison(text, directed)
            or _FRAME_ADJ.search(text) is not None
            or len(TOKEN_RE.findall(text)) <= 2  # a bare shout: "kutte!!!"
        )
        n_frame = sum(1 for e in soft if e.ctx == "frame")
        group = _GROUP_MARK.search(text) is not None
        kept = list(firm)
        for e in soft:
            if e.ctx == "frame":
                if framed or firm or n_frame >= 2 or (group and e.kind == "identity"):
                    kept.append(e)
            elif e.ctx == "cricket":
                if _CRICKET.search(text) is None:
                    kept.append(e)
            elif e.ctx == "topic":
                if not neutral and _TOPIC_NEUTRAL.search(text) is None:
                    kept.append(e)
            elif not neutral:
                kept.append(e)
        return kept


def _stronger(old: LexEntry | None, new: LexEntry) -> LexEntry:
    return new if old is None or new.severity > old.severity else old


@lru_cache(maxsize=1)
def default_matcher() -> RuleMatcher:
    return RuleMatcher()
