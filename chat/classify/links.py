"""URL and UPI-handle helpers shared by the normaliser and the scam detector.

* ``refang`` rewrites the usual link obfuscations (``hxxp``, ``[.]``, ``(dot)``, ``name . site``, ``http: / /``,
  ``id (at) ybl``, ``id at the rate ybl``) into plain form, so one set of regexes sees both.
* ``find_urls`` returns the links of a message as ``Link`` objects (host, path, registrable domain).
* ``blank_urls`` replaces every link with a space (the abuse lexicon must never read a domain name).

The tables are general knowledge about link abuse (cheap TLDs, lure words, brands that scammers
imitate, genuine domains of those brands, domains that are never scams), not tied to any data set.
Nothing here logs or stores text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# --- tables -------------------------------------------------------------------------------------

#: TLDs that are cheap or free and over-represented in scam links.
SUSPICIOUS_TLDS = frozenset(
    "xyz top click vip monster buzz icu cc site online club live win fun link cfd sbs shop store work rest "
    "bond cyou ws tk ml ga cf gq pw info biz life lol today space website tech cam loan men stream "
    "download racing party review date trade bid gdn host press uno".split()
)
#: TLDs worth recognising as a link when written without a scheme.
COMMON_TLDS = frozenset("com in me co org net io app dev tv gg to ly be gl".split())
_ALL_TLDS = SUSPICIOUS_TLDS | COMMON_TLDS
_SECOND_LEVEL = frozenset({"co", "com", "org", "net", "gov", "ac", "edu", "nic"})

#: Words that make a link a lure when they appear in its host or path.
LURE_WORDS = (
    "cashback", "giveaway", "claim", "bonus", "winner", "reward", "verify", "verification", "refund",
    "airdrop", "jackpot", "lucky", "freegift", "recharge", "unban", "appeal", "kyc", "loan", "gift",
    "prize", "free", "diamond", "royalpass", "elitepass", "voucher", "coupon", "offer", "earn", "usdt",
    "crypto", "invest", "hack", "aimbot", "unlock", "secure-login", "wallet", "lottery", "otp",
)  # fmt: skip
_LURE_RE = re.compile("|".join(LURE_WORDS))
_LURE_TOKEN_RE = re.compile(r"(?<![a-z0-9])(?:uc|login|update|support|apk|mod|sale|win)(?![a-z0-9])")

#: Brands that scam links imitate (substring in the host).
BRANDS = (
    "paytm", "phonepe", "gpay", "googlepay", "bhim", "hdfc", "icici", "sbi", "axisbank", "kotak", "pnb",
    "jio", "airtel", "flipkart", "amazon", "myntra", "dream11", "bgmi", "pubg", "freefire", "garena",
    "youtube", "instagram", "facebook", "whatsapp", "telegram", "netflix", "binance", "wazirx", "coinbase",
)  # fmt: skip
#: Registrable domains that really belong to those brands (or are common and harmless).
TRUSTED_DOMAINS = frozenset(
    """paytm.com phonepe.com hdfcbank.com sbi.co.in icicibank.com axisbank.com kotak.com pnbindia.in
    jio.com airtel.in flipkart.com amazon.in amazon.com myntra.com dream11.com pubg.com garena.com
    youtube.com youtu.be instagram.com facebook.com whatsapp.com telegram.org netflix.com binance.com
    wazirx.com coinbase.com google.com twitch.tv twitter.com x.com github.com wikipedia.org reddit.com
    discord.com discord.gg spotify.com bbc.com bbc.co.uk imgur.com linkedin.com microsoft.com apple.com
    nytimes.com thehindu.com ndtv.com timesofindia.com hindustantimes.com stackoverflow.com
    medium.com gmail.com bgmi.in epicgames.com steampowered.com steamcommunity.com itch.io gog.com
    ubisoft.com ea.com playstation.com xbox.com nintendo.com mozilla.org tiktok.com snapchat.com
    pinterest.com quora.com patreon.com ko-fi.com streamlabs.com streamelements.com paypal.com
    openai.com anthropic.com""".split()
)
SHORTENER_HOSTS = frozenset(
    "bit.ly tinyurl.com t.ly cutt.ly rb.gy is.gd shorturl.at goo.gl ow.ly tiny.cc t.me wa.me "
    "chat.whatsapp.com bit.do s.id".split()
)

# --- UPI handles ----------------------------------------------------------------------------------

_HANDLES_FILE = Path(__file__).parent / "scam_handles.txt"


def _load_handles(path: Path = _HANDLES_FILE) -> tuple[str, ...]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            out.append(line)
    return tuple(sorted(set(out), key=len, reverse=True))


UPI_HANDLES = _load_handles()
#: Handles that are almost never anything but a UPI handle: ``name at ybl`` is read as an ID without further checks.
CLEAR_HANDLES = frozenset(
    "ybl ibl axl apl fbl okaxis oksbi okicici okhdfcbank okbizaxis yapl ptyes pthdfc ptsbi ptaxis axisb ikwik kmbl kmhdfc "
    "waaxis wahdfcbank waicici wasbi abfspay idfcfirst rbl".split()
)
# Words that precede an ordinary "at" ("meet me at paytm"), never the name part of a UPI ID.
_AT_STOP = frozenset(
    """me you him her us it them the a an at on in is are was be am i we they he she look talk meet stay wait available home
    work here there up down out off call text msg message pay paid send sent transfer recharge donate refund scan only now also
    same sirf aur ya bas ab abhi yahan wahan kal aaj ghar office shop store open close live online offline joined join""".split()
)
_HANDLE_ALT = "|".join([re.escape(h) for h in UPI_HANDLES] + [r"ok[\s\-_]?(?:axis|sbi|icici|hdfcbank)"])
_AT_BARE = re.compile(
    rf"(?<![A-Za-z0-9._\-@])(?P<id>[A-Za-z0-9][A-Za-z0-9._\-]{{1,62}})(?:\s+|[-_])[aàáâäåāа]t(?:\s+|[-_])(?P<h>{_HANDLE_ALT})\b(?![\w@])(?!\.[A-Za-z0-9])",
    re.IGNORECASE,
)


# "s h o p 1 2 @ y b l": every character of the ID and of the handle is separated by a space.
_SPACED_ID = re.compile(
    r"(?<![A-Za-z0-9])(?P<id>(?:[A-Za-z0-9._\-]\s){3,30}[A-Za-z0-9._\-])\s*(?:@|\(at\)|\[at\]|\bat\b)\s*(?P<h>(?:[A-Za-z]\s){2,11}[A-Za-z])(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def _unspace_id(m: re.Match[str]) -> str:
    handle = re.sub(r"\s", "", m.group("h")).lower()
    if handle in UPI_HANDLES:
        ident = re.sub(r"\s", "", m.group("id"))
        return f"{ident}@{handle}"
    return m.group(0)


def _bare_at(m: re.Match[str]) -> str:
    ident, handle = m.group("id"), re.sub(r"[\s\-_]", "", m.group("h")).lower()
    if ident.lower() in _AT_STOP:
        return m.group(0)
    if handle in CLEAR_HANDLES or re.search(r"[\d._\-]", ident):
        return f"{ident}@{handle}"
    return m.group(0)


# --- refanging ----------------------------------------------------------------------------------

_TLD_ALT = "|".join(sorted(_ALL_TLDS, key=len, reverse=True))
_SCHEME_FANG = re.compile(r"\bh[x*]{2}p(s?)\s*(?:[:：]\s*/\s*/|://)\s*", re.IGNORECASE)
_SCHEME_SPACED = re.compile(r"\b(https?)\s*:\s*/\s*/\s*", re.IGNORECASE)
_DOT_WORD = re.compile(
    r"\s*(?:\[\s*\.\s*\]|\(\s*\.\s*\)|\{\s*\.\s*\}|\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|<\s*dot\s*>)\s*",
    re.IGNORECASE,
)
_SPOKEN_DOT = re.compile(r"(?<=[A-Za-z0-9-])\s+(?:dot|डॉट)\s+(?=[A-Za-z0-9-]+(?:/|\b))", re.IGNORECASE)
_SPACED_DOT = re.compile(
    rf"(?<=[A-Za-z0-9-]{{4}})\s+\.\s+(?=(?:{_TLD_ALT})\b)",
    re.IGNORECASE,
)
_SHORT_SPACED = re.compile(
    r"\b(t|rb|s|is|wa|bit|ow|goo|tiny|cutt|tinyurl|shorturl)\s+\.\s+(ly|gy|id|me|do|at|cc|gl|com|url)\b(?=/)",
    re.IGNORECASE,
)
_AT_WORD = re.compile(
    r"\s*(?:[\(\[\{<]\s*(?:at|@)\s*[\)\]\}>]|\bat\s+the\s+rate(?:\s+of)?\b|एट\s*(?:द|दी)\s*रेट|"
    r"\bat\s*\[\s*\]|＠)\s*",
    re.IGNORECASE,
)
_AT_SPACED = re.compile(
    rf"(?<=[A-Za-z0-9])\s+@\s+(?=[A-Za-z])|(?<=[A-Za-z0-9])\s*@\s+(?=(?:{_HANDLE_ALT})(?![\w@]))|"
    rf"(?<=[A-Za-z0-9])\s+@\s*(?=(?:{_HANDLE_ALT})(?![\w@]))",
    re.IGNORECASE,
)


def refang(text: str) -> str:
    """Undo common link / UPI-ID obfuscation. Idempotent; text without such marks is returned unchanged."""
    if not text:
        return text
    t = text
    low = t.lower()
    if "x" in low and ("hxx" in low or "h*" in low):
        t = _SCHEME_FANG.sub(lambda m: f"http{m.group(1)}://", t)
    if ":" in t and "/" in t:
        t = _SCHEME_SPACED.sub(lambda m: f"{m.group(1)}://", t)
    if "[" in t or "(" in t or "{" in t or "<" in t:
        t = _DOT_WORD.sub(".", t)
    if "dot" in low or "डॉट" in t:
        t = _SPOKEN_DOT.sub(".", t)
    if " . " in t:
        t = _SHORT_SPACED.sub(lambda m: f"{m.group(1)}.{m.group(2)}", t)
        t = _SPACED_DOT.sub(".", t)
    if "at" in low or "एट" in t or "＠" in t or "(@)" in t or "[@]" in t or "{@}" in t:
        t = _AT_WORD.sub("@", t)
    if " @" in t or "@ " in t:
        t = _AT_SPACED.sub("@", t)
    if "@" in t or "at" in low:
        t = _SPACED_ID.sub(_unspace_id, t)
    if "at" in low or "ät" in low or "àt" in low or "át" in low:
        t = _AT_BARE.sub(_bare_at, t)
    return t


# --- link extraction ----------------------------------------------------------------------------

_URL_RE = re.compile(
    r"""
    (?P<scheme>https?://|www\.)?
    (?P<host>(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?P<tld>[a-z]{2,24})|(?:\d{1,3}\.){3}\d{1,3})
    (?::\d{2,5})?
    (?P<path>[/?#][^\s]*)?
    """,
    re.IGNORECASE | re.VERBOSE,
)
_EMAIL_LIKE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_TRAIL = ".,;:!?)]}>\"'"


@dataclass(frozen=True, slots=True)
class Link:
    host: str  # lowercase
    path: str  # lowercase, may be empty
    domain: str  # registrable domain (``example.co.in``) or the IP
    tld: str  # lowercase, ``""`` for an IP host
    has_scheme: bool
    is_ip: bool
    span: tuple[int, int]

    @property
    def shortener(self) -> bool:
        return self.domain in SHORTENER_HOSTS or self.host in SHORTENER_HOSTS


def _registrable(host: str) -> str:
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


_SLICE = 600
_STEP = 400


def find_links(text: str) -> list[Link]:
    """Links in ``text`` (already refanged), found slice by slice so hostile input stays linear in its length."""
    if len(text) <= _SLICE:
        return _find_links(text)
    out: list[Link] = []
    seen: set[int] = set()
    for start in range(0, len(text), _STEP):
        for ln in _find_links(text[start : start + _SLICE]):
            begin = ln.span[0] + start
            if begin in seen:
                continue
            seen.add(begin)
            out.append(
                Link(ln.host, ln.path, ln.domain, ln.tld, ln.has_scheme, ln.is_ip, (begin, ln.span[1] + start))
            )
        if len(out) >= 50:
            break
    return out


def _find_links(text: str) -> list[Link]:
    """Links in ``text`` (already refanged). A bare ``name.tld`` counts only for known TLDs."""
    out: list[Link] = []
    if "." not in text:
        return out
    email_spans = [m.span() for m in _EMAIL_LIKE.finditer(text)]
    for m in _URL_RE.finditer(text):
        s, e = m.span()
        if s > 0 and (text[s - 1].isalnum() or text[s - 1] in "@._-"):
            continue
        if any(es <= s < ee for es, ee in email_spans):
            continue
        host = m.group("host").lower().rstrip(".")
        scheme = m.group("scheme")
        tld = (m.group("tld") or "").lower()
        is_ip = tld == "" and host.replace(".", "").isdigit()
        if is_ip:
            if not all(0 <= int(p) <= 255 for p in host.split(".")):
                continue
        else:
            if not scheme and tld not in _ALL_TLDS:
                continue
            if tld.isdigit():
                continue
        raw_path = m.group("path") or ""
        path = raw_path.lower().rstrip(_TRAIL)
        end = e - (len(raw_path) - len(raw_path.rstrip(_TRAIL)))
        out.append(
            Link(
                host=host,
                path=path,
                domain=host if is_ip else _registrable(host),
                tld="" if is_ip else tld,
                has_scheme=bool(scheme),
                is_ip=is_ip,
                span=(s, end),
            )
        )
    return out


def blank_urls(text: str) -> str:
    """``text`` with each link replaced by a space (after refanging)."""
    t = refang(text)
    links = find_links(t)
    if not links:
        return t
    parts: list[str] = []
    pos = 0
    for ln in links:
        parts.append(t[pos : ln.span[0]])
        parts.append(" ")
        pos = ln.span[1]
    parts.append(t[pos:])
    return "".join(parts)


# --- judging a link -----------------------------------------------------------------------------


@lru_cache(maxsize=4096)
def _brand_in(host: str) -> str:
    for b in BRANDS:
        if b in host:
            return b
    return ""


def is_trusted(link: Link) -> bool:
    return link.domain in TRUSTED_DOMAINS


def link_lure(link: Link) -> bool:
    """A lure word in the host or path (or one of the short ones as a separate token)."""
    hp = link.host.rsplit(".", 1)[0] + link.path if not link.is_ip else link.path
    return bool(_LURE_RE.search(hp) or _LURE_TOKEN_RE.search(re.sub(r"[./?=&_]", "-", hp)))


def link_impersonates(link: Link) -> bool:
    """The host carries a well-known brand but is not one of that brand's domains."""
    if link.is_ip or is_trusted(link):
        return False
    # a brand only counts in the part before the registrable domain's TLD (``paytm.lucky-x.top`` yes)
    return bool(_brand_in(link.host.rsplit(".", 1)[0]))
