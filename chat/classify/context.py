"""Context that changes what a lexicon hit means.

A bad word is not always abuse. These helpers read the *structure* around the hit, not a word list:

* ``is_reported``  : the author is quoting, reporting or discussing abuse ("he called me ...", ``"..."`` after
                     "said", "do not say ...", "ban whoever wrote ..."), not aiming it at anyone;
* ``is_banter``    : friendly teasing. Only mild words, and a friendly or praising frame ("bol bhai bc", "abe saale
                     kya khela 😂"). Strong slurs, family abuse, threats, sexual and identity abuse never qualify;
* ``has_game_cue`` : the message is about a game ("round", "clutch", "lobby") and has no real-life cue, so a
                     violent verb is game talk.

All of them work on the lowercase lexicon view (``Normalized.text``) and the case-preserving link view. Nothing here
logs or stores text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Protocol

# --- reported speech ------------------------------------------------------------------------------

_NARRATOR = (
    r"(?:mujhe|mujhko|mujhse|mere\s+ko|meri|mera|mere|usne|usko|isne|unhone|unhe|kisi\s+ne|kisine|koi\s+(?:banda|ladka|user|viewer|insaan)?|"
    r"kuch\s+log|logo?n?\s+ne|ek\s+(?:banda|bande|ladka|ladki|bachha|user|viewer|insaan|aadmi)|jisne|jinhone|jo\s+bhi|"
    r"someone|somebody|some\s+(?:guy|one|dude|kid|people|idiot)|he|she|they|people|a\s+(?:guy|user|viewer|kid|troll)|"
    r"who|whoever|anyone\s+who|i\s+(?:was|got|get)|my\s+\w+|me|"
    r"(?:the\s+)?(?:comment|comments|message|messages|msg|post|reply|dm|chat|title|caption|text|screenshot|video\s+title))"
)
_SAY = (
    r"(?:bola|boli|bole|bolta|bolti|bolte|bol\s+raha|bol\s+rahi|bol\s+rahe|bola\s+tha|kaha|kahi|kaha\s+tha|kahta|kehta|kehti|"
    r"keh\s+raha|kehte|kehta\s+tha|likha|likhi|likhta|likh\s+diya|likh\s+rakha|likhe|said|says|saying|called|calls|calling|"
    r"wrote|writes|written|typed|abused|abusing|cursed|swore|replied|commented|texted|messaged|posted|spammed|"
    r"gaali\s+di|gali\s+di|gaali\s+diya|gali\s+diya|gaali\s+dete|gali\s+dete|gaali\s+dena|gaaliyan|gaaliyaan|"
    r"insulted|insult\s+kiya|shouted|screamed|chillaya|bulaya|bula\s+raha|tag\s+kiya|dm\s+kiya|dm\s+kar)"
)
_REPORTED = re.compile(rf"(?<![a-z0-9]){_NARRATOR}\W+(?:\w+\W+){{0,7}}?{_SAY}(?![a-z0-9])")
# A friend, relative or colleague who said something (past tense only: "dost bola chutiya hai tu").
_NARRATOR_LOOSE = (
    r"(?:dost|bhai|didi|teacher|sir|mom|papa|maa|dad|friend|brother|sister|uncle|aunty|bhabhi|boss|coach|"
    r"classmate|roommate|neighbour|wife|husband|gf|bf)"
)
_SAY_PAST = r"(?:bola|boli|bole|kaha|kahi|likha|likhi|said|called|wrote|typed|abused|cursed|replied)"
_REPORTED_LOOSE = re.compile(rf"(?<![a-z0-9]){_NARRATOR_LOOSE}\W+(?:\w+\W+){{0,3}}?{_SAY_PAST}(?![a-z0-9])")
# Puzzlement about people who write abuse ("pata nahi kyun", "why do people").
_REPORT_WONDER = re.compile(
    r"(?<![a-z0-9])(?:pata\s+nahi\s+(?:kyun|kyu|kyon)|kyun\s+(?:likhte|bolte|karte)|why\s+do\s+(?:people|they)|jaane\s+kyun|na\s+jane\s+kyun)"
)
# "yt comments me log ... likh dete hain": where it happens, then what people write or say.
_REPORT_LOC = re.compile(
    r"(?<![a-z0-9])(?:comments?|chat|dms?|yt|youtube|insta|instagram|twitter|reddit|x)\s+(?:me|mein|pe|par|on|in)\b.{0,25}"
    r"(?<![a-z0-9])(?:log|people|users?|kids?)\s+(?:\w+\s+){0,3}?(?:likh|bol|kah|keh|type|write|say|call|abuse|gaali|gali)"
)
# "do not say it", "gaali mat do", "watch your language": the message argues against abuse.
_ANTI_ABUSE = re.compile(
    r"(?<![a-z0-9])(?:mat|na|nahi|nhi|nai|stop|dont|don'?t|do\s+not|never|please\s+don'?t)\s+(?:\w+\s+){0,3}?"
    r"(?:bolo|bolna|bol|kaho|kehna|bole|say|saying|call|calling|use|likho|likhna|write|dena|do|gaali|gali|abuse)(?![a-z0-9])|"
    r"(?<![a-z0-9])(?:gaali|gali|gaaliyan|abuse|abusing|bad\s+words|language|swearing)\s+(?:mat|nahi|na|nhi|galat|bura|band)(?![a-z0-9])|"
    r"(?<![a-z0-9])watch\s+(?:your|ur)\s+language|(?<![a-z0-9])(?:gaali|gali)\s+dena\s+(?:band|galat|buri)|"
    r"(?<![a-z0-9])(?:bolna|kehna|kahna|likhna)\s+(?:acchi|achhi|sahi|theek)\s+(?:baat\s+)?nahi",
)
# A word named as a word: "the word X", "X ka matlab", "X is a slur".
_META_WORD = re.compile(
    r"(?<![a-z0-9])(?:the\s+word|word\s+(?:is|was)|shabd|iska\s+matlab|ka\s+matlab|is\s+a\s+slur|is\s+an?\s+insult|"
    r"slur\s+hai|gaali\s+hai|gaali\s+hota|gaali\s+hoti|offensive\s+word|bad\s+word)(?![a-z0-9])"
)
_QUOTE = re.compile(
    r'"[^"\n]{2,120}"|“[^”\n]{2,120}”|‘[^’\n]{2,120}’|«[^»\n]{2,120}»|(?<![\w])\'[^\'\n]{3,80}\'(?![\w])|`[^`\n]{2,80}`'
)
_QUOTE_CUE = re.compile(
    r"(?<![a-z0-9])(?:said|says|bola|bole|boli|kaha|likha|likhi|wrote|written|typed|called|comment|comments|chat|word|shabd|"
    r"gaali|gali|abuse|slur|reported|report|message|msg|dm|title|caption|username|naam|spam|spammed|banned|ban|ban\s+karo|"
    r"mod|mods|moderator|screenshot|ss|clip)(?![a-z0-9])"
)


class _Scanner(Protocol):
    def has_hit(self, lexicon_text: str) -> bool: ...


def split_quotes(link_text: str) -> tuple[list[str], str]:
    """The quoted segments of a message and the text with them removed."""
    segments = _QUOTE.findall(link_text)
    if not segments:
        return [], link_text
    return segments, _QUOTE.sub(" ", link_text)


def is_reported(lexicon_text: str, link_text: str, scanner: _Scanner | None = None) -> bool:
    """Quoted, reported or discussed abuse (see the module docstring)."""
    if (
        _REPORTED.search(lexicon_text)
        or _REPORTED_LOOSE.search(lexicon_text)
        or _REPORT_WONDER.search(lexicon_text)
        or _REPORT_LOC.search(lexicon_text)
        or _ANTI_ABUSE.search(lexicon_text)
        or _META_WORD.search(lexicon_text)
    ):
        return True
    if scanner is not None and (
        '"' in link_text
        or "“" in link_text
        or "'" in link_text
        or "‘" in link_text
        or "«" in link_text
        or "`" in link_text
    ):
        segments, outside = split_quotes(link_text)
        if segments and not scanner.has_hit(outside_view(outside)):
            n_outside = len(re.findall(r"\w+", outside))
            if _QUOTE_CUE.search(outside.lower()) or n_outside >= 3:
                return True
    return False


def outside_view(text: str) -> str:
    from .normalize import lexicon_view

    return lexicon_view(text)


# --- friendly banter ----------------------------------------------------------------------------

_PRAISE = re.compile(
    r"(?<![a-z0-9])(?:op|mast|masta|kamaal|kamal|zabardast|zabardust|legend|goat|gg|ggs|wp|shabash|shabaash|sahi|badhiya|badiya|"
    r"awesome|nice|maza|mazaa|best|killer|beast|insane|clutch|headshot|boss|king|gazab|jhakaas|dhasu|love|pyaar|bhai\s+ne|"
    r"kya\s+(?:khela|khelta|khel|game|play|shot|kill|headshot|clutch|baat|scene)|well\s+played|good\s+game|ez|poggers?|pog|"
    r"lit|fire|sick|crazy|unreal|wow|waah|wah|shandaar|bahut\s+accha|ekdum\s+(?:mast|sahi|op))(?![a-z0-9])|[❤💪🔥👏🙌💯😍]"
)
_LAUGH = re.compile(
    r"(?<![a-z0-9])(?:lol+|lmao|lmfao|rofl|ha(?:ha)+h?|he(?:he)+|xd+|hehe+)(?![a-z0-9])|[😂🤣😆😹💀😭😅]"
)
_AFFECT = re.compile(
    r"(?<![a-z0-9])(?:bhai|bhaiya|bhaii|bro|bruh|bhaijaan|yaar|yar|yaaar|dost|dude|boss|mere\s+(?:bhai|yaar|dost|sher)|sher)(?![a-z0-9])"
)
_HOSTILE = re.compile(
    r"(?<![a-z0-9])(?:bekaar|bekar|bakwas|worst|hate|nafrat|trash|garbage|useless|worthless|pathetic|disgusting|uninstall|"
    r"band\s+kar|bandh\s+kar|chup|chod\s+de|nikal|nikalo|bhag|get\s+out|shut\s+up|quit|leave|loser|noob\s+streamer|"
    r"ghatiya|waste|boring|cringe|fake|fraud|chor|dhokha|stop\s+streaming)(?![a-z0-9])"
)
# Words that are teased with between friends. Anything else (slurs, family abuse, sexual words) is not banter.
_JOCULAR = frozenset(
    "saala saale sala kamina kamine kameena kamini kutta kutte harami nikamma nikamme nikammi gadha gadhe ullu bewakoof "
    "bevkoof bevakoof bewkoof chapri bhikari gawar jahil chudail dumbass idiot loser nalayak".split()
)
# Real profanity (and its abbreviations: bc, mc, bsdk, mkc ...) stays a flag even when it is used as praise, as does
# anything that insults someone's family. Only the jocular words above can be teasing.
#: Set to ``True`` to let ``bc`` / ``mc`` / ``mf`` count as teasing too (the Gate 4 set labels such praise as abuse, the
#: blind set as banter; see README "Policy"). Off by default: profanity as praise is held for a moderator.
ALLOW_PROFANE_BANTER = False
_INTERJECTION = frozenset("saala saale sala".split())
_STRONG_JOKE = frozenset("harami kutta kutte nalayak".split())


if ALLOW_PROFANE_BANTER:  # pragma: no cover
    _JOCULAR |= frozenset("bc mc mf".split())
    _INTERJECTION |= frozenset("bc mc mf".split())


def is_banter(
    lexicon_text: str,
    link_text: str,
    entries: Iterable[tuple[str, str, int, bool]],
    directed: bool,
) -> bool:
    """Friendly teasing: mild jocular words only, in a friendly or praising frame.

    ``entries`` are ``(term, kind, severity, has_context_flag)`` of every hit.
    """
    hits = list(entries)
    if not hits:
        return False
    for term, kind, sev, _ in hits:
        if kind != "abuse" or sev >= 3 or term.split(" ")[0] not in _JOCULAR or " " in term.strip():
            return False
    view = f"{lexicon_text} {link_text.lower()}"
    if _HOSTILE.search(view):
        return False
    laugh = _LAUGH.search(view) is not None
    praise = _PRAISE.search(view) is not None
    affect = _AFFECT.search(lexicon_text) is not None
    interjection = all(t in _INTERJECTION for t, *_ in hits)
    strong = any(t in _STRONG_JOKE for t, *_ in hits) or len(hits) > 1
    if strong:  # a real insult word needs more than one friendly marker
        return (laugh or praise) and (affect or (laugh and praise))
    if directed:
        return laugh or praise
    return laugh or praise or (affect and interjection)


# --- game talk ----------------------------------------------------------------------------------

_GAME = re.compile(
    r"(?<![a-z0-9])(?:round|match|game|gg|ggs|rank|ranked|1v1|2v2|1v4|clutch|lobby|zone|squad|scrim|bgmi|valorant|valo|"
    r"free\s*fire|ff|cod|pubg|fortnite|minecraft|map|headshot|spray|loot|drop|rush|push|peek|noob|ez|wp|enemy|enemies|team|peeche|aage|bush|sniper|awm|m416|scar|smg|gun|"
    r"next\s+(?:round|game|match)|this\s+(?:round|game|match)|in\s+(?:game|match|lobby)|duo|trio|tournament|finals?)(?![a-z0-9])"
)
_REAL_LIFE = re.compile(
    r"(?<![a-z0-9])(?:ghar|house|home|family|parents|maa|baap|behen|bhai\s+ko|real\s+life|irl|rl|outside|bahar|address|"
    r"school|college|office|stream\s+band|swat|police|pata|location|jaan|jan\s+se|zinda|zinda|acid|chaku|"
    r"bomb|knife|rape|balatkar|kidnap|utha)(?![a-z0-9])"
)


def has_game_cue(lexicon_text: str) -> bool:
    """Game talk with no real-life cue: a violent verb here is about the game."""
    return _GAME.search(lexicon_text) is not None and _REAL_LIFE.search(lexicon_text) is None
