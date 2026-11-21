"""Spoken-profanity eval transcripts (Hinglish in Latin letters and Devanagari, English, Kannada, Tamil, Telugu,
Bengali) and clean look-alikes. Written for this eval only: own carrier sentences and own word lists, not read from
``guard/risk`` (so a gap in the product lexicon shows up as a miss). The set is transcript-level: an offline English
TTS cannot say these words, and the guard reads the transcript, so each utterance carries recogniser-style words with
exact timings (0.28 to 0.5 s per word, 0.05 s gaps) and the truth of which words are profane.

Severity truth: 2 strong, 1 mild (a word that is rude but common, such as "saala"). Groups: ``hinglish_latn``,
``hinglish_deva``, ``english``, ``regional``, ``variants`` (repeated letters, leetspeak: spec test 5), ``clean`` and
``clean_trap`` (harmless words that contain a profane substring: the Scunthorpe cases).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

LATN_STRONG = [
    "chutiya", "chutiye", "madarchod", "bhenchod", "behenchod", "bhosdike", "bhosadike", "gandu", "randi",
    "lauda", "lund", "bhadwa", "haramzade", "chutiyapa", "bahenchod", "bhosdi",
]  # fmt: skip
LATN_MILD = ["saala", "saale", "kamina", "kamine", "harami", "kutte", "kutta", "bakchod"]
LATN_CARRIERS = [
    "abe {w} ye kya kar raha hai", "{w} stream band kar", "yaar ye game {w} hai bilkul", "tu {w} hai bhai",
    "aaj ka match {w} tha", "oye {w} sun zara", "{w} kahin ke", "bhai tu toh {w} nikla", "kya {w} gameplay hai",
    "mujhe lagta hai ye banda {w} hai", "arre {w} fir se haar gaye", "chal nikal {w} yahan se",
]  # fmt: skip
DEVA_STRONG = ["चूतिया", "मादरचोद", "बहनचोद", "भोसड़ीके", "गांडू", "रंडी", "लौड़े", "चूतिये", "भेनचोद", "हरामज़ादे"]
DEVA_MILD = ["साला", "साले", "कमीना", "हरामी", "कुत्ते"]
DEVA_CARRIERS = [
    "अबे {w} ये क्या कर रहा है", "तू तो {w} है भाई", "{w} स्ट्रीम बंद कर", "आज का मैच {w} था", "ओये {w} सुन ज़रा",
    "{w} कहीं के", "ये बंदा पूरा {w} निकला", "अरे {w} फिर से हार गए",
]  # fmt: skip
EN_STRONG = ["fuck", "fucking", "shit", "bitch", "bastard", "asshole", "motherfucker", "dickhead"]
EN_MILD = ["damn", "crap", "piss"]
EN_CARRIERS = [
    "you are such a {w}", "what the {w} was that", "this game is {w} garbage", "{w} i missed again",
    "oh {w} that was close", "stop being a {w} man",
]  # fmt: skip
# (language tag, native or Latin word, carrier). Informational: the lexicon covers these only partly.
REGIONAL = [
    ("kn", "ಸೂಳೆ", "ನೀನು ಒಬ್ಬ {w} ಅಷ್ಟೇ"), ("kn", "sule maga", "nee obba {w} ashte"), ("kn", "bosdi", "ey {w} ide yenu"),
    ("ta", "ஓத்தா", "டேய் {w} என்ன பண்ற"), ("ta", "otha", "da {w} enna panra"), ("ta", "punda", "yen {w} ippadi pesura"),
    ("te", "లంజ", "నువ్వు ఒక {w} రా"), ("te", "lanja", "ra {w} em chestunnav"), ("te", "dengey", "ey {w} ikkadi nundi po"),
    ("bn", "মাগি", "তুই একটা {w} রে"), ("bn", "magi", "ei {w} ki korcho"), ("bn", "khanki", "oi {w} chup kor"),
]  # fmt: skip
VARIANTS = [  # repeated letters and leetspeak as a recogniser or a streamer might spell them
    ("chuuutiya", 2), ("ch00tiya", 2), ("madarchoooood", 2), ("g4ndu", 2), ("bhosdikeee", 2), ("saaaala", 1),
    ("fuuuck", 2), ("sh1t", 2), ("b1tch", 2), ("kamiiina", 1), ("lauuuda", 2), ("randiii", 2),
]  # fmt: skip

CLEAN_LATN = [
    "aaj ka match bahut accha tha sabko dhanyavaad", "chat mein kisi ne sawaal poocha hai main jawab deta hoon",
    "ye wala gun mujhe bahut pasand hai isliye isse khelunga", "stream ke baad hum ek aur game kheelenge",
    "bhai thoda sa wait karo main settings badal raha hoon", "kal subah jaldi uthna hai isliye jaldi band karenge",
    "mera internet thoda slow chal raha hai aaj", "sabko mera namaste aur welcome stream mein",
    "us team ne bahut accha khela hum seekh rahe hain", "dost ne naya phone liya hai bahut mast hai",
    "chai peene ka time ho gaya hai ek minute", "ye circle bahut chhota hai andar aa jao jaldi",
]  # fmt: skip
CLEAN_DEVA = [
    "आज का मैच बहुत अच्छा था सबको धन्यवाद", "चैट में किसी ने सवाल पूछा है मैं जवाब देता हूँ", "ये वाली गन मुझे बहुत पसंद है",
    "स्ट्रीम के बाद हम एक और गेम खेलेंगे", "भाई थोड़ा सा रुको मैं सेटिंग बदल रहा हूँ", "मेरा इंटरनेट थोड़ा धीमा चल रहा है आज",
    "सबको मेरा नमस्ते और स्ट्रीम में स्वागत है", "चाय पीने का समय हो गया है एक मिनट",
]  # fmt: skip
CLEAN_EN = [
    "welcome back everyone today we play a new match", "thank you for the follow please like and share",
    "this map is very big so stay close to the team", "let us check the chat before we start the round",
    "that was a really good shot nice one", "i need to fix my microphone one second",
]  # fmt: skip
# harmless words with a profane substring (whole-token matching must not fire)
TRAP_WORDS = [
    "chutney", "gandhi", "bhosale", "randhir", "lodha", "choudhary", "assam", "classic", "scunthorpe", "dickens", "passion",
    "assistant", "bassist", "mumbai", "cocktail", "analysis", "shiitake", "grapes", "landmark", "chhatri", "kutumb", "gandhinagar",
]  # fmt: skip
TRAP_CARRIERS = [
    "mujhe {w} bahut pasand hai", "kal hum {w} ke baare mein baat karenge", "yeh {w} wala option try karo",
    "the word {w} is on the screen right now", "{w} ka naam suna hai kya tumne",
]  # fmt: skip


@dataclass
class Utt:
    id: str
    group: str
    lang: str
    text: str
    words: list[tuple[str, float, float]]
    profane: bool
    level: int  # 0 clean, 1 mild, 2 strong
    hit_words: list[int] = field(default_factory=list)  # indices into ``words``
    t_offset: float = 0.0


def _timed(text: str, rng: random.Random) -> list[tuple[str, float, float]]:
    t, out = 0.0, []
    for w in text.split():
        d = rng.uniform(0.28, 0.5)
        out.append((w, round(t, 3), round(t + d, 3)))
        t += d + 0.05
    return out


def _mk(uid: str, group: str, lang: str, text: str, term: str | None, level: int, rng: random.Random) -> Utt:
    words = _timed(text, rng)
    hits: list[int] = []
    if term is not None:
        n = len(term.split())
        toks = [w for w, _, _ in words]
        for i in range(len(toks) - n + 1):
            if " ".join(toks[i : i + n]) == term:
                hits = list(range(i, i + n))
                break
    return Utt(uid, group, lang, text, words, term is not None, level, hits)


def build_utterances(seed: int, scale: float = 1.0) -> list[Utt]:
    rng = random.Random(seed)
    out: list[Utt] = []

    def many(group: str, lang: str, terms: list[tuple[str, int]], carriers: list[str], n: int) -> None:
        seen: set[str] = set()
        k = 0
        while k < n and len(seen) < len(terms) * len(carriers):
            term, lvl = rng.choice(terms)
            text = rng.choice(carriers).format(w=term)
            if text in seen:
                continue
            seen.add(text)
            out.append(_mk(f"p{len(out):04d}", group, lang, text, term, lvl, rng))
            k += 1

    def s(x: int) -> int:
        return max(2, int(x * scale))

    many(
        "hinglish_latn",
        "hi-Latn",
        [(w, 2) for w in LATN_STRONG] + [(w, 1) for w in LATN_MILD],
        LATN_CARRIERS,
        s(90),
    )
    many(
        "hinglish_deva",
        "hi",
        [(w, 2) for w in DEVA_STRONG] + [(w, 1) for w in DEVA_MILD],
        DEVA_CARRIERS,
        s(50),
    )
    many("english", "en", [(w, 2) for w in EN_STRONG] + [(w, 1) for w in EN_MILD], EN_CARRIERS, s(30))
    many("variants", "hi-Latn", list(VARIANTS), LATN_CARRIERS[:6] + EN_CARRIERS[:2], s(24))
    for lang, term, carrier in REGIONAL:
        out.append(_mk(f"p{len(out):04d}", "regional", lang, carrier.format(w=term), term, 2, rng))
    for text in CLEAN_LATN:
        out.append(_mk(f"p{len(out):04d}", "clean", "hi-Latn", text, None, 0, rng))
    for text in CLEAN_DEVA:
        out.append(_mk(f"p{len(out):04d}", "clean", "hi", text, None, 0, rng))
    for text in CLEAN_EN:
        out.append(_mk(f"p{len(out):04d}", "clean", "en", text, None, 0, rng))
    for i, w in enumerate(TRAP_WORDS):
        out.append(
            _mk(f"p{len(out):04d}", "clean_trap", "hi-Latn", TRAP_CARRIERS[i % 5].format(w=w), None, 0, rng)
        )
    for i, u in enumerate(out):
        u.t_offset = (
            30.0 * i
        )  # stream-time gap between utterances: the per-severity rate limit never couples them
    return out
