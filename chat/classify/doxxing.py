"""Doxxing detection (docs/specs/chat.md 5.1 step 5).

Finds (1) the streamer's configured private terms and phone numbers, (2) Indian mobile numbers in
many spellings (spaced, dashed, ``+91``, Indic digits, spoken digits), (3) PIN codes next to an
address cue, (4) Verhoeff-valid 12-digit numbers (Aadhaar-like) and PAN numbers, (5) intent phrases.

Private terms and phones live in RAM only, are never logged and never appear in a ``repr``.
Findings carry codes only.
"""

from __future__ import annotations

import re
import unicodedata

from contracts import Category

from .candidate import Candidate
from .normalize import TOKEN_RE, Normalized, fold_token, lexicon_view

_INDIC_DIGITS = {
    ord(chr(base + i)): ord(str(i))
    for base in (0x966, 0x9E6, 0xA66, 0xAE6, 0xB66, 0xBE6, 0xC66, 0xCE6, 0xD66)
    for i in range(10)
}

_SPOKEN: dict[str, str] = {}
for _digit, _words in enumerate(
    [
        ("zero", "shunya", "sifar", "शून्य", "जीरो", "ज़ीरो"),
        ("one", "ek", "एक"),
        ("two", "do", "दो"),
        ("three", "teen", "तीन"),
        ("four", "char", "chaar", "चार"),
        ("five", "panch", "paanch", "पांच", "पाँच"),
        ("six", "chhe", "chhah", "chha", "cheh", "छह", "छः", "छे", "छ"),
        ("seven", "saat", "sat", "सात"),
        ("eight", "aath", "ath", "आठ"),
        ("nine", "nau", "nao", "नौ"),
    ]
):
    for _w in _words:
        _SPOKEN[_w] = str(_digit)
_MULTIPLIER = {"double": 2, "dabal": 2, "dubal": 2, "डबल": 2, "triple": 3, "ट्रिपल": 3}

_TOKEN = re.compile(r"\d+|[a-zऀ-ॿ]+|[^\s]")
_RUN_SEPS = frozenset("-.,()+")
_MOBILE = re.compile(r"(?:0{0,2}91|0)?[6-9]\d{9}")
_COUNTRY = frozenset({"91", "091", "0091", "0"})
_PIN = re.compile(r"(?<!\d)[1-9]\d{2}\s?\d{3}(?!\d)")
_ADDRESS_CUE = re.compile(
    r"house|flat|plot|h\.?\s?no|near|road|nagar|colony|gali|mohalla|sector|pin\s?(?:code)?|ghar|address|rehta|rehti|rahta|rahti|pata\b|"
    r"मकान|घर|पता|गली|मोहल्ला|कॉलोनी|नगर|रोड|पिन",
    re.IGNORECASE,
)
# 4th letter = holder type. ``X`` is the SPECIMEN marker (never a real holder type); other letters must be real.
_PAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{3}[PCHFATBLJGX][A-Z]\d{4}[A-Z](?![A-Za-z0-9])", re.IGNORECASE)
_INTENT = re.compile(
    r"(?<![a-z])dox{1,2}(?:ed|ing|er)?(?![a-z])|"
    r"leak\s+(?:his|her|their|uska|iska|uski|iski)\s+(?:home\s+)?(?:address|number|phone|location|pata)|"
    r"ghar\s+ka\s+(?:address|pata)|home\s+address|address\s+(?:leak|share|bhej|batao)|"
    r"(?:uska|iska|uski|iski)\s+(?:address|pata|number)\s+(?:do|batao|bhej|leak)|"
    r"घर\s+का\s+(?:पता|एड्रेस)|एड्रेस\s+लीक"
)
# Asking for somebody else's address, location or number: a third-person pronoun, an information noun
# and (nearby) a request or leak verb. Needs all three, so "my address" or "address bar" never match.
_THIRD_PERSON = (
    r"(?:uska|iska|uski|iski|uske|iske|inka|unka|unki|inki|his|her|their|is\s+(?:bande|bandi|ladke|ladki|streamer)\s+ka|"
    r"streamer\s+(?:ka|ki|ke)|उसका|इसका|उसकी|इसकी|उनका|इनका|स्ट्रीमर\s+का)"
)
_INFO_NOUN = (
    r"(?:(?:real|exact|ghar\s+ka|home|house|asli|permanent)\s+)?(?:address|pata|location|loc\b|whereabouts|phone(?:\s+number)?|"
    r"number|no\.|ghar|घर|पता|एड्रेस|लोकेशन|नंबर|नम्बर)"
)
_REQUEST = (
    r"(?:batao|bata\s+do|bata\s+de|dhundo|dhoondo|dhundho|nikalo|nikal\s+lo|leak|share|bhejo|bhej\s+do|bhej\s+de|"
    r"paas\s+hai|pata\s+hai|pata\s+kar|chahiye|dedo|de\s+do|do\b|find|track|trace|drop|post|dm|"
    r"बताओ|ढूंढो|ढूँढो|लीक|भेजो|चाहिए|दो\b)"
)
_ASK_INFO = re.compile(
    rf"(?<![a-z]){_THIRD_PERSON}\s+{_INFO_NOUN}(?:\W+\w+){{0,5}}?\W+{_REQUEST}|{_REQUEST}\W+(?:\w+\W+){{0,3}}{_THIRD_PERSON}\s+{_INFO_NOUN}|"
    rf"(?<![a-z]){_INFO_NOUN}\s+(?:leak|dhundo|dhoondo|nikalo)\b.{{0,15}}{_THIRD_PERSON}|"
    r"\b(?:leak|share)\s+karo\b.{0,20}\b(?:address|number|location)\b|\bkisi\s+ko\s+(?:iska|uska|uski|iski)\b",
    re.IGNORECASE,
)


