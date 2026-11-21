"""T3.3 (Gate 3 fixes): decide early. Digit runs, spelt-out ids and UPI ids are muted from their shape,
not only from a cue or a complete number. Own phrasings, synthetic and clearly fake."""

from __future__ import annotations

import asyncio

import pytest

from audio.orchestrator.mute import AT_WORDS, Carry, SpanPlanner, is_letter_word
from audio.pii.detector import SpokenPiiDetector
from audio.pii.numbers import id_runs, number_runs, prepare
from audio.pii.rules import SpokenRules
from contracts.enums import Category
from contracts.speech import TranscriptWindow, Word

STEP = 0.4


def words(text: str, t0: float = 0.0, step: float = STEP, seg_id: int = 1) -> list[Word]:
    return [
        Word(t, t0 + i * step, t0 + i * step + step * 0.8, False, seg_id) for i, t in enumerate(text.split())
    ]


def win(text: str, **kw) -> TranscriptWindow:
    ws = words(text, **kw)
    return TranscriptWindow(tuple(ws), ws[0].t0, ws[-1].t1, 0)


def spans(text: str):
    return asyncio.run(SpokenPiiDetector(SpokenRules()).detect(win(text)))


def covered_words(text: str) -> list[str]:
    """Words whose time lies inside a returned span."""
    w = win(text)
    out = asyncio.run(SpokenPiiDetector(SpokenRules()).detect(w))
    return [
        x.text for x in w.words if any(s.pts_start - 1e-6 <= x.t0 and x.t1 <= s.pts_end + 1e-6 for s in out)
    ]


# ------------------------------------------------------------------ digit runs without a cue
def test_three_single_digits_are_muted_without_any_cue():
    assert covered_words("bhai teen saat nau yaad rakhna") == ["teen", "saat", "nau"]
    assert covered_words("it was five eight two") == ["five", "eight", "two"]


def test_two_digits_without_a_cue_pass():
    assert spans("score is three seven now") == []


def test_counting_up_or_down_is_not_a_code():
    assert spans("one two three four let us go") == []
    assert spans("five four three two one start") == []


def test_quantities_with_sau_or_hazaar_are_not_codes():
    assert spans("chaar sau pachas rupaye") == []
    assert spans("do hazaar paanch") == []


def test_multi_digit_numerals_alone_do_not_trigger_the_run_rule():
    assert spans("price is 250 and 40") == []


def test_a_misheard_digit_does_not_split_the_run():
    # "kya" stands where a digit was lost: one run of five slots, not two short runs
    runs = number_runs(prepare(words("nau kya aath saat chhe")))
    assert len(runs) == 1 and runs[0].digits == "9?876"
    assert covered_words("nau kya aath saat chhe") == ["nau", "kya", "aath", "saat", "chhe"]


def test_connectors_are_not_bridged():
    runs = number_runs(prepare(words("teen aur char")))
    assert [r.digits for r in runs] == ["3", "4"]


def test_a_cue_lets_two_digits_through():
    assert covered_words("otp hai four two")[-2:] == ["four", "two"]


def test_cue_in_other_scripts_opens_the_window():
    # Kannada "code" and "ottu" are cues; a 4-digit numeral after them is muted
    assert [s.category for s in spans("ಕೋಡ್ 4821")] == [Category.SPOKEN_OTHER_PII]


# ------------------------------------------------------------------ spelt-out ids
def test_spelt_letters_and_digits_are_one_run():
    toks = prepare(words("k a v i t a four two"))
    (r,) = id_runs(toks)
    assert r.letters >= 6 and r.digits == 2


def test_lone_a_or_i_do_not_make_a_spelt_id():
    assert all(r.letters < 2 for r in id_runs(prepare(words("i do two"))))
    assert spans("i do not know") == []


def test_spelt_id_is_muted_before_the_handle_is_heard():
    got = spans("p r i y a seven seven")
    assert len(got) == 1 and got[0].category is Category.SPOKEN_OTHER_PII and got[0].pts_start == 0.0


def test_long_spelt_upi_id_is_muted_from_its_first_word():
    text = "mera upi id hai anil d seven e two c f at the rate of oksbi"
    w = win(text)
    (s,) = [
        x
        for x in asyncio.run(SpokenPiiDetector(SpokenRules()).detect(w))
        if x.category is Category.SPOKEN_UPI
    ]
    first = next(x for x in w.words if x.text == "anil")
    last = w.words[-1]
    assert s.pts_start == pytest.approx(first.t0) and s.pts_end == pytest.approx(last.t1)


def test_marker_with_one_misheard_word_still_finds_a_known_handle():
    got = spans("rohit b three at the kya of ybl")
    assert [s.category for s in got] == [Category.SPOKEN_UPI]


def test_marker_without_the_leading_at_needs_a_known_handle():
    assert [s.category for s in spans("rohit nine the rate of okaxis")] == [Category.SPOKEN_UPI]
    assert spans("the rate of interest is high") == []


def test_a_fuzzy_marker_with_an_unknown_handle_needs_a_spelt_name():
    assert spans("meet at the corner of market") == []


# ------------------------------------------------------------------ addresses
def test_address_region_follows_the_cue_not_the_cue_itself():
    w = win("my address is house number four two river view apartments sector nine")
    (s,) = asyncio.run(SpokenPiiDetector(SpokenRules()).detect(w))
    cue = {x.text: x for x in w.words}
    assert s.pts_start >= cue["is"].t1 - 1e-6  # "my address is" stays audible
    assert s.pts_end == pytest.approx(w.words[-1].t1)


