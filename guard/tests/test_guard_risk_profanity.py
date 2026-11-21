"""T5.2 profanity: normalisation, matcher, stream adapter, precision/recall on hand-written phrasings.

SYNTHETIC: the phrasings below were written by the same author as the lexicon, so recall is an
optimistic upper bound on real streamer speech. They are not a real-world benchmark."""

from __future__ import annotations

import dataclasses
import logging
import time

import pytest

from contracts.enums import Category
from contracts.speech import Transcript, Word
from guard.risk.lexicon_data import BENIGN_ALLOW, DEVANAGARI, ENGLISH, LATIN_HINGLISH, REGIONAL
from guard.risk.profanity import ProfanityDetector, ProfanityMatcher, normalize_token

# ----------------------------------------------------------------------------- corpora
HINGLISH_POS = [
    "abe tu chutiya hai kya",
    "teri maa ki chut",
    "madarchod ne phir se game kharab kar diya",
    "yaar ye bhenchod lag gaya",
    "saale gandu mat bol",
    "bhosdike kahan ghus gaya",
    "chuuutiya hai tu",
    "ch00tiya mat ban",
    "mdrchod sab ke sab",
    "bsdk chup kar",
    "m4darchod",
    "bhen chod ye kya kiya",
    "madar chod hai ye",
    "tu ek number ka lund hai",
    "lauda lag gaya mera",
    "randi ka bachcha",
    "gaaaandu log",
    "teri gand maar dunga",
    "bhadwa hai wo",
    "haramzade sab ke sab",
    "kutiya kahin ki",
    "mkc kya kar raha hai",
    "tum log chutiye ho",
    "bhosdi wale",
    "arre jhaant ke baal",
    "ye chudai nahi chalegi",
    "behnchod seedha chal",
    "bkl phir se aa gaya",
    "gandu hai poora",
    "b.s.d.k kya bol raha hai",
]
DEVANAGARI_POS = [
    "तू चूतिया है क्या",
    "मादरचोद ने गेम खराब कर दिया",
    "भेनचोद क्या कर रहा है",
    "अबे गांडू",
    "भोसड़ी के कहाँ गया",
    "रंडी की औलाद",
    "लंड मत बोल",
    "चुतिया बना दिया",
    "मादर चोद",
    "गाँडू लोग",
    "चूत मत बोल यार",
    "भड़वा है वो",
    "कुतिया कहीं की",
    "भैन चोद",
    "बहनचोद सुन",
]
ENGLISH_POS = [
    "what the fuck is this",
    "you are such a bitch",
    "this is bullshit man",
    "holy shit",
    "f*ck this game",
    "fuuuck",
    "sh1t happens",
    "a$$hole move",
    "stop being a dick",
    "that bastard camped",
    "motherfucker killed me",
    "you piece of shit",
    "what a cunt",
    "shut the fck up",
    "fucking noob",
]
REGIONAL_POS = [
    "ಏ ಸೂಳೆ ಮಗನೇ",
    "bolimaga yaar",
    "sule maga",
    "ತುನ್ನೆ ಮಗನೇ",
    "ei pundai",
    "தேவிடியா மகனே",
    "ommala da",
    "lanja kodaka",
    "ఏరా లంజ",
    "మొడ్డ",
    "dengey ra",
    "pooku",
    "শালা চুদবো",
    "মাগি",
    "khanki",
    "বোকাচোদা",
]
MILD_POS = [
    "kya bakchodi hai ye",
    "saala kutta",
    "kamina hai wo",
    "oh crap",
    "damn that was close",
    "pissed me off",
    "साला कमीना",
    "bc kya game hai",
    "loosu da",
]
SLUR_POS = ["you faggot", "oye chinki", "katua kahin ka"]