# --- indirect requests for a person's private details ("who knows where he lives") -----------------------------
_ASK_WHO = r"(?:who|anyone|anybody|someone|somebody|koi|kisi\s+ko|kaun|kya\s+kisi|does\s+anyone|can\s+(?:someone|anyone)|pls|plz|please)"
_ASK_VERB = (
    r"(?:know|knows|tell|find|dig|get|trace|track|pata|malum|maloom|jaanta|jaanti|jaante|bata|batao|dhundh\w*|dhoondh\w*|"
    r"nikal\w*|leak|share|check|help)"
)
_LIVES = (
    r"(?:where\s+(?:\w+\s+){0,3}?(?:live|lives|living|stay|stays|staying|work|works|study|studies|reside|resides)|"
    r"(?:kahan|kaha|kidhar)\s+(?:\w+\s+){0,3}?(?:rehta|rehti|rahta|rahti|rehte|rahte|reh\s+raha|reh\s+rahi|kaam|padhta|padhti|se\s+hai|ka\s+hai)|"
    r"(?:kis|kaun\s?se|kaun\s?si|kaun\s?sa|konse|konsi|kon\s+si|kon\s+sa)\s+(?:city|area|shehar|sheher|shahar|gali|mohalla|colony|school|college|office|company|state|jagah)\s+(?:\w+\s+){0,3}?(?:rehta|rehti|rahta|rahti|se|mein|me|padhta|padhti|kaam|ka|ki)|"
    r"कहाँ\s+रहता|कहां\s+रहता|कहाँ\s+रहती|कहां\s+रहती)"
)
_PRIVATE_OF = (
    r"(?:(?:his|her|their|uska|uski|uske|iska|iski|iske|unka|unki|inka|inki|streamer\s+(?:ka|ki|ke)|is\s+\w+\s+(?:ka|ki|ke)|iss\s+\w+\s+(?:ka|ki|ke))\s+"
    r"(?:\w+\s+){0,2}?(?:address|location|loc|number|phone|home|house|ghar|pata|school|college|office|workplace|real\s+name|asli\s+naam|"
    r"full\s+name|surname|whatsapp|insta\s+id|insta|instagram|snap|snapchat|fb|facebook|private\s+id|parents|family)|"
    r"(?:उसका|इसका|उसकी|इसकी|स्ट्रीमर\s+का)\s+(?:\w+\s+){0,2}?(?:पता|एड्रेस|नंबर|नम्बर|घर|लोकेशन))"
)
_ASK_CUE = re.compile(
    r"\?|(?<![a-z])(?:kahan|kaha|kidhar|kya\s+hai|kaun\s+sa|kaunsa|which|where|batao|bata|pata|koi|who|anyone|dedo|do|chahiye|leak|share|dm|"
    r"drop|post|find|dhundo|nikalo|kya|mila|mil\s+gaya|mili|dena|dedo|de\s+do|bhej|leak)(?![a-z])|बताओ|चाहिए|कहाँ|कहां"
)
_THIRD_MARK = re.compile(
    r"(?<![a-z])(?:he|she|they|his|her|their|him|uska|uski|uske|iska|iski|iske|unka|unki|wo|woh|vo|ye|yeh|streamer|"
    r"that\s+(?:guy|girl|dude|kid)|this\s+(?:guy|girl|dude|kid))(?![a-z])|@\w"
)
_SECOND_MARK = re.compile(
    r"(?<![a-z])(?:you|u|your|ur|tu|tum|aap|ap|tera|teri|tere|tumhara|tumhari|aapka|aapki|tujhe|tumhe)(?![a-z])|(?<![a-z])rehte\s+ho|"
    r"(?<![a-z])rehti\s+ho|(?<![a-z])rahte\s+ho|(?<![a-z])rahti\s+ho|(?<![a-z])rehta\s+hai\s+tu|रहते\s+हो|रहती\s+हो"
)
_INDIRECT_ASK = re.compile(
    rf"(?<![a-z]){_ASK_WHO}\W+(?:\w+\W+){{0,6}}?{_ASK_VERB}\W+(?:\w+\W+){{0,6}}?(?:{_LIVES}|{_PRIVATE_OF})"
)
_INDIRECT_ASK_REV = re.compile(
    rf"(?<![a-z]){_ASK_WHO}\W+(?:\w+\W+){{0,4}}?(?:{_PRIVATE_OF})\W+(?:\w+\W+){{0,3}}?(?:pata|malum|maloom|jaanta|jaanti|bata|batao|hai)"
)
_LIVES_RE = re.compile(rf"(?<![a-z]){_LIVES}")
_PRIVATE_RE = re.compile(rf"(?<![a-z]){_PRIVATE_OF}")

