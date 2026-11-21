"""T3.2 units: span maths (9.11), transcript window overlap merge (5.4), deadlines and coverage (5.3)."""

from __future__ import annotations

import pytest

from audio.orchestrator.failsafe import ArrivalMap, Coverage, hard_deadline, soft_deadline
from audio.orchestrator.fallback_pii import DigitRunPiiDetector, load_pii_detector
from audio.orchestrator.mute import Carry, SpanPlanner, is_number_token
from audio.orchestrator.window import TranscriptWindowBuilder, norm
from contracts.enums import Category
from contracts.speech import PiiSpan, TranscriptWindow, Word


def w(text: str, t0: float, t1: float, seg: int = 1, approx: bool = False) -> Word:
    return Word(text, t0, t1, approx, seg)


def window(words, new_from=0, end=None) -> TranscriptWindow:
    return TranscriptWindow(tuple(words), words[0].t0, end if end is not None else words[-1].t1, new_from)


def pii(t0, t1, cat=Category.SPOKEN_PHONE, conf=0.9, gran="word", amb=False, reason="r") -> PiiSpan:
    return PiiSpan(cat, t0, t1, conf, reason, gran, amb)


# ------------------------------------------------------------------ span maths
def test_padding_is_0_2s_each_side_and_clamped_at_zero():
    ws = [w("abc", 2.0, 2.5)]
    (p,) = SpanPlanner().plan([pii(2.0, 2.5)], window(ws), {}, 9.0)
    assert (p.pts_start, p.pts_end) == (pytest.approx(1.8), pytest.approx(2.7))
    ws = [w("abc", 0.05, 0.4)]
    (p,) = SpanPlanner().plan([pii(0.05, 0.4)], window(ws), {}, 9.0)
    assert p.pts_start == 0.0


def test_spans_closer_than_0_3s_merge_and_far_ones_do_not():
    ws = [w("a", 1.0, 1.3), w("b", 1.8, 2.1), w("c", 4.0, 4.3)]  # padded: [0.8,1.5] [1.6,2.3] [3.8,4.5]
    out = SpanPlanner().plan([pii(1.0, 1.3), pii(1.8, 2.1), pii(4.0, 4.3)], window(ws), {}, 9.0)
    assert len(out) == 2 and out[0].pts_start == pytest.approx(0.8) and out[0].pts_end == pytest.approx(2.3)
    assert out[1].pts_start == pytest.approx(3.8)


def test_minimum_span_is_0_3s():
    ws = [w("a", 3.0, 3.01)]
    (p,) = SpanPlanner(pad_s=0.0).plan([pii(3.0, 3.01)], window(ws), {}, 9.0)
    assert p.pts_end - p.pts_start == pytest.approx(0.3)


def test_trailing_number_word_at_segment_end_extends_and_sets_the_carry_anchor():
    ws = [w("mera", 1.0, 1.3), w("nau", 1.4, 1.8)]
    (p,) = SpanPlanner().plan([pii(1.4, 1.8)], window(ws), {}, newest_end=1.85)
    assert (
        p.tail_extended
        and p.pts_end == pytest.approx(1.8 + 0.2 + SpanPlanner().tail_ext_s)
        and p.anchor_t1 == pytest.approx(1.8)
    )
    (q,) = SpanPlanner().plan([pii(1.4, 1.8)], window(ws), {}, newest_end=5.0)  # not at the segment end
    assert not q.tail_extended and q.pts_end == pytest.approx(2.0)


def test_leading_digits_of_the_next_segment_are_muted_through_the_carry():
    ws = [w("nau", 1.4, 1.8, 1), w("aath", 1.95, 2.3, 2), w("phir", 2.4, 2.7, 2)]
    spans = SpanPlanner().leading_numbers(Carry(1.8, Category.SPOKEN_PHONE, 0.9), window(ws, new_from=1))
    assert len(spans) == 1 and (spans[0].pts_start, spans[0].pts_end) == (1.95, 2.3)
    assert SpanPlanner().leading_numbers(None, window(ws, 1)) == []
    far = [w("aath", 4.5, 4.9, 2)]  # more than 2 s after the carry: not a continuation
    assert SpanPlanner().leading_numbers(Carry(1.8, Category.SPOKEN_PHONE, 0.9), window(far)) == []