NEGATIVES = [
    "chod do yaar ye game",
    "mujhe chhod ke mat jao",
    "mc stan ka gaana sunao",
    "maggi bana do",
    "tune kya kiya",
    "moby dick is a novel",
    "assam tea garden",
    "class ke baad milte hain",
    "scunthorpe is a town in england",
    "cockpit mein baitha hu",
    "the cocktail party",
    "gandhi ji ki jayanti",
    "gandhinagar jaana hai",
    "chutney achi hai",
    "chutki mein kaam ho jayega",
    "bhosale sahab aaye",
    "mumbai se hoon",
    "hello everyone welcome to the stream",
    "aaj ka stream bahut mast hoga",
    "pakki baat hai",
    "chakka maar diya",
    "kaise ho aap sab",
    "subscribe karo channel ko",
    "like and share",
    "ye gun bahut powerful hai",
    "sniper lekar aao",
    "doctor ne dawai di",
    "namaste doston",
    "assassin creed khelte hain",
    "the analysis was thorough",
    "bassist played well",
    "passage clear hai",
    "compass dekho",
    "document bhej do",
    "dickens wrote great novels",
    "lundberg family",
    "pussycat dolls",
    "shiitake mushroom recipe",
    "title screen aa gayi",
    "வணக்கம் நண்பர்களே",
    "தமிழ் படம் பார்த்தேன்",
    "నమస్తే మిత్రులారా",
    "ಎಲ್ಲರಿಗೂ ನಮಸ್ಕಾರ",
    "সবাইকে নমস্কার",
    "आज का स्ट्रीम बहुत अच्छा था",
    "छूट मिल रही है सेल में",
    "छोड़ दो यार",
    "मुंबई जा रहा हूँ",
    "गांधी जी",
    "गाँव में रहता हूँ",
    "hola amigos como estan",
    "pass the salt please",
    "ek do teen char paanch",
    "dick van dyke show dekha",
    "emcee ne announce kiya",
    "aaj ka match bahut accha tha",
    "bhai ye kaise kiya tune",
    "gun ko reload karo",
    "mera naam rahul hai",
    "thanks for the gift",
    "kal milte hain",
    "game start karte hain",
    "mic check one two",
]