# A written-out address: a house/flat/plot number together with a street, sector, colony ... word.
_HOUSE = (
    r"(?:h\.?\s?no\.?|house(?:\s+no\.?)?|flat(?:\s+no\.?)?|plot(?:\s+no\.?)?|door\s+no\.?|room(?:\s+no\.?)?|ward\s+no\.?|"
    r"bungalow|villa|quarters?|qtr|मकान(?:\s+नंबर)?|फ्लैट|प्लॉट|घर\s+नंबर)"
)
_STREET = (
    r"(?:sector|sec|block|blk|phase|road|rd|street|st|lane|nagar|colony|gali|mohalla|society|apartments?|apts?|enclave|vihar|"
    r"puram|marg|chowk|layout|extension|ext|cross|main|chawl|wadi|basti|residency|towers?|heights|complex|"
    r"सेक्टर|गली|नगर|कॉलोनी|रोड|मोहल्ला|चौक)"
)
_ADDR_STRUCT = re.compile(
    rf"(?<![a-z0-9]){_HOUSE}\s*[#:\-]?\s*[a-z]?\d{{1,5}}[a-z]?\b.{{0,40}}?(?<![a-z]){_STREET}(?![a-z])|"
    rf"(?<![a-z]){_STREET}\s*[#:\-]?\s*\d{{1,3}}\b.{{0,40}}?(?<![a-z]){_HOUSE}\s*[#:\-]?\s*[a-z]?\d{{1,5}}|"
    r"(?<![a-z])address\s*(?:is|:|-|=)\s*[^\n]{0,40}?\d[^\n]{0,40}",
    re.IGNORECASE,
)


