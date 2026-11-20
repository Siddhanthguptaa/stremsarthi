"""Normalisation (spec case 2) and lexicon rules (case 1). SPECIMEN test strings only."""

from __future__ import annotations

import pytest

from chat.classify import default_matcher, normalize
from chat.classify.normalize import transliterate_devanagari

M = default_matcher()


def score(text: str):
    return M.match(normalize(text))


# --- case 2: normalisation ----------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["m.c", "m c", "m*c", "m_c", "m-c", "M.C", "ｍｃ", "m​c", "mmmc"])
def test_separators_and_width_tricks_match_plain_form(variant: str) -> None:
    assert normalize(variant).text == normalize("mc").text or score(variant).matched
    assert score(variant).kind == score("mc").kind == "abuse"


@pytest.mark.parametrize(
    "variant",
    ["g4ndu", "gand​u", "gaaaandu", "g a n d u", "g.a.n.d.u", "ɡandu", "gаndu", "GANDU", "gandu!!!"],
)
def test_obfuscated_gandu_matches(variant: str) -> None:
    r = score(variant)
    assert r.matched and r.severity == 2, variant


@pytest.mark.parametrize(
    "variant",
    ["bh3nchod", "b h e n c h o d", "bhenchodddd", "bhen​chod", "ｂｈｅｎｃｈｏｄ", "b.h.e.n.c.h.o.d"],
)
def test_obfuscated_substring_term_matches(variant: str) -> None:
    assert score(variant).severity == 3, variant


def test_star_wildcard_inside_a_word() -> None:
    assert score("ch*tiya").matched
    assert score("m*darchod").matched


def test_numbers_and_mentions_are_not_leet_mangled() -> None:
    n = normalize("call 9876543210 @streamer 2024 100%")
    assert "9876543210" in n.text and "2024" in n.text and "@streamer" in n.text
    assert n.link_text == "call 9876543210 @streamer 2024 100%"


def test_raw_text_is_kept_untouched() -> None:
    raw = "M.C ​ g4ndu"
    assert normalize(raw).raw == raw


def test_devanagari_kept_and_transliterated() -> None:
    n = normalize("तू चूतिया है")
    assert "चूतिया" in n.text and n.roman == "tu chutiya hai"
    assert transliterate_devanagari("भोसडीके") == "bhosdike"
    assert transliterate_devanagari("गांडू") == "gandu"


# --- case 1: lexicon coverage ---------------------------------------------------------------------

HINGLISH_LATIN = [
    "tu madarchod hai",
    "abe bhenchod",
    "behenchod kahin ka",
    "bhosdike chup kar",
    "chutiya hai tu",
    "chutiye log",
    "gandu streamer",
    "tera gaand fat gayi",
    "randi ka bacha",
    "harami saala",
    "haramkhor hai tu",
    "kutta hai tu",
    "bakchod mat kar",
    "bsdk stream band kar",
    "bkl kahin ka",
    "mkc kya khel raha hai",
    "lund hai tu",
    "lauda hai ye",
    "bhadwa hai tu",
    "jhatu streamer",
    "chinal hai ye",
    "kamina insaan",
    "tmkc",
    "teri maa ki",
    "fuck you streamer",
    "asshole stream",
    "bitch please",
    "nigger",
    "motherfucker",
    "kys loser",
]
DEVANAGARI = [
    "मादरचोद",
    "तू बहनचोद है",
    "भेनचोद",
    "चूतिया स्ट्रीमर",
    "गांडू कहीं का",
    "रंडी की औलाद",
    "हरामी कुत्ता",
    "भोसड़ीके चुप",
    "लंड है तू",
    "बकचोद मत कर",
]
REGIONAL = [
    "otha da",
    "punda mavane",
    "thevidiya payale",
    "lanja kodaka",
    "munda nuvvu",
    "sule maga",
    "bosudi",
    "khanki magi",
    "ஓத்தா",
    "புண்டை",
    "தேவடியா",
    "లంజ",
    "ముండ",
    "ಸೂಳೆ ಮಗ",
    "ಬೋಸುಡಿ",
    "মাগি",
    "খানকি",
    "চোদনা",
    "চুদি",
    "bokachoda",
    "ottha",
]
THREATS = [
    "maar dunga tujhe",
    "jaan se maar dunga",
    "kaat dunga",
    "i will kill you",
    "मार दूंगा",
    "जान से मार दूंगा",
]


@pytest.mark.parametrize("text", HINGLISH_LATIN)
def test_hinglish_latin_abuse_is_flagged(text: str) -> None:
    r = score(text)
    assert r.matched and r.kind in {"abuse", "threat"}, text
    assert r.score >= 0.5


@pytest.mark.parametrize("text", DEVANAGARI)
def test_devanagari_abuse_is_flagged(text: str) -> None:
    assert score(text).matched, text


@pytest.mark.parametrize("text", REGIONAL)
def test_regional_abuse_is_flagged(text: str) -> None:
    assert score(text).matched, text


@pytest.mark.parametrize("text", THREATS)
def test_threats_are_flagged_severe(text: str) -> None:
    r = score(text)
    assert r.kind == "threat" and r.severity == 3 and r.score >= 0.9


def test_sexual_harassment_terms() -> None:
    assert score("rapist").kind == "sexual" and score("rapist").severity == 3
    assert score("send nudes").matched


@pytest.mark.parametrize(
    "text",
    [
        "mc stan ka gana lagao",
        "mc donalds ka ad",
        "chaudhary sir ne bola",
        "bhosale sir aaye",
        "gandhi jayanti",
        "gandhinagar se hu",
        "randhir bhai",
        "chhutti hai aaj",
        "chutney achi thi",
        "chutki baja ke dekh",
        "hello bhai nice stream",
        "bhai kya gameplay hai",
        "chod na bhai jaane de",
        "bhai address bar me type karo",
        "आज का स्ट्रीम बहुत मस्त था",
        "छुट्टी है आज",
        "வணக்கம் அண்ணா",
        "నమస్కారం అన్న",
        "ನಮಸ್ಕಾರ ಅಣ್ಣ",
        "নমস্কার দাদা",
    ],
)
def test_benign_words_with_abusive_substrings_are_allowed(text: str) -> None:
    assert not score(text).matched, text


def test_scoring_rules() -> None:
    assert score("saala").score == 0.5  # severity 1
    assert score("chutiya").score == 0.75  # severity 2
    assert score("madarchod").score == 0.95  # severity 3
    assert score("chutiya harami").score == 0.85  # 0.75 + 0.1 per extra distinct hit
    assert score("tu chutiya").score == 0.8  # +0.05 streamer-directed
    assert score("@streamer chutiya").score == 0.8
    assert score("madarchod bhenchod harami chutiya kutta").score == 1.0  # capped
