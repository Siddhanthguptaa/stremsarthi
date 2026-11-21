"""Weak signals: messages the rules did not flag but that an LLM should look at.

The lexicons catch what they know. Plenty of abuse, threats, harassment and doxxing is phrased with no listed
word. These cheap checks look for the *shape* of such a message (aimed at a person, plus violence, sex, hate
or a request for someone's private details) and emit a ``lenient`` candidate (score 0.4). With a Groq stage it
goes there; with none the local fallback is ALLOW, so these never add false positives on their own.

This is deliberately narrow: it must not send ordinary chat to the LLM (token budget 8,000 per minute). The
selectivity is measured in the tests (a share of clean messages that reach it).
"""

from __future__ import annotations

import re

from contracts import Category

from .candidate import Candidate
from .normalize import Normalized

_SECOND = re.compile(
    r"(?<![a-z0-9])(?:tu|tera|teri|tere|tujhe|tujhko|tumhe|tumko|tum|tumhara|tumhari|tumhare|aap|aapko|you|your|ur|u|"
    r"streamer|nuvvu|ninnu|ninna|nin|neenu|unna|unnai|tui|tor|tomake)(?![a-z0-9])|@\w|तू|तुम|तेरा|तेरी|तुझे|आप"
)
_VIOLENCE = re.compile(
    r"(?<![a-z0-9])(?:maar\w*|mar\s+ja\w*|kaat\w*|tod\w*|phod\w*|jala\w*|goli|chaku|knife|gun|bomb|acid|kill\w*|murder\w*|die|"
    r"dead|death|hang\w*|shoot\w*|stab\w*|burn\w*|hurt\w*|beat|punch\w*|slap\w*|encounter|accident|khatam|barbaad|"
    r"khoon|khun|janaza|haddi|pasli|zinda|jaan|swat|dox\w*|hunt\w*|track\w*|find\s+you|locat\w*)(?![a-z0-9])|मार|काट|गोली|जान|खून"
)
_SEXUAL = re.compile(
    r"(?<![a-z0-9])(?:nude|nudes|nangi|body|figure|chest|boobs?|cleavage|thighs?|legs|sexy|hot|maal|item|patakha|sex|"
    r"sleep\s+with|hotel|raat|night|bed|bistar|moan|rate|price|kitne|available|bikini|panty|bra|kapde|kapde|"
    r"chudai|chodna|lund|gaand|gand)(?![a-z0-9])|नंगी|कपडे|सेक्स|रात|होटल"
)
_NEGATIVE_EVAL = re.compile(
    r"(?<![a-z0-9])(?:bekaar|bekar|bakwas|kachra|zero|joker|clown|chhapri|chapri|nalayak|nikamma|waste|useless|worthless|"
    r"loser|pathetic|disgusting|garbage|trash|stupid|dumb|idiot|moron|pagal|mental|bewakoof|gawar|jahil|ghatiya|"
    r"kisi\s+kaam\s+ka\s+nahi|kuch\s+nahi\s+kar\s+(?:payega|sakta|sakti)|shakal|aukat|muh\s+band|chup\s+kar|nikal|"
    r"dafa|bhaag|bhag\s+ja|get\s+lost|shut\s+up|fuck\w*|hate|nafrat|ghin|sharam|besharam|badtameez|kutta|kutte|suar|janwar|"
    r"animal|pig|dog|rat|cockroach|vermin|filth\w*|dirty|gandi|ganda|gande|neech)(?![a-z0-9])"
)
_GROUP = re.compile(
    r"(?<![a-z0-9])(?:muslims?|musalman\w*|mussalman\w*|hindus?|sikhs?|sardars?|christians?|isai|dalits?|brahmins?|bamun|baniya|"
    r"jats?|yadavs?|rajputs?|thakurs?|madrasis?|biharis?|bengalis?|gujjus?|marwaris?|punjabis?|pahadis?|nepalis?|chinese|"
    r"pakistanis?|africans?|blacks?|jews?|arabs?|tribals?|adivasis?|gays?|lesbians?|trans(?:gender)?s?|hijras?|"
    r"ladkiyan|ladkiyon|ladkiya|girls|women|aurat\w*|south\s+indians?|north\s+indians?|dalit|caste|jaat|jati|religion|dharm)(?![a-z0-9])"
)
# Scripts of the languages whose lexicons are thin: Telugu, Kannada, Tamil, Bengali.
_REGIONAL_SCRIPT = re.compile("[ఀ-౿ಀ-೿஀-௿ঀ-৿]")
_DIGITS_RUN = re.compile(r"(?:\d[\s\-.]?){7,}")
_CONTACT_CUE = re.compile(
    r"(?<![a-z0-9])(?:whatsapp|call|number|no|contact|phone|mobile|dm|inbox|insta|instagram|snap|telegram|address|pata|ghar)(?![a-z0-9])"
)
_PLACE_CUE = re.compile(
    r"(?<![a-z0-9])(?:ghar|address|pata|location|house|home|flat|school|college|office|workplace|rehta|rehti|rahta|rahti|"
    r"lives?|stays?|living|city|area|colony|mohalla|gali|real\s+name|asli\s+naam|full\s+name|surname|family|parents)(?![a-z0-9])|घर|पता|एड्रेस"
)
_THIRD = re.compile(
    r"(?<![a-z0-9])(?:he|she|they|his|her|their|him|uska|uski|uske|iska|iski|iske|unka|unki|inka|inki|wo|woh|vo|ye|yeh|"
    r"is\s+(?:bande|banday|ladke|ladki|streamer)|streamer\s+(?:ka|ki|ke)|that\s+(?:guy|girl|dude|streamer)|"
    r"this\s+(?:guy|girl|dude|streamer))(?![a-z0-9])|@\w|उसका|इसका|उसकी|इसकी"
)
_PAY_WORD = re.compile(
    r"(?<![a-z0-9])(?:upi|qr|scan|paytm|gpay|phonepe|bhim|payment|donate|donation|bhejo|bhej|send\s+money|refund|cashback)(?![a-z0-9])"
)
_AMOUNT = re.compile(r"₹|\brs\.?\s?\d|\d+\s?(?:rs|rupees|rupay|inr)\b|\b\d{2,6}\b")
_ID_LIKE = re.compile(
    r"(?<![\w.@-])[a-z0-9][a-z0-9._-]{2,40}\s*(?:@|\(at\)|\[at\]|at\s+the\s+rate)\s*[a-z]{2,12}\b(?!\.[a-z])"
)