def _hits(text: str) -> list[int]:
    d = ProfanityDetector(wall_ts_fn=lambda _t: "t")
    words = [(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate(text.split())]
    return [int(s.label[-1]) for s in d.feed(words)]


def _recall(corpus: list[str], min_sev: int = 1) -> tuple[float, list[str]]:
    miss = [t for t in corpus if not any(s >= min_sev for s in _hits(t))]
    return 1 - len(miss) / len(corpus), miss


# ----------------------------------------------------------------------------- normalisation
@pytest.mark.parametrize(
    ("raw", "norm"),
    [
        ("Chutiyaaaa!", "chutiya"),
        ("CH00TIYA", "chutiya"),
        ("f*ck", "fck"),
        ("a$$hole", "ashole"),
        ("B.S.D.K", "bsdk"),
        ("sh1t,", "shit"),
        ("...", ""),
        ("123456", "123456"),
        ("​madar‍chod", "madarchod"),
    ],
)
def test_normalize_latin(raw: str, norm: str) -> None:
    assert normalize_token(raw) == norm


def test_normalize_devanagari_folds_nukta_vowel_length_and_chandrabindu() -> None:
    assert normalize_token("भोसड़ी") == normalize_token("भोसडी")
    assert normalize_token("गाँडू") == normalize_token("गांडु")
    assert normalize_token("चूतिया") == normalize_token("चुतिया")


# ----------------------------------------------------------------------------- precision / recall
def test_hinglish_latin_recall_at_least_85() -> None:
    rec, miss = _recall(HINGLISH_POS, 2)
    assert rec >= 0.85, miss


def test_devanagari_recall_at_least_85() -> None:
    rec, miss = _recall(DEVANAGARI_POS, 2)
    assert rec >= 0.85, miss


def test_english_recall_at_least_85() -> None:
    rec, miss = _recall(ENGLISH_POS, 2)
    assert rec >= 0.85, miss


def test_regional_recall_at_least_85() -> None:
    rec, miss = _recall(REGIONAL_POS, 1)
    assert rec >= 0.85, miss


def test_mild_and_slur_lists_hit_with_right_severity() -> None:
    assert all(max(_hits(t), default=0) == 1 for t in MILD_POS)
    assert all(max(_hits(t), default=0) == 3 for t in SLUR_POS)


def test_negatives_and_benign_allow_list_give_zero_hits() -> None:
    bad = [t for t in NEGATIVES if _hits(t)]
    assert bad == []
    for phrase in BENIGN_ALLOW:
        assert _hits(phrase) == [], phrase


def test_overall_precision_and_recall_numbers() -> None:
    pos = HINGLISH_POS + DEVANAGARI_POS + ENGLISH_POS + REGIONAL_POS + MILD_POS + SLUR_POS
    tp = sum(1 for t in pos if _hits(t))
    fp = sum(1 for t in NEGATIVES if _hits(t))
    precision = tp / (tp + fp)
    recall = tp / len(pos)
    print(
        f"profanity synthetic: n_pos={len(pos)} n_neg={len(NEGATIVES)} precision={precision:.3f} recall={recall:.3f}"
    )
    assert precision >= 0.95 and recall >= 0.85


def test_lexicon_has_no_empty_or_duplicate_conflicts() -> None:
    entries = (*LATIN_HINGLISH, *DEVANAGARI, *ENGLISH, *REGIONAL)
    assert all(sev in (1, 2, 3) and term.strip() for term, sev in entries)
    m = ProfanityMatcher()
    for phrase in BENIGN_ALLOW:
        assert m.match([normalize_token(w) for w in phrase.split()]) == []


def test_language_not_in_lexicon_gives_no_signal() -> None:
    assert _hits("hijo de puta que haces") == []
    assert _hits("verdammte scheisse nochmal") == []


# ----------------------------------------------------------------------------- stream adapter
def _det() -> ProfanityDetector:
    return ProfanityDetector(wall_ts_fn=lambda t: f"w{t}")


def test_signal_shape_for_each_severity() -> None:
    d = _det()
    (l2,) = d.feed([("chutiya", 1.0, 1.4)])
    assert (l2.source, l2.score, l2.ttl_s, l2.label, l2.category) == (
        "profanity",
        0.6,
        30.0,
        "profanity:L2",
        Category.PROFANITY,
    )
    assert l2.pts == 1.0
    (l1,) = d.feed([("saala", 5.0, 5.3)])
    assert (l1.source, l1.score, l1.label) == ("profanity", 0.3, "profanity:L1")
    (l3,) = d.feed([("faggot", 9.0, 9.4)])
    assert (l3.source, l3.score, l3.ttl_s, l3.label) == ("slur", 0.9, 120.0, "slur:L3")


def test_phrase_split_across_segments_matches_when_last_word_arrives() -> None:
    d = _det()
    assert d.feed([("madar", 1.0, 1.3)]) == []
    (sig,) = d.feed([("chod", 1.4, 1.8)])
    assert sig.label == "profanity:L2" and sig.pts == 1.0


def test_overlap_reemission_is_reported_once() -> None:
    d = _det()
    first = d.feed([("yaar", 0.5, 0.8), ("bhosdike", 1.0, 1.5)])
    again = d.feed([("bhosdike", 1.05, 1.5), ("kya", 1.6, 1.8)])  # 0.4 s overlap re-emits the word
    assert len(first) == 1 and again == []


def test_rate_limit_one_per_severity_per_2s() -> None:
    d = _det()
    out = d.feed([("chutiya", 1.0, 1.3), ("gandu", 1.6, 1.9), ("randi", 2.5, 2.8)])
    assert len(out) == 1  # 1.0 passes, 1.6 and 2.5 are inside 2 s of 1.0
    out = d.feed([("lund", 3.2, 3.5)])
    assert len(out) == 1
    # a different severity is limited independently
    out = d.feed([("saala", 3.3, 3.6)])
    assert len(out) == 1


def test_disabled_gives_nothing() -> None:
    d = ProfanityDetector(enabled=False)
    assert d.feed([("chutiya", 1.0, 1.3)]) == []


def test_transcript_with_and_without_word_times() -> None:
    d = _det()
    words = (Word("tu", 0.0, 0.2), Word("chutiya", 0.3, 0.8))
    tr = Transcript(0.0, 1.0, "tu chutiya", words, "hi", "local", 0.2)
    assert len(d.feed_transcript(tr)) == 1
    d2 = _det()
    tr2 = Transcript(10.0, 12.0, "bhenchod kya hai", (), "hi", "local", 0.2)
    (sig,) = d2.feed_transcript(tr2)
    assert 10.0 <= sig.pts <= 12.0


def test_window_expires_old_words() -> None:
    d = _det()
    d.feed([("madar", 1.0, 1.3)])
    assert d.feed([("chod", 20.0, 20.4)]) == []  # too far apart to be one word


def test_latency_per_transcript_is_small() -> None:
    d = _det()
    words = [
        (w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate("abe tu chutiya hai kya bol raha hai yaar".split())
    ]
    t0 = time.perf_counter()
    for k in range(200):
        d.feed([(w, a + k * 10, b + k * 10) for w, a, b in words])
    per_ms = (time.perf_counter() - t0) / 200 * 1000
    print(f"profanity feed latency: {per_ms:.3f} ms per 9-word segment")
    assert per_ms < 5.0


# ----------------------------------------------------------------------------- privacy
def test_signals_and_logs_contain_no_transcript_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    d = ProfanityDetector()
    secret_terms = ["chutiya", "madarchod", "motherfucker", "faggot", "bhosdike"]
    sigs = []
    for i, term in enumerate(secret_terms):
        sigs += d.feed([(term, i * 5.0, i * 5.0 + 0.4)])
    assert len(sigs) == len(secret_terms)
    dumped = " ".join(repr(s) + str(dataclasses.asdict(s)) for s in sigs)
    for term in secret_terms:
        assert term not in dumped
    assert all(term not in r.getMessage() for r in caplog.records for term in secret_terms)


# ----------------------------------------------------------------------------- spelling families
FAMILY_POS = [
    "bahenchod",
    "bahen chod",
    "behenchod",
    "behnchod",
    "bhenchod",
    "benchod",
    "bhainchod",
    "bhnchod",
    "bahenchodd",
    "baheenchod",
    "bhenchud",
    "bahanchod",
    "madarchod",
    "maderchod",
    "madrchod",
    "maadarchod",
    "madharchod",
    "madarchood",
    "mdrchod",
    "madar chod",
    "bhosdike",
    "bhosadike",
    "bosdike",
    "bhosdiwale",
    "bhosri",
    "bhosda",
    "chutiya",
    "chotiya",
    "chootiya",
    "chutiyapa",
    "chutiye",
    "chuutiyaa",
]
FAMILY_NEG = [
    "chod",
    "chod do yaar",
    "chodna mat",
    "bahen",
    "bahen ko call karo",
    "behen ki shaadi hai",
    "bahen chod do",
    "bhen de do",
    "bench",
    "bhosale",
    "bhosle",
    "bosch",
    "madar",
    "madam",
    "madras",
    "mader",
    "chhod",
    "chhodo",
    "chodu bhagat",
    "chuti",
    "chhutti mil gayi",
    "chotti behen",
    "choti si baat",
    "bhaiya",
    "bhai",
    "benzene",
    "maharaj",
    "madhur",
    "chutney",
]


@pytest.mark.parametrize("text", FAMILY_POS)
def test_family_spellings_hit_l2(text: str) -> None:
    assert _hits(text) and max(_hits(text)) == 2, text


@pytest.mark.parametrize("text", [t for t in FAMILY_NEG if t != "chodu bhagat"])
def test_family_lookalikes_stay_out(text: str) -> None:
    assert _hits(text) == [], text