def _edit1(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` are at most one edit apart (insert, delete, replace, or two letters swapped)."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return (
            len(diff) == 2
            and diff[1] == diff[0] + 1
            and a[diff[0]] == b[diff[1]]
            and a[diff[1]] == b[diff[0]]
        )
    if la > lb:
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1 :]


_STAT = re.compile(
    r"(?<![a-z])(?:kills?|score|jersey|level|rank|ping|fps|rating|followers?|subs|subscribers?|views|wins|goals|runs|wickets|"
    r"damage|headshots?|map|maps|server|game|games|save|files?|folder|bar)(?![a-z])"
)


def indirect_request(text: str) -> bool:
    """Asking for a third person's whereabouts or private details ("who knows where he lives", "iska ghar kahan hai")."""
    if _STAT.search(text):
        return False
    if _INDIRECT_ASK.search(text) or _INDIRECT_ASK_REV.search(text):
        return True
    m = _LIVES_RE.search(text)
    if (
        m is not None
        and _SECOND_MARK.search(text) is None
        and (_THIRD_MARK.search(text) or _ASK_CUE.search(text))
    ):
        return True
    return _PRIVATE_RE.search(text) is not None and _ASK_CUE.search(text) is not None


# --- Verhoeff ---------------------------------------------------------------------------------
_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5), (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7), (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3), (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)  # fmt: skip
_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4), (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7), (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)  # fmt: skip


def verhoeff_valid(number: str) -> bool:
    if not number.isdigit():
        return False
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def _digit_runs(text: str) -> list[list[str]]:
    """Runs of digit groups: numeric tokens and spoken digits separated by at most ``-.,()+``."""
    runs: list[list[str]] = []
    run: list[str] = []
    mult = 1
    pending_sep = False

    def flush() -> None:
        nonlocal run, mult, pending_sep
        if run:
            runs.append(run)
        run, mult, pending_sep = [], 1, False

    for m in _TOKEN.finditer(text):
        tok = m.group(0)
        if tok.isdigit():
            run.append(tok)
            mult, pending_sep = 1, False
        elif tok in _SPOKEN:
            run.extend([_SPOKEN[tok]] * mult)
            mult, pending_sep = 1, False
        elif tok in _MULTIPLIER:
            mult = _MULTIPLIER[tok]
            pending_sep = False
        elif tok in _RUN_SEPS and run and not pending_sep:
            pending_sep = True
        else:
            flush()
    flush()
    return runs


def _plausible(groups: list[str]) -> bool:
    """Reject accidental concatenations (counting, scores): groups must look like a written number."""
    if len(groups) == 1:
        return True
    body = groups[1:] if groups[0] in _COUNTRY else groups
    if all(len(g) == 1 for g in groups) or all(len(g) == 2 for g in body):
        return True
    return all(len(g) >= 3 for g in body)


def find_numbers(text: str) -> list[tuple[str, str]]:
    """``[(kind, digits)]`` for mobile numbers (``phone``) and Verhoeff-valid 12-digit numbers (``aadhaar``)."""
    found: list[tuple[str, str]] = []
    for run in _digit_runs(text.translate(_INDIC_DIGITS).lower()):
        run = run[:40]
        for i in range(len(run)):
            acc = ""
            for j in range(i, len(run)):
                acc += run[j]
                if len(acc) > 14:
                    break
                groups = run[i : j + 1]
                if len(acc) < 10 or not _plausible(groups):
                    continue
                if _MOBILE.fullmatch(acc):
                    found.append(("phone", acc))
                elif len(acc) == 12 and verhoeff_valid(acc):
                    # a real Aadhaar starts with 2-9; 0 or 1 is the SPECIMEN form. Both need a valid checksum.
                    found.append(("aadhaar", acc))
    return found


class DoxxingDetector:
    """Holds the streamer's private terms and phones in RAM and scans messages."""

    def __init__(self, terms: list[str] | None = None, phones: list[str] | None = None) -> None:
        self._term_re: re.Pattern[str] | None = None
        self._phones: frozenset[str] = frozenset()
        self._subs: list[str] = []
        self._fuzzy: list[tuple[int, str]] = []  # (word count, joined phonetic key) of terms of 5+ letters
        self.update(terms or [], phones or [])

    def __repr__(self) -> str:  # never print private data
        return "DoxxingDetector(<private>)"

    def update(self, terms: list[str], phones: list[str]) -> None:
        self._fuzzy = []
        self._subs = [
            v.replace(" ", "") for v in {lexicon_view(t) for t in terms} if len(v.replace(" ", "")) >= 5
        ]
        for t in terms:
            toks = TOKEN_RE.findall(lexicon_view(t))
            keys = [fold_token(x) for x in toks]
            joined = "".join(keys)
            if len(joined) >= 5 and joined.isascii():
                self._fuzzy.append((len(keys), joined))
        views = sorted({lexicon_view(t) for t in terms if len(lexicon_view(t)) >= 3}, key=len, reverse=True)
        parts = []
        for v in views:
            esc = re.escape(v)
            lead = r"(?<![a-z0-9])" if v[0].isascii() and v[0].isalnum() else ""
            trail = r"(?![a-z0-9])" if v[-1].isascii() and v[-1].isalnum() else ""
            parts.append(f"{lead}{esc}{trail}")
        self._term_re = re.compile("|".join(parts)) if parts else None
        digits = {
            re.sub(r"\D", "", unicodedata.normalize("NFKC", p).translate(_INDIC_DIGITS)) for p in phones
        }
        self._phones = frozenset(d[-10:] for d in digits if len(d) >= 10)

    @staticmethod
    def _cue_in_text(text: str, digits: str) -> bool:
        """The address cue next to the same digits in the leet-folded view (``p1nc0d3 886972``)."""
        i = text.find(digits)
        if i < 0:
            return False
        return (
            _ADDRESS_CUE.search(text[max(0, i - 30) : i] + " " + text[i + len(digits) : i + len(digits) + 30])
            is not None
        )

    def _blob_hit(self, blobs: tuple[str, ...]) -> bool:
        """A private term inside a run of single characters joined into one string (word boundaries are lost)."""
        return any(t in b.lower() for b in blobs for t in self._subs)

    def _fuzzy_hit(self, text: str) -> bool:
        """A private term of 5+ letters written with one typo, split in two words or run together."""
        if not self._fuzzy:
            return False
        keys = [fold_token(t) for t in TOKEN_RE.findall(text) if t.isascii()]
        for n_words, joined in self._fuzzy:
            for size in range(1, min(3, n_words + 1) + 1):
                for i in range(len(keys) - size + 1):
                    cand = "".join(keys[i : i + size])
                    if len(cand) >= 5 and _edit1(cand, joined):
                        return True
        return False

    def detect(self, norm: Normalized, *, include_private: bool = True) -> list[Candidate]:
        out: list[Candidate] = []
        seen_reasons: set[str] = set()
        if include_private and self._term_re is not None and self._term_re.search(norm.text):
            out.append(Candidate(Category.CHAT_DOXXING, 0.97, "dox_private_term"))
        elif (
            include_private
            and (norm.link_blobs or norm.blobs)
            and self._blob_hit(norm.link_blobs + norm.blobs)
        ):
            out.append(Candidate(Category.CHAT_DOXXING, 0.92, "dox_private_term_split"))
        elif include_private and self._fuzzy_hit(norm.text):
            out.append(Candidate(Category.CHAT_DOXXING, 0.92, "dox_private_term_fuzzy"))
        link = norm.link_text
        numbers = find_numbers(link) if len(link) >= 10 else []
        for kind, digits in numbers:
            if kind == "phone":
                private = include_private and digits[-10:] in self._phones
                reason, score = ("dox_private_phone" if private else "dox_phone"), 0.97
            else:
                reason, score = "dox_aadhaar_like", 0.9
            if reason not in seen_reasons:
                seen_reasons.add(reason)
                out.append(Candidate(Category.CHAT_DOXXING, score, reason))
        for m in _PIN.finditer(link):
            lo, hi = max(0, m.start() - 30), min(len(link), m.end() + 30)
            if _ADDRESS_CUE.search(link[lo : m.start()] + " " + link[m.end() : hi]) or self._cue_in_text(
                norm.text, m.group(0)
            ):
                out.append(Candidate(Category.CHAT_DOXXING, 0.9, "dox_pin_address"))
                break
        if _PAN.search(link):
            out.append(Candidate(Category.CHAT_DOXXING, 0.9, "dox_pan"))
        if _INTENT.search(norm.text) or _ASK_INFO.search(norm.link_text):
            out.append(Candidate(Category.CHAT_DOXXING, 0.9, "dox_intent"))
        elif indirect_request(norm.text):
            out.append(Candidate(Category.CHAT_DOXXING, 0.8, "dox_indirect_request"))
        if _ADDR_STRUCT.search(link.translate(_INDIC_DIGITS)) or _ADDR_STRUCT.search(norm.text):
            out.append(Candidate(Category.CHAT_DOXXING, 0.85, "dox_address_structure"))
        return out
