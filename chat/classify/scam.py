"""Scam, fake giveaway and fake UPI payment detection (docs/specs/chat.md 5.1 step 4).

Runs on the case-preserving link view of the message (NFKC, zero-width removed, link and UPI-ID
obfuscation undone: ``hxxp``, ``[.]``, ``(at)``, ``at the rate``), not on the leet-folded lexicon view.
Emits ``CHAT_FAKE_UPI`` (0.98) for UPI deep links and UPI IDs next to a money word, ``CHAT_SCAM_LINK`` (0.9)
for shorteners next to a payment/giveaway word, payment-link hosts, fake giveaways and links that look
like a lure by their structure: a raw IP address, a well-known brand inside a domain that does not belong
to that brand, a lure word in a cheap-TLD domain, or an ordinary link next to scam wording (KYC, loan,
lottery, hack tools, unban or verification offers). Weaker signals score 0.35-0.6 and go to the LLM stage.
Links to well-known harmless domains never count by themselves.
"""

from __future__ import annotations

import re

from contracts import Category

from .candidate import Candidate
from .links import (
    SUSPICIOUS_TLDS,
    UPI_HANDLES,
    Link,
    find_links,
    is_trusted,
    link_impersonates,
    link_lure,
    refang,
)


def build_upi_id_regex(handles: tuple[str, ...]) -> re.Pattern[str]:
    alt = "|".join(re.escape(h) for h in handles)
    return re.compile(
        rf"(?<![\w.\-@])[a-z0-9][a-z0-9._\-]{{1,63}}@(?:{alt})(?![\w@])(?!\.[a-z0-9])", re.IGNORECASE
    )


_UPI_LINK = re.compile(r"(?:upi|tez|phonepe|paytmmp|gpay|bhim|paytm)://[^\s]*", re.IGNORECASE)
_UPI_PAY_PARAM = re.compile(r"pay\?pa=[A-Za-z0-9._\-]{2,64}@[A-Za-z]{2,32}", re.IGNORECASE)
_UPI_ID = build_upi_id_regex(UPI_HANDLES)
_UPI_PIN = re.compile(
    r"upi\s*pin|pin\s*(?:dalo|daalo|enter|share|batao|bhejo)|(?:enter|share|send|batao|bhejo)\s+(?:your\s+|ur\s+)?pin",
    re.IGNORECASE,
)
_MONEY = re.compile(
    r"\b(?:pay|paid|payment|send|sent|bhej|bhejo|bhejna|bhejdo|transfer|donate|donation|dedo|de do|"
    r"refund|cashback|recharge|gpay|phonepe|paytm|bhim|scan|rupees?|rupay|paise|paisa|inr|rs\.?\s?\d+)\b|₹",
    re.IGNORECASE,
)
_MONEY_DEVA = re.compile(r"भेजो|भेज|भेजना|पेमेंट|पे\s*करो|रुपये|रुपए|रुपया|ट्रांसफर|दान|डोनेट|रिफंड")
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
    r"\b(?:send|pay|bhejo|bhej)\s+(?:rs\.?\s*|₹\s*)?\d+\s*(?:rs|rupees|₹)?\b.{0,25}\b(?:get|milega|milenge|paao|pao|double)\b",
    re.IGNORECASE,
)
# Scam wording that, next to an ordinary link, makes the link a lure.
_TOPIC = re.compile(
    r"\bkyc\b|account\s+(?:will\s+be\s+)?(?:suspend|block|band|ban|freez|close)|(?:suspend|block|band|ban)(?:ed|ho)?\s+(?:ho\s+)?(?:jayega|gaya|ho\s+gaya|tonight)|"
    r"\bloan\b|lottery|lucky\s+draw|\bjeet(?:a|e|i|o)\b|\bwon\b|\bprize\b|\bwinner\b|\bearn(?:ing)?\b|kamai|\bkamao\b|\bdouble\b|invest|"
    r"\busdt\b|crypto|airdrop|\btoken\b|\bcoin\b|verif(?:y|ication)|unban|\bappeal\b|monetis|monetiz|\blogin\b|"
    r"refund|cashback|\bbonus\b|\bclaim\b|\bgift\b|\bfree\b|\bhack\b|aimbot|wallhack|mod\s+apk|\bapk\b|unlimited|"
    r"redeem|elite\s+pass|royal\s+pass|\bdiamonds?\b|\buc\b|winning\s+team|grand\s+league|work\s+from\s+home|"
    r"\bapply\b|\bapprove[d]?\b|no\s+documents?|bina\s+document|\bblue\s*tick\b|verified\s+badge|"
    r"आपने|जीत|इनाम|लॉटरी|कमाई|पैसे\s+दुगने|दुगना|मुफ़्त|मुफ्त|फ्री|क्लिक|यहाँ\s+क्लिक|यहां\s+क्लिक|लोन|केवाईसी",
    re.IGNORECASE,
)
# Hack tools and cheats offered through some contact channel.
_CHEAT = re.compile(
    r"\bhack\b|aimbot|wallhack|mod\s+apk|unlimited\s+(?:diamonds?|uc|coins?)|\bcheat\b", re.IGNORECASE
)
_DOWNLOAD_CONTACT = re.compile(
    r"\bdm\b|whats\s?app|telegram|inbox|\bbio\b|download|\blink\b|\bt\.me\b", re.IGNORECASE
)
# A question or a warning is not an offer.
_NOT_AN_OFFER = re.compile(
    r"\?|\bkaise\b|\bkaisa\b|\bkya\b|\bkyun\b|\bhow\b|\bwhy\b|\bbina\b|\bwithout\b|\bnahi\b|\bnhi\b|\bmat\b|"
    r"\bscam\b|\bfake\b|\bfraud\b|\bwarning\b|\bbeware\b|\bsavdhan\b|don'?t|\bdo\s+not\b",
    re.IGNORECASE,
)
_WINDOW = 40

