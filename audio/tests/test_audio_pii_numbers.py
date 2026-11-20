"""T3.3: normalisation and the number-word table (docs/specs/audio.md 5.5). Synthetic phrases only."""

from __future__ import annotations

import pytest

from audio.pii.numbers import (
    Tok,
    digit_value,
    join_single_letters,
    load_vocab,
    normalise_text,
    number_runs,
    to_digits,
    tokenize,
)
from contracts.speech import Word

SCRIPTS = {
    "devanagari": "०१२३४५६७८९",
    "bengali": "০১২৩৪৫৬৭৮৯",
    "tamil": "௦௧௨௩௪௫௬௭௮௯",
    "telugu": "౦౧౨౩౪౫౬౭౮౯",
    "kannada": "೦೧೨೩೪೫೬೭೮೯",
}


@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_script_digits_map_to_ascii(script):
    assert normalise_text(SCRIPTS[script]).strip() == "0123456789"


def test_normalise_strips_punctuation_and_case():
    assert normalise_text("  Hello, WORLD!! ").split() == ["hello", "world"]
    assert normalise_text("९८७६-५४३२१०").split() == ["9876", "543210"]


def _w(text: str, i: int) -> Word:
    return Word(text, i * 0.3, i * 0.3 + 0.25)


def test_single_letters_join_into_a_handle():
    toks = join_single_letters(tokenize([_w(t, i) for i, t in enumerate("ok o k s b i".split())]))
    assert [t.text for t in toks] == ["ok", "oksbi"]
    toks = join_single_letters(tokenize([_w(t, i) for i, t in enumerate("pay t m".split())]))
    assert [t.text for t in toks] == ["pay", "tm"]


# (digit, every spelling in the table of 5.5: Hindi/English roman and script, Kannada, Tamil, Telugu, Bengali)
TABLE = {
    "0": "zero oh shunya ज़ीरो शून्य sonne ಸೊನ್ನೆ poojyam பூஜ்யம் sunna సున్నా shunyo শূন্য",
    "1": "one ek एक ondu ಒಂದು onru ஒன்று okati ఒకటి এক",
    "2": "two do दो eradu ಎರಡು irandu இரண்டு rendu రెండు dui দুই",
    "3": "three teen तीन mooru ಮೂರು moondru மூன்று moodu మూడు tin তিন",
    "4": "four char chaar चार naalku ನಾಲ್ಕು naangu நான்கு naalugu నాలుగు চার",
    "5": "five paanch panch पांच पाँच aidu ಐದು ainthu ஐந்து ఐదు পাঁচ",
    "6": "six chhe chhah che छह छः छे aaru ಆರು ஆறு ఆరు choy ছয়",
    "7": "seven saat sat सात elu ಏಳು ezhu ஏழு edu ఏడు shat সাত",
    "8": "eight aath ath आठ entu ಎಂಟು ettu எட்டு enimidi ఎనిమిది aat আট",
    "9": "nine nau नौ ombattu ಒಂಬತ್ತು onbathu ஒன்பது tommidi తొమ్మిది noy নয়",
}


@pytest.mark.parametrize("digit", sorted(TABLE))
def test_number_table_row(digit):
    for spelling in TABLE[digit].split():
        tok = normalise_text(spelling).strip()
        assert digit_value(tok) == digit, spelling


def test_double_triple_and_country_code():
    assert to_digits("double nine eight") == "998"
    assert to_digits("triple eight") == "888"
    assert to_digits("ट्रिपल आठ") == "888"
    assert to_digits("dabal nau ek") == "991"
    assert to_digits("plus nine one nine eight") == "9198"
    assert to_digits("plus ninety one nine eight") == "9198"
    assert to_digits("hello world") is None


def test_compound_words_keep_run_contiguous_without_digits():
    toks = [Tok(t, i * 0.3, i * 0.3 + 0.2, False, 0, i, i) for i, t in enumerate("ek sau teen".split())]
    runs = number_runs(toks)
    assert len(runs) == 1 and runs[0].digits == "13" and (runs[0].tok0, runs[0].tok1) == (0, 2)


def test_run_breaks_at_a_pause_of_one_second():
    toks = [
        Tok(t, t0, t0 + 0.2, False, 0, i, i)
        for i, (t, t0) in enumerate([("nau", 0.0), ("ek", 0.5), ("do", 1.7)])
    ]
    assert [r.digits for r in number_runs(toks)] == ["91", "2"]


def test_vocab_covers_spec_handles_and_cues():
    v = load_vocab()
    assert {"oksbi", "okhdfcbank", "ybl", "paytm"} <= v.handles
    assert ("mera", "number") in v.cue_generic and ("ghar", "ka", "pata") in v.cue_address_open
