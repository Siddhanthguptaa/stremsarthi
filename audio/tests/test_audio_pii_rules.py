"""T3.3: spoken-PII rules (docs/specs/audio.md 5.6) on synthetic, clearly fake phrases."""

from __future__ import annotations

import asyncio

import pytest

from audio.orchestrator.window import TranscriptWindowBuilder
from audio.pii.detector import SpokenPiiDetector
from audio.pii.rules import SpokenRules
from contracts.enums import Category
from contracts.speech import TranscriptWindow, Word

STEP = 0.35  # seconds per word


def words(
    text: str, t0: float = 0.0, step: float = STEP, seg_id: int = 1, approx: bool = False
) -> list[Word]:
    return [
        Word(t, t0 + i * step, t0 + i * step + step * 0.8, approx, seg_id) for i, t in enumerate(text.split())
    ]


def win(text: str, **kw) -> TranscriptWindow:
    ws = words(text, **kw)
    return TranscriptWindow(tuple(ws), ws[0].t0, ws[-1].t1, 0)


def run(window: TranscriptWindow, **kw):
    return asyncio.run(SpokenPiiDetector(SpokenRules(**kw)).detect(window))


def one(text: str, **kw):
    spans = run(win(text), **kw)
    assert len(spans) == 1, spans
    return spans[0]


def test_mobile_number_in_hindi_words_is_phone_0_9():
    s = one("nau aath saat chhe paanch char teen do ek shunya")  # 9876543210
    assert (s.category, s.confidence, s.ambiguous) == (Category.SPOKEN_PHONE, 0.9, False)


def test_mobile_with_country_code_and_script_digits():
    assert (
        one("plus nine one nine eight seven six five four three two one zero").category
        is Category.SPOKEN_PHONE
    )
    assert one("९८७६५४३२१०").category is Category.SPOKEN_PHONE
    assert one("0 9 8 7 6 5 4 3 2 1 0".replace("0 9", "09", 1)).category is Category.SPOKEN_PHONE


def test_double_and_triple_inside_a_phone_number():
    # 98 triple 8 + 5 digits = 10 digits
    s = one("nine eight triple eight one two three four five")
    assert s.category is Category.SPOKEN_PHONE


def test_aadhaar_and_card_lengths_are_other_pii_0_8():
    a = one("2 3 4 5 6 7 8 9 0 1 2 3")  # 12 digits, first digit 2: not a mobile
    assert (a.category, a.confidence) == (Category.SPOKEN_OTHER_PII, 0.8)
    c = one("1 2 3 4 1 2 3 4 1 2 3 4 1 2 3 5")  # 16 digits, fails Luhn
    assert (c.category, c.confidence) == (Category.SPOKEN_OTHER_PII, 0.8)


def test_card_passing_luhn_gets_a_small_boost():
    assert one(" ".join("4111111111111111")).confidence == pytest.approx(0.9)


def test_six_digit_run_with_cue_is_muted_without_cue_is_ambiguous():
    s = one("mera otp hai one two three four five six")
    assert s.category is Category.SPOKEN_OTHER_PII and not s.ambiguous and s.confidence >= 0.8
    # no LLM wired: the 5.7 fallback mutes a 6+ digit run and flags it ambiguous (confidence 0.5)
    s = one("so the code is one two three four five six")
    assert s.ambiguous and s.confidence == 0.5 and s.reason == "llm_fallback_mute"


def test_cue_must_come_before_and_within_six_seconds():
    far = "otp " + "bahut der baad ek baar fir " * 2 + "one two three four five six"
    spans = run(win(far, step=0.5))
    assert spans and all(s.ambiguous for s in spans)  # cue too old: the run is ambiguous, not confirmed


def test_four_digit_number_needs_a_watch_window():
    assert run(win("room number one two three four")) == []
    s = one("pin hai one two three four")
    assert s.category is Category.SPOKEN_OTHER_PII and s.confidence == 0.6


def test_ordinary_speech_and_short_numbers_pass():
    assert run(win("aaj ka stream bahut mazedaar hai do teen log aaye hain")) == []
    assert run(win("kal teen baje milte hain")) == []


def test_watch_window_adds_confidence_to_a_long_number():
    plain = one("nine eight seven six five four three two one zero")
    cued = one("mera number hai nine eight seven six five four three two one zero")
    assert plain.confidence == 0.9 and cued.confidence == 1.0