# A QR code or "scan this" lure in text: scan + (a QR word or an imperative) + something to receive.
_SCAN = re.compile(r"\bscan\b|स्कैन|\bscaan\b|\bskan\b", re.IGNORECASE)
_QR_WORD = re.compile(r"\bqr\b|\bq\.r\b|barcode|bar\s+code|\bcode\b|क्यूआर|क्यू\s?आर", re.IGNORECASE)
_SCAN_IMPERATIVE = re.compile(
    r"\bscan\s+(?:karo|kar|kro|kijiye|kijiyega|krna|karna|karke|this|the|now|it|here|ye|yeh|isko|is|and|to|for)\b",
    re.IGNORECASE,
)
_RECEIVE = re.compile(
    r"\b(?:receive|recieve|get|milega|milenge|milti|paao|pao|lo|lena|le\s+lo|claim|win|jeet|reward|prize|cashback|refund|"
    r"lakhpati|crorepati|jackpot|inaam|inam|paise|paisa|money|rupees?|rs\.?|inr|donat\w*|bonus|gift|free|payment|pay|credit|transfer|amount)\b|₹",
    re.IGNORECASE,
)

_PAST_SCAN = re.compile(
    r"\bscan(?:ned)?\s+(?:kar\s+)?(?:diya|liya|kiya|kar\s+liya|ho\s+gaya|hogaya)\b|\bscanned\b", re.IGNORECASE
)

# Talking ABOUT scams (a warning, a moderator notice): not an offer. Needs a negated action or scam wording plus
# a moderation or awareness cue, so "100% legit, not a scam, send to ..." does not qualify.
_WARN_NEG = re.compile(
    r"\b(?:mat|na|nahi|nhi|nai|never|don'?t|dont|do\s+not|avoid|stop|bacho|bachna|kabhi\s+nahi)\s+(?:\w+\s+){0,3}?"
    r"(?:click|kholo|khol|open|bhejo|bhejna|bhej|send|pay|payment|scan|share|karo|karna|daalo|dalo|try|trust|download|join|reply|believe)\b|"
    r"\b(?:click|open|send|pay|scan|share|download|join|trust|reply)\w*\s+(?:\w+\s+){0,4}?(?:mat|nahi|nhi|na\s+kare|na\s+karein|not|never)\b",
    re.IGNORECASE,
)
_SCAM_NAME = re.compile(
    r"\b(?:scams?|scammers?|fraud(?:s|ster|sters)?|phishing|phish|fake|spam(?:mer|mers)?|dhokha|dhokhebaaz|thagi|thag|bots?)\b|फ्रॉड|स्कैम|धोखा",
    re.IGNORECASE,
)
_MOD_CUE = re.compile(
    r"\b(?:mods?|moderators?|admins?|ban|banned|bans|delete|deleted|remove|removed|report|reported|alert|warning|warn|beware|"
    r"careful|savdhan|dhyan|reminder|notice|bacho|bachna|ignore|block|aware|awareness|attention|"
    r"streamer\s+(?:will\s+)?never|never\s+ask|kabhi\s+nahi\s+(?:maangte|mangte|puchte)|hum\s+kabhi)\b",
    re.IGNORECASE,
)


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


def _is_discussion(text: str) -> bool:
    """A warning or a moderator notice about scams (see ``_WARN_NEG``)."""
    if _WARN_NEG.search(text):
        return True
    return _SCAM_NAME.search(text) is not None and _MOD_CUE.search(text) is not None


def detect_scam(link_text: str, *, fanged: bool = False) -> list[Candidate]:
    """All scam candidates in one message (``link_text`` is the link view from ``normalize``).

    ``fanged`` says the link view differs from the text as written (an ID or link was written in an obfuscated way).
    A warning or moderator notice about scams demotes the hard findings to one ``lenient`` candidate.
    """
    out = _detect_scam(link_text, fanged)
    if out and any(c.score >= 0.5 and not c.lenient for c in out):
        text = refang(link_text)
        if _is_discussion(text) and not _LURE.search(text):
            return [Candidate(out[0].category, 0.5, "scam_discussion", lenient=True)]
    return out


