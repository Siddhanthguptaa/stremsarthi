"""Scam, fake giveaway and fake UPI payment detection (docs/specs/chat.md 5.1 step 4).

Runs on the case-preserving link view of the message (NFKC, zero-width removed), not on the
leet-folded lexicon view. Emits ``CHAT_FAKE_UPI`` (0.98) for UPI deep links and UPI IDs next to a
money word, ``CHAT_SCAM_LINK`` (0.9) for shorteners next to a payment/giveaway word, payment-link
hosts and fake giveaways. Weaker signals score 0.35-0.6 and go to the LLM stage.
"""

from __future__ import annotations

import re
from pathlib import Path

from contracts import Category

from .candidate import Candidate

_HANDLES_FILE = Path(__file__).parent / "scam_handles.txt"


def _load_handles(path: Path = _HANDLES_FILE) -> tuple[str, ...]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            out.append(line)
    return tuple(sorted(set(out), key=len, reverse=True))


def build_upi_id_regex(handles: tuple[str, ...]) -> re.Pattern[str]:
    alt = "|".join(re.escape(h) for h in handles)
    return re.compile(
        rf"(?<![\w.\-@])[a-z0-9][a-z0-9._\-]{{1,63}}@(?:{alt})(?![\w@])(?!\.[a-z0-9])", re.IGNORECASE
    )


_UPI_LINK = re.compile(r"(?:upi|tez|phonepe|paytmmp|gpay|bhim|paytm)://[^\s]*", re.IGNORECASE)
_UPI_PAY_PARAM = re.compile(r"pay\?pa=[A-Za-z0-9._\-]{2,64}@[A-Za-z]{2,32}", re.IGNORECASE)
_UPI_ID = build_upi_id_regex(_load_handles())
_UPI_PIN = re.compile(
    r"upi\s*pin|pin\s*(?:dalo|daalo|enter|share|batao|bhejo)|(?:enter|share|send|batao|bhejo)\s+(?:your\s+|ur\s+)?pin",
    re.IGNORECASE,
)
_MONEY = re.compile(
    r"\b(?:pay|paid|payment|send|sent|bhej|bhejo|bhejna|bhejdo|transfer|donate|donation|dedo|de do|"
    r"refund|cashback|recharge|gpay|phonepe|paytm|bhim|scan|rupees?|rupay|paise|paisa|inr|rs\.?\s?\d+)\b|₹",
    re.IGNORECASE,
)
_PAY_HOST = re.compile(
    r"(?<![a-z0-9.-])(?:phonepe\.com/[^\s]*pay|(?:paytm|pytm)\.me|rzp\.io|razorpay\.me|"
    r"pay\.google\.com|gpay\.app\.goo\.gl|bharatpe\.in|imp\.pe)(?:/|\b)",
    re.IGNORECASE,
)
_SHORTENER = re.compile(
    r"(?<![a-z0-9.-])(?:bit\.ly|tinyurl\.com|t\.ly|cutt\.ly|rb\.gy|is\.gd|shorturl\.at|goo\.gl|ow\.ly|"
    r"tiny\.cc|t\.me|wa\.me|chat\.whatsapp\.com|bit\.do|s\.id)/",
    re.IGNORECASE,
)
_URL = re.compile(
    r"https?://|www\.|(?<![a-z0-9@.-])[a-z0-9-]{2,}\.(?:com|in|me|ly|xyz|site|online|co|app|link|top|club|info|live|win|fun)\b",
    re.IGNORECASE,
)
_GIVEAWAY = re.compile(
    r"give\s?away|prize|winner|you\s+(?:have\s+)?won|lucky\s+draw|redeem\s+code|reward|jeet(?:a|e|o)?\b|inaam|inam|muft|"
    r"free\s+(?:fire\s+(?:max\s+)?)?(?:diamonds?|uc|robux|v-?bucks|gift\s?card|recharge|data|laptop|iphone|skin|royal\s?pass)|"
    r"claim\s+(?:your|ur|now|free|reward|prize|gift)",
    re.IGNORECASE,
)
_ACTION = re.compile(
    r"\blink\b|\bclick\b|\bupi\b|\bpay\b|\bsend\b|\bdm\b|whats\s?app|telegram|inbox|\bbio\b|\bprofile\b|\bvisit\b|\bregister\b",
    re.IGNORECASE,
)
_CONTACT = re.compile(r"\bupi\b|\bpay\b|\bdm\b|whats\s?app|telegram|inbox", re.IGNORECASE)
_EARN = re.compile(
    r"\b(?:earn|kamao|kamai|kamaye|profit|invest(?:ment)?|trading|crypto|loan|double\s+your)\b", re.IGNORECASE
)
_EARN_CONTACT = re.compile(r"\bdm\b|whats\s?app|telegram|inbox|\bcontact\b", re.IGNORECASE)
_LURE = re.compile(
    r"(?:send|pay|bhejo|bhej)\s+(?:rs\.?\s*|₹\s*)?\d+\s*(?:rs|rupees|₹)?.{0,25}(?:get|milega|milenge|paao|pao|double)",
    re.IGNORECASE,
)
_WINDOW = 40