def test_a_full_address_is_one_region_even_when_longer_than_twelve_tokens():
    text = "ghar ka pata flat number four two a b c apartments sector nine near the lake road pincode four zero zero zero zero one"
    w = win(text)
    out = asyncio.run(SpokenPiiDetector(SpokenRules()).detect(w))
    assert any(s.pts_end >= w.words[-1].t1 - 1e-6 for s in out)


def test_devanagari_address_words_are_cues():
    assert [s.category for s in spans("फ्लैट नंबर तीन सेक्टर पांच")] == [Category.SPOKEN_ADDRESS]


def test_bare_pata_in_ordinary_speech_does_not_mute():
    assert spans("mujhe pata nahi kya hua tha") == []


# ------------------------------------------------------------------ carry across segments
def test_carry_of_a_spelt_id_continues_with_letters_marker_and_handle():
    ws = [
        Word("c", 1.4, 1.6, False, 1),
        Word("f", 1.7, 1.9, False, 2),
        Word("at", 2.0, 2.2, False, 2),
        Word("the", 2.25, 2.4, False, 2),
        Word("rate", 2.45, 2.7, False, 2),
        Word("of", 2.75, 2.9, False, 2),
        Word("somehandle", 2.95, 3.4, False, 2),
        Word("phir", 3.5, 3.8, False, 2),
    ]
    win_ = TranscriptWindow(tuple(ws), 1.4, 3.8, 1)
    (s,) = SpanPlanner().leading_numbers(Carry(1.6, Category.SPOKEN_UPI, 0.6, "id"), win_)
    assert s.pts_start == pytest.approx(1.7) and s.pts_end == pytest.approx(3.4)
    # a number carry does not swallow letters or markers
    assert SpanPlanner().leading_numbers(Carry(1.6, Category.SPOKEN_PHONE, 0.9), win_) == []


def test_span_ending_on_the_marker_waits_for_the_handle():
    ws = [Word("d", 1.0, 1.2, False, 1), Word("e", 1.3, 1.5, False, 1), Word("at", 1.6, 1.8, False, 1)]
    from contracts.speech import PiiSpan

    (p,) = SpanPlanner().plan(
        [PiiSpan(Category.SPOKEN_UPI, 1.0, 1.8, 0.6, "r", "word", False)],
        TranscriptWindow(tuple(ws), 1.0, 1.8, 0),
        {},
        newest_end=1.85,
    )
    assert p.tail_extended and p.carry_kind == "id"


def test_letter_word_helper_and_marker_words():
    assert is_letter_word("D") and not is_letter_word("do") and not is_letter_word("5")
    assert {"at", "rate", "of"} <= AT_WORDS


# ------------------------------------------------------------------ red-team fixes
def test_letter_for_word_spelling_is_one_letter():
    toks = prepare(words("m for mango r for rose t for tiger two"))
    assert [t.text for t in toks] == ["mrt", "two"]
    (r,) = id_runs(toks)
    assert r.letters == 3 and r.digits == 1
    assert [s.category for s in spans("m for mango r for rose t for tiger two")] == [
        Category.SPOKEN_OTHER_PII
    ]


def test_for_after_a_non_matching_word_is_left_alone():
    assert [t.text for t in prepare(words("m for tiger"))] == ["m", "for", "tiger"]


def test_english_two_digit_number_words_form_a_run():
    runs = number_runs(prepare(words("ninety eight seventy six fifty four")))
    assert [r.digits for r in runs] == ["987654"]
    assert covered_words("ninety eight seventy six fifty four") == [
        "ninety",
        "eight",
        "seventy",
        "six",
        "fifty",
        "four",
    ]
    assert [r.digits for r in number_runs(prepare(words("twenty thirteen")))] == ["2013"]
    assert spans("it took twenty five minutes") == []  # one pair alone is a quantity


def test_digit_groups_separated_by_long_pauses_stay_one_run():
    ws = [*words("nau aath", 0.0), *words("saat chhe", 2.5), *words("paanch char", 5.0)]
    runs = number_runs(prepare(ws), gap_s=2.0)
    assert [r.digits for r in runs] == ["987654"]
    far = [*words("nau aath", 0.0), *words("saat chhe", 4.0)]
    assert len(number_runs(prepare(far), gap_s=2.0)) == 2


def test_carry_bridges_a_pause_of_one_and_a_half_seconds():
    ws = [Word("nau", 1.0, 1.3, False, 1), Word("aath", 2.8, 3.1, False, 2)]
    win_ = TranscriptWindow(tuple(ws), 1.0, 3.1, 1)
    (s,) = SpanPlanner().leading_numbers(Carry(1.3, Category.SPOKEN_PHONE, 0.9), win_)
    assert s.pts_start == pytest.approx(2.8)


def test_two_misheard_digits_in_a_row_are_bridged_but_three_are_not():
    assert [r.digits for r in number_runs(prepare(words("nau aath kya kuch saat chhe")))] == ["98??76"]
    assert len(number_runs(prepare(words("nau aath kya kuch bhi saat chhe")))) == 2


def test_three_letters_make_a_spelling_and_two_do_with_a_cue():
    assert [s.category for s in spans("q w z")] == [Category.SPOKEN_OTHER_PII]
    assert spans("q w") == []
    assert [s.category for s in spans("gpay karo q w")] == [Category.SPOKEN_OTHER_PII]