def test_spoken_upi_forms_cover_name_and_handle():
    s = one("rahul nine eight at ok s b i")
    assert s.category is Category.SPOKEN_UPI and s.confidence == 0.9
    w = win("rahul nine eight at ok s b i")
    assert s.pts_start == w.words[0].t0 and s.pts_end == w.words[-1].t1  # whole name + handle
    s = one("rahul at the rate of ybl")
    assert s.category is Category.SPOKEN_UPI and s.pts_start == 0.0
    assert one("rahul एट द रेट ybl").category is Category.SPOKEN_UPI
    assert one("priya at y b l").category is Category.SPOKEN_UPI
    assert one("my upi id is rahul98@oksbi").category is Category.SPOKEN_UPI


def test_upi_name_stops_at_filler_words_and_pauses():
    w = win("mera upi id hai rahul at ybl")
    s = run(w)[0]
    names = {t.text: t for t in w.words}
    assert s.pts_start == names["rahul"].t0
    gap = [*words("kuch bhi", 0.0), *words("sharma at ybl", 3.0)]
    s = run(TranscriptWindow(tuple(gap), 0.0, 5.0, 0))[0]
    assert s.pts_start == 3.0  # the 3 s pause cuts the name


def test_plain_at_and_bank_names_do_not_trigger():
    assert run(win("hum park at the evening milenge")) == []
    assert run(win("sbi ki branch mein jana hai")) == []


def test_unknown_handle_needs_an_explicit_marker_and_is_ambiguous():
    assert run(win("rahul at foobar")) == []
    cands = SpokenRules().analyse(win("rahul at the rate of foobar"))[1]
    assert len(cands) == 1 and cands[0].category is Category.SPOKEN_UPI
    assert cands[0].ambiguous and cands[0].confidence == 0.6
    # no LLM and no watch window: the 5.7 fallback lets it pass; inside a watch window it is muted
    assert run(win("rahul at the rate of foobar")) == []
    assert one("mera upi id hai rahul at the rate of foobar").category is Category.SPOKEN_UPI


def test_address_cue_covers_up_to_twelve_tokens():
    text = "flat number " + " ".join(f"w{i}" for i in range(20))
    w = win(text)
    s = run(w)[0]
    assert s.category is Category.SPOKEN_ADDRESS
    last_covered = max(x.t1 for x in w.words[: 2 + 12])
    assert s.pts_end == pytest.approx(last_covered)


def test_address_cue_stops_at_a_pause_of_0_8s():
    ws = [*words("ghar number paanch", 0.0), *words("aur baat kuch aur hai", 3.0)]
    s = run(TranscriptWindow(tuple(ws), 0.0, 5.0, 0))[0]
    assert s.pts_end == ws[2].t1


def test_weak_address_words_alone_pass_but_count_with_an_address_open_cue():
    assert run(win("main road par traffic bahut hai aaj")) == []
    s = one("mera address hai shastri nagar road gali number paanch")
    assert s.category is Category.SPOKEN_ADDRESS and not s.ambiguous


def test_address_mute_off_disables_address_spans():
    assert run(win("flat number five gali number two"), address_mute=False) == []


def test_number_split_across_two_segments_is_one_span():
    b = TranscriptWindowBuilder(span_s=8.0)
    first = words("mera number nau aath saat chhe paanch", 0.0, seg_id=1)
    w1 = b.add(first, 0.0, 1.9)
    det = SpokenPiiDetector(SpokenRules())
    spans1 = asyncio.run(det.detect(w1))
    assert spans1[0].category is Category.SPOKEN_OTHER_PII  # 5 digits + cue
    second = words("char teen do ek shunya", 1.9, seg_id=2)
    w2 = b.add(second, 1.9, 3.7)
    spans2 = asyncio.run(det.detect(w2))
    assert len(spans2) == 1 and spans2[0].category is Category.SPOKEN_PHONE
    assert spans2[0].pts_start == pytest.approx(first[2].t0) and spans2[0].pts_end == pytest.approx(
        second[-1].t1
    )


def test_only_spans_touching_new_words_are_reported():
    ws = words("nau aath saat chhe paanch char teen do ek shunya") + words("bas itna hi", 5.0, seg_id=2)
    w = TranscriptWindow(tuple(ws), 0.0, 6.5, 10)
    assert run(w) == []


def test_approx_words_make_segment_granular_spans():
    ws = words("nau aath saat chhe paanch char teen do ek shunya", approx=True)
    s = asyncio.run(SpokenPiiDetector().detect(TranscriptWindow(tuple(ws), 0.0, 4.0, 0)))[0]
    assert s.granularity == "segment"