def _near(a: re.Pattern[str], b: re.Pattern[str], text: str, window: int = _WINDOW) -> bool:
    spans_b = [m.span() for m in b.finditer(text)]
    if not spans_b:
        return False
    for m in a.finditer(text):
        s, e = m.span()
        for bs, be in spans_b:
            if (bs >= e and bs - e <= window) or (s >= be and s - be <= window) or (bs < e and be > s):
                if (bs, be) != (s, e):
                    return True
    return False


def detect_scam(link_text: str) -> list[Candidate]:
    """All scam candidates in one message (``link_text`` is the link view from ``normalize``)."""
    out: list[Candidate] = []
    text = link_text
    if not text:
        return out
    has_money = _MONEY.search(text) is not None

    if _UPI_LINK.search(text) or _UPI_PAY_PARAM.search(text):
        out.append(Candidate(Category.CHAT_FAKE_UPI, 0.98, "fake_upi_link"))
    if _UPI_ID.search(text):
        if has_money:
            out.append(Candidate(Category.CHAT_FAKE_UPI, 0.98, "fake_upi_id_money"))
        else:
            out.append(Candidate(Category.CHAT_FAKE_UPI, 0.6, "upi_id_bare"))
    if _UPI_PIN.search(text):
        out.append(Candidate(Category.CHAT_FAKE_UPI, 0.95, "fake_upi_pin"))
    if _PAY_HOST.search(text):
        out.append(Candidate(Category.CHAT_SCAM_LINK, 0.9, "scam_pay_host"))

    giveaway = _GIVEAWAY.search(text) is not None
    if _SHORTENER.search(text):
        if giveaway or has_money:
            out.append(Candidate(Category.CHAT_SCAM_LINK, 0.9, "scam_shortener_word"))
        else:
            out.append(Candidate(Category.CHAT_SCAM_LINK, 0.35, "shortener_bare"))
    if giveaway and (_near(_GIVEAWAY, _ACTION, text) or _near(_GIVEAWAY, _URL, text)):
        strong = _near(_GIVEAWAY, _URL, text) or _near(_GIVEAWAY, _CONTACT, text)
        out.append(
            Candidate(
                Category.CHAT_SCAM_LINK,
                0.9 if strong else 0.6,
                "scam_giveaway" if strong else "giveaway_weak",
            )
        )
    if _LURE.search(text):
        out.append(Candidate(Category.CHAT_SCAM_LINK, 0.9, "scam_money_lure"))
    if _EARN.search(text) and _near(_EARN, _EARN_CONTACT, text):
        out.append(Candidate(Category.CHAT_SCAM_LINK, 0.9, "scam_earn_dm"))
    return out