def _detect_scam(link_text: str, fanged: bool) -> list[Candidate]:
    out: list[Candidate] = []
    text = link_text
    if not text:
        return out
    text = refang(text)
    has_money = _MONEY.search(text) is not None or _MONEY_DEVA.search(text) is not None

    if _UPI_LINK.search(text) or _UPI_PAY_PARAM.search(text):
        out.append(Candidate(Category.CHAT_FAKE_UPI, 0.98, "fake_upi_link"))
    if _UPI_ID.search(text):
        if has_money:
            out.append(Candidate(Category.CHAT_FAKE_UPI, 0.98, "fake_upi_id_money"))
        elif fanged:  # nobody hides a legitimate handle behind "(at)" or "[at]"
            out.append(Candidate(Category.CHAT_FAKE_UPI, 0.95, "fake_upi_id_obfuscated"))
        else:
            out.append(Candidate(Category.CHAT_FAKE_UPI, 0.6, "upi_id_bare"))
    if _SCAN.search(text) and not _NOT_AN_OFFER.search(text) and not _PAST_SCAN.search(text):
        if _RECEIVE.search(text) and (_QR_WORD.search(text) or _SCAN_IMPERATIVE.search(text)):
            out.append(Candidate(Category.CHAT_FAKE_UPI, 0.95, "fake_upi_qr_lure"))
        elif _QR_WORD.search(text):
            out.append(Candidate(Category.CHAT_FAKE_UPI, 0.5, "qr_scan_bare", lenient=True))
    if _UPI_PIN.search(text) and not _NOT_AN_OFFER.search(text):  # "upi pin kaise reset kare" is a question
        out.append(Candidate(Category.CHAT_FAKE_UPI, 0.95, "fake_upi_pin"))
    if _PAY_HOST.search(text):
        out.append(Candidate(Category.CHAT_SCAM_LINK, 0.9, "scam_pay_host"))

    links = find_links(text)
    out.extend(_judge_links(links, text))

    giveaway = _GIVEAWAY.search(text) is not None
    if _SHORTENER.search(text):
        if giveaway or has_money:
            out.append(Candidate(Category.CHAT_SCAM_LINK, 0.9, "scam_shortener_word"))
        else:
            out.append(Candidate(Category.CHAT_SCAM_LINK, 0.35, "shortener_bare"))
    if giveaway and (_near(_GIVEAWAY, _ACTION, text) or _near(_GIVEAWAY, _URL, text) or links):
        strong = _near(_GIVEAWAY, _URL, text) or _near(_GIVEAWAY, _CONTACT, text)
        if strong or any(not is_trusted(ln) for ln in links):
            out.append(Candidate(Category.CHAT_SCAM_LINK, 0.9, "scam_giveaway"))
        elif _NOT_AN_OFFER.search(text):
            pass  # "how do I get free diamonds without a link?" is a question
        else:
            out.append(Candidate(Category.CHAT_SCAM_LINK, 0.6, "giveaway_weak"))
    if _CHEAT.search(text) and _DOWNLOAD_CONTACT.search(text) and not _NOT_AN_OFFER.search(text):
        out.append(Candidate(Category.CHAT_SCAM_LINK, 0.9, "scam_cheat_offer"))
    if _LURE.search(text):
        out.append(Candidate(Category.CHAT_SCAM_LINK, 0.9, "scam_money_lure"))
    if _EARN.search(text) and _near(_EARN, _EARN_CONTACT, text):
        out.append(Candidate(Category.CHAT_SCAM_LINK, 0.9, "scam_earn_dm"))
    return out


def _judge_links(links: list[Link], text: str) -> list[Candidate]:
    """Structure-based verdicts on the links of one message (see the module docstring)."""
    out: list[Candidate] = []
    if not links:
        return out
    topic = _TOPIC.search(text) is not None
    for ln in links:
        if is_trusted(ln) or ln.shortener:
            continue
        odd_tld = ln.tld in SUSPICIOUS_TLDS
        lure = link_lure(ln)
        if ln.is_ip:
            out.append(Candidate(Category.CHAT_SCAM_LINK, 0.9, "scam_ip_link"))
        elif link_impersonates(ln):
            out.append(Candidate(Category.CHAT_SCAM_LINK, 0.9, "scam_lookalike_domain"))
        elif odd_tld and (lure or topic):
            out.append(Candidate(Category.CHAT_SCAM_LINK, 0.9, "scam_lure_link"))
        elif lure and topic:
            out.append(Candidate(Category.CHAT_SCAM_LINK, 0.9, "scam_lure_link"))
        elif topic and ln.has_scheme and not _NOT_AN_OFFER.search(text):
            out.append(Candidate(Category.CHAT_SCAM_LINK, 0.6, "scam_topic_link"))
        elif odd_tld:
            out.append(Candidate(Category.CHAT_SCAM_LINK, 0.45, "odd_tld_link"))
    return out