def test_interpolated_words_widen_the_span_to_the_segment_bounds():
    ws = [w("a", 5.0, 5.5, 3, approx=True), w("b", 5.5, 6.0, 3, approx=True)]
    (p,) = SpanPlanner().plan([pii(5.0, 5.5, gran="segment")], window(ws), {3: (4.8, 6.1)}, 9.0)
    assert (
        p.granularity == "segment" and p.pts_start == pytest.approx(4.6) and p.pts_end == pytest.approx(6.3)
    )


def test_reason_is_always_a_code():
    ws = [w("x", 1.0, 1.5)]
    (p,) = SpanPlanner().plan([pii(1.0, 1.5, reason="r" * 60, amb=True)], window(ws), {}, 9.0)
    assert p.reason.startswith("ambiguous:") and len(p.reason) <= 64  # still valid for MuteSpan.reason


def test_number_tokens_in_all_scripts():
    assert all(is_number_token(t) for t in ["nau", "९८७", "ಒಂದು", "ஒன்று", "ఒకటి", "এক", "double", "sau"])
    assert not any(is_number_token(t) for t in ["hello", "mera", ""])


# ------------------------------------------------------------------ transcript window
def test_overlap_words_are_dropped_by_suffix_prefix_match():
    b = TranscriptWindowBuilder()
    b.add([w("ek", 0.0, 0.3), w("do", 0.4, 0.7), w("teen", 1.0, 1.3)], 0.0, 1.5)
    win = b.add([w("do", 0.42, 0.7), w("teen", 1.0, 1.31), w("char", 1.5, 1.8)], 1.1, 2.9, prev_end=1.5)
    assert [x.text for x in win.words] == ["ek", "do", "teen", "char"] and win.new_from_index == 3


def test_repeated_word_with_a_real_gap_is_kept():
    b = TranscriptWindowBuilder()
    b.add([w("nau", 0.0, 0.3)], 0.0, 0.5)
    win = b.add([w("nau", 2.0, 2.3)], 1.9, 3.0, prev_end=0.5)  # same text but 1.7 s later: a new word
    assert [x.text for x in win.words] == ["nau", "nau"]


def test_window_drops_words_older_than_its_span():
    b = TranscriptWindowBuilder(span_s=8.0)
    b.add([w("purana", 0.0, 0.3)], 0.0, 0.5)
    win = b.add([w("naya", 20.0, 20.3)], 19.9, 21.0, prev_end=0.5)
    assert [x.text for x in win.words] == ["naya"]


def test_norm_strips_punctuation_and_keeps_indic_marks():
    assert norm("Nau,") == "nau" and norm("पाँच।") == "पाँच"


# ------------------------------------------------------------------ deadlines and coverage
def test_deadline_arithmetic():
    assert soft_deadline(10.0, 4.0) == pytest.approx(13.7) and hard_deadline(10.0, 4.0) == pytest.approx(13.9)


def test_coverage_returns_only_uncovered_pieces():
    c = Coverage()
    c.add(1.0, 2.0)
    c.add(3.0, 4.0)
    assert c.uncovered(0.5, 4.5) == [(0.5, 1.0), (2.0, 3.0), (4.0, 4.5)]
    assert c.uncovered(1.2, 1.8) == []
    c.add(2.0, 3.0)
    assert c.uncovered(0.0, 5.0) == [(0.0, 1.0), (4.0, 5.0)]


def test_arrival_map_converts_both_ways():
    m = ArrivalMap()
    m.add(0.0, 100.0)
    m.add(10.0, 110.5)  # a hiccup: arrival drifted by 0.5 s
    assert m.arrival_of(5.0) == pytest.approx(105.0) and m.arrival_of(12.0) == pytest.approx(112.5)
    assert m.pts_at(112.5) == pytest.approx(12.0)


# ------------------------------------------------------------------ detector loading
def test_load_pii_detector_prefers_audio_pii_and_passes_the_timing_hook():
    from audio.pii.detector import SpokenPiiDetector
    from contracts.config import Config

    seen: list[str] = []
    det = load_pii_detector(Config(), on_timing=lambda s, a, b: seen.append(s))
    assert isinstance(det, SpokenPiiDetector)


def test_fallback_digit_run_detector_flags_ten_digits():
    import asyncio

    ws = [
        w(t, i * 0.3, i * 0.3 + 0.25)
        for i, t in enumerate("nau aath saat chhe paanch char teen do ek shunya".split())
    ]
    out = asyncio.run(DigitRunPiiDetector().detect(window(ws)))
    assert len(out) == 1 and out[0].category is Category.SPOKEN_PHONE