def detect_suspects(norm: Normalized) -> list[Candidate]:
    """Lenient candidates (score 0.4) for messages worth an LLM look. ``norm`` is the normalised message."""
    text = norm.text
    if len(text) < 6:
        return []
    out: list[Candidate] = []
    second = _SECOND.search(text) is not None
    if second and _VIOLENCE.search(text):
        out.append(Candidate(Category.CHAT_ABUSE, 0.4, "suspect_threat", lenient=True))
    elif second and _SEXUAL.search(text):
        out.append(Candidate(Category.CHAT_ABUSE, 0.4, "suspect_sexual", lenient=True))
    elif second and _NEGATIVE_EVAL.search(text):
        out.append(Candidate(Category.CHAT_ABUSE, 0.4, "suspect_insult", lenient=True))
    elif _GROUP.search(text) and _NEGATIVE_EVAL.search(text):
        out.append(Candidate(Category.CHAT_ABUSE, 0.4, "suspect_identity", lenient=True))
    elif second and _REGIONAL_SCRIPT.search(text) and len(text.split()) >= 2:
        out.append(Candidate(Category.CHAT_ABUSE, 0.4, "suspect_regional", lenient=True))
    link = norm.link_text
    if (_DIGITS_RUN.search(link) and _CONTACT_CUE.search(text)) or (
        _PLACE_CUE.search(text) and (_THIRD.search(text) or "?" in link)
    ):
        out.append(Candidate(Category.CHAT_DOXXING, 0.4, "suspect_dox", lenient=True))
    if (_PAY_WORD.search(text) and _AMOUNT.search(link)) or _ID_LIKE.search(link.lower()):
        out.append(Candidate(Category.CHAT_FAKE_UPI, 0.4, "suspect_payment", lenient=True))
    return out


# --- shapes the rules can state outright ---------------------------------------------------------------------------

_ADDRESSEE = re.compile(
    r"(?<![a-z0-9])(?:tu|tujhe|tujhko|tera|teri|tere|tum|tumhe|tumko|tumhara|tumhari|tumhare|you|u|ur|your|streamer|ninnu|nee|ninna|unna|tore|tui)(?![a-z0-9])|तू|तुझे|तेरा|तेरी|तुम"
)
_HARM = re.compile(
    r"(?<![a-z0-9])(?:maut|marna|maar\w*|marwa\w*|khatam|tamam|goli|chaku|acid|jala\w*|kaat\w*|tod\w*|phod\w*|pel\w*|thok\w*|"
    r"uda\w*|utha\w*|barbaad|bigaad\w*|nipta\w*|nipat\w*|sadak\s+pe|ghus\s+ke|haddi|pasli|kill\w*|murder\w*|stab\w*|"
    r"shoot\w*|burn\w*|beat\w*|hurt\w*|run\s+over|end\s+you|regret|bach\s+(?:nahi|nhi)|bach(?:ega|egi|enge|ne)|sabak|"
    r"sambhal\s+ke|dekh\s+lenge|khatra|see\s+what\s+happens|pada\s+milega|haath\s+se|bura\s+(?:haal|anjaam))(?![a-z0-9])"
)
_FUTURE = re.compile(
    r"(?<![a-z0-9])(?:dunga|dungi|denge|dalunga|dalenge|lunga|lenge|karunga|karenge|jayega|jaayega|jayegi|milega|milegi|"
    r"aayegi|aayega|payega|payegi|hoga|hogi|honi|gonna|will|ll|going\s+to|wanna|hope|should|sikhayenge|chhodenge|chodenge|"
    r"maarenge|dikhaunga|karwaunga|bachega|bachegi|bachenge|happens|yaad\s+rakhna|dekh\s+lena|aa\s+raha|aa\s+rahe)(?![a-z0-9])"
)


def detect_shapes(norm: Normalized) -> list[Candidate]:
    """A threat stated by its shape: someone addressed + a harm word + a future or wish (``hold`` level, 0.8).

    ``mujhe maar mat``, game talk (``clutch``, ``round``) and a harm word with no addressee do not count.
    """
    text = norm.text
    if len(text) < 8 or _HARM.search(text) is None:
        return []
    if _ADDRESSEE.search(text) is None or _FUTURE.search(text) is None:
        return []
    from .context import has_game_cue

    if has_game_cue(text):
        return []
    return [Candidate(Category.CHAT_ABUSE, 0.8, "threat_shape")]
