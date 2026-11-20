"""Sample-accurate mutes on the 48 kHz timeline (docs/specs/engine.md 5.6, T3.4). CPU only, synthetic audio."""

from __future__ import annotations

import time

import numpy as np

from contracts import Category, MuteSpan
from engine.audio_mute import (
    BLEEP_AMPLITUDE,
    FADE_SAMPLES,
    RATE,
    MuteBook,
    MuteRange,
    apply_range,
    gain_envelope,
    merge_ranges,
    span_to_range,
)

DC = 10000  # constant signal: every zero in the output was written by the mute


def span(
    start: float, end: float, mode: str = "silence", sid: str = "s1", granularity: str = "segment"
) -> MuteSpan:
    return MuteSpan(sid, start, end, Category.SPOKEN_PHONE, granularity, 0.9, "digits", 0.0, mode=mode)  # type: ignore[arg-type]


def tone(n: int, first: int = 0, amp: int = 10000) -> np.ndarray:
    idx = np.arange(first, first + n)
    return (amp * np.sin(2 * np.pi * 440.0 * idx / RATE)).astype(np.int16)


def dc(n: int) -> np.ndarray:
    return np.full(n, DC, np.int16)


def test_exact_sample_indices_zeroed_and_fades_lie_inside_the_span():
    pcm = dc(RATE)  # one second
    rng = span_to_range(span(0.25, 0.5))
    assert (rng.start, rng.end) == (12000, 24000)  # round(pts * 48000), end exclusive
    assert apply_range(pcm, 0, rng)
    zeros = np.flatnonzero(pcm == 0)
    # exactly the samples between the two fades are zero: [start + 480, end - 480)
    assert zeros[0] == 12000 + FADE_SAMPLES and zeros[-1] == 24000 - FADE_SAMPLES - 1
    assert len(zeros) == 12000 - 2 * FADE_SAMPLES
    assert np.array_equal(pcm[:12000], dc(12000))  # bit-exact outside the span
    assert np.array_equal(pcm[24000:], dc(RATE - 24000))
    fade_out = pcm[12000 : 12000 + FADE_SAMPLES].astype(float)
    assert fade_out[0] == DC  # the first sample of the span is still at full gain
    assert np.all(np.diff(fade_out) < 0) and fade_out[-1] > 0  # linear ramp down, 10 ms, still audible
    fade_in = pcm[24000 - FADE_SAMPLES : 24000].astype(float)
    assert np.all(np.diff(fade_in) > 0) and fade_in[0] > 0 and fade_in[-1] == DC


def test_range_is_round_of_pts_times_rate():
    assert span_to_range(span(1.0000106, 1.5)).start == 48001  # 48000.51 rounds up
    assert span_to_range(span(1.0000104, 1.5)).start == 48000  # 48000.50 rounds down
    assert span_to_range(span(0.0, 1.5)).end == 72000


def test_bleep_is_a_1khz_sine_at_minus_18_dbfs_phase_continuous_across_slices():
    pcm = tone(RATE)
    whole = pcm.copy()
    apply_range(whole, 0, span_to_range(span(0.25, 0.75, mode="bleep")))
    interior = whole[12000 + FADE_SAMPLES + 100 : 36000 - FADE_SAMPLES - 100].astype(float)
    peak = np.abs(interior).max()
    assert abs(peak - BLEEP_AMPLITUDE) <= 2
    assert abs(20 * np.log10(peak / 32767) + 18.0) < 0.1
    spectrum = np.abs(np.fft.rfft(interior))
    assert abs(np.argmax(spectrum) * RATE / len(interior) - 1000.0) < 5.0
    # the speech is gone inside the fades' interior: the bleep is the only content
    k = np.arange(12000 + FADE_SAMPLES, 36000 - FADE_SAMPLES)
    expected = np.rint(BLEEP_AMPLITUDE * np.sin(2 * np.pi * 1000.0 * k / RATE))
    assert np.abs(whole[k].astype(float) - expected).max() <= 1
    # slice-wise application gives the same samples (the sine follows the absolute index)
    book = MuteBook()
    book.add(span(0.25, 0.75, mode="bleep"))
    out = np.concatenate([_apply(book, pcm, f) for f in range(0, RATE, 1600)])
    assert np.array_equal(out, whole)


def _apply(book: MuteBook, pcm: np.ndarray, first: int, n: int = 1600) -> np.ndarray:
    chunk = pcm[first : first + n].copy()
    book.apply(chunk, first)
    return chunk


def test_short_spans_are_hard_silence():
    pcm = dc(2000)
    apply_range(pcm, 0, span_to_range(span(0.005, 0.02)))  # 15 ms: no room for two fades
    assert not pcm[240:960].any()
    assert np.all(pcm[:240] == DC) and np.all(pcm[960:] == DC)


def test_applying_slice_by_slice_equals_applying_the_whole_buffer():
    whole = tone(RATE)
    expected = whole.copy()
    apply_range(expected, 0, span_to_range(span(0.1234, 0.3777)))
    book = MuteBook()
    book.add(span(0.1234, 0.3777))
    out = np.concatenate([_apply(book, whole, f) for f in range(0, RATE, 1600)])
    assert np.array_equal(out, expected)
    assert book.emitted_through == 1600 * (-(-RATE // 1600))  # the cursor follows the last slice cut


def test_overlapping_and_adjacent_spans_merge_into_one_range_with_one_pair_of_fades():
    book = MuteBook()
    book.add(span(0.10, 0.30, sid="a"))
    book.add(span(0.30, 0.50, sid="b"))  # adjacent
    book.add(span(0.45, 0.60, sid="c"))  # overlapping
    book.add(span(0.80, 0.90, sid="d"))  # separate
    ranges = book.merged_ranges
    assert [(r.start, r.end) for r in ranges] == [(4800, 28800), (38400, 43200)]
    pcm = dc(RATE)
    book.apply(pcm, 0)
    zeros = np.flatnonzero(pcm == 0)
    # no fade seam at the 0.30 and 0.45 joins: zero all the way through
    assert np.array_equal(zeros[zeros < 30000], np.arange(4800 + FADE_SAMPLES, 28800 - FADE_SAMPLES))
    assert pcm[28800 - 1] > 0 and pcm[4800] == DC  # outer edges fade, inside the merged span
    assert np.all(pcm[28800:38400] == DC)  # the gap between the two ranges is untouched


def test_merge_mode_silence_wins_over_bleep():
    merged = merge_ranges([MuteRange("a", 0, 100, "bleep"), MuteRange("b", 50, 200, "silence")])
    assert len(merged) == 1 and merged[0].mode == "silence" and (merged[0].start, merged[0].end) == (0, 200)


def test_late_span_is_counted_and_not_stored_but_a_partly_emitted_one_still_applies_its_rest():
    book = MuteBook()
    book.advance(24000)  # the audio up to 0.5 s has left
    outcome = book.add(span(0.0, 0.5))
    assert not outcome and outcome.late and outcome.leaked_samples == 24000
    assert book.late == 1 and len(book) == 0
    outcome = book.add(span(0.4, 0.9, sid="p"))
    assert outcome and not outcome.late and outcome.leaked_samples == 24000 - 19200
    assert book.partly_late == 1 and book.accepted == 1
    pcm = dc(1600)
    book.apply(pcm, 24000)
    assert not pcm.any()  # the fade-out is already behind us: the rest of the span is silence
    # a span entirely in the future is neither late nor leaked
    clean = book.add(span(5.0, 6.0, sid="f"))
    assert clean and clean.leaked_samples == 0 and not clean.late


def test_span_accepted_until_its_audio_leaves_not_until_a_clock_time():
    """The book only knows samples: a span handed over just before its first sample is cut is applied."""
    book = MuteBook()
    pcm = dc(RATE)
    for first in range(0, 48000, 1600):  # emit the first second, frame by frame
        chunk = pcm[first : first + 1600].copy()
        book.apply(chunk, first)
    assert book.emitted_through == 48000
    assert book.add(span(1.0, 1.5)).accepted  # starts exactly at the next sample: not late at all
    assert book.late == 0 and book.partly_late == 0


def test_failsafe_spans_are_final_and_never_shrink():
    book = MuteBook()
    book.add(span(1.0, 2.0, sid="x", granularity="failsafe"))
    out = book.add(span(1.2, 1.5, sid="x"))  # a shorter "verdict" with the same id arrives later
    assert out.accepted and out.replaced
    assert [(r.start, r.end) for r in book.merged_ranges] == [(48000, 96000)]  # coverage kept in full
    assert book.merged_ranges[0].final
    book.advance(60000)
    assert [(r.start, r.end) for r in book.merged_ranges] == [(48000, 96000)]  # applied: still final
    book.add(span(0.9, 2.5, sid="x"))  # a longer one extends it
    assert [(r.start, r.end) for r in book.merged_ranges] == [(43200, 120000)]


def test_non_failsafe_span_revised_before_it_leaves_is_replaced_after_it_started_only_united():
    book = MuteBook()
    book.add(span(1.0, 2.0, sid="w"))
    assert book.add(span(1.2, 1.5, sid="w")).replaced  # nothing emitted yet: the revision wins
    assert [(r.start, r.end) for r in book.merged_ranges] == [(57600, 72000)]
    book.advance(60000)  # its head has left: it can no longer shrink
    book.add(span(1.3, 1.4, sid="w"))
    assert [(r.start, r.end) for r in book.merged_ranges] == [(57600, 72000)]


def test_hard_silence_ranges_cover_late_arriving_audio_without_fades():
    book = MuteBook()
    book.add_silence(1000, 5000)
    pcm = dc(8000)
    assert book.apply(pcm, 0) == 1
    assert not pcm[1000:5000].any() and np.all(pcm[:1000] == DC) and np.all(pcm[5000:] == DC)
    book.apply(dc(1), 9000)  # past the range: pruned
    assert book.apply(dc(8000), 0) == 0


def test_mode_override_switches_every_range_to_bleep():
    book = MuteBook()
    book.add(span(0.1, 0.4))
    book.mode_override = "bleep"
    pcm = dc(RATE // 2)
    book.apply(pcm, 0)
    interior = pcm[4800 + FADE_SAMPLES : 19200 - FADE_SAMPLES].astype(float)
    assert interior.any() and np.abs(interior).max() <= BLEEP_AMPLITUDE + 1


def test_prune_clear_and_bounds():
    book = MuteBook(max_spans=3)
    for k in range(5):
        book.add(span(k, k + 0.5, sid=f"s{k}"))
    assert len(book) == 3  # bounded
    book.prune(emitted_through=int(3.2 * RATE))
    assert len(book) == 2
    book.clear()
    assert len(book) == 0 and book.merged_ranges == []
    book.reset_cursor()
    assert book.emitted_through == -1


def test_no_overlap_means_nothing_changes():
    pcm = tone(1600)
    before = pcm.copy()
    assert MuteBook().apply(pcm, 0) == 0
    book = MuteBook()
    book.add(span(5.0, 6.0))
    assert book.apply(pcm, 0) == 0 and np.array_equal(pcm, before)


def test_zero_length_span_is_ignored():
    outcome = MuteBook().add(span(1.0, 1.0))
    assert not outcome and not outcome.late


def test_envelope_helper_shape():
    env = gain_envelope(12000)
    assert env[0] == 1.0 and env[-1] == 1.0 and not env[FADE_SAMPLES : 12000 - FADE_SAMPLES].any()
    assert len(env) == 12000


def test_apply_cost_per_frame_is_under_half_a_millisecond():
    """Acceptance T3.4: mute application < 0.5 ms per frame (a 1.5 s bleep span, worst case: sine + fades)."""
    pcm = tone(RATE * 4)
    for mode in ("silence", "bleep"):
        book = MuteBook()
        for k in range(6):  # several spans in the book, one 1.5 s span active
            book.add(span(0.2 + 0.5 * k, 0.5 + 0.5 * k, mode=mode, sid=f"s{k}"))
        book.add(span(1.0, 2.5, mode=mode, sid="long"))
        costs = []
        for first in range(0, RATE * 3, 1600):
            chunk = pcm[first : first + 1600].copy()
            began = time.perf_counter()
            book.apply(chunk, first)
            costs.append((time.perf_counter() - began) * 1000.0)
        worst, median = max(costs), float(np.median(costs))
        print(f"mute apply ({mode}): median {median:.3f} ms, max {worst:.3f} ms per 1600-sample frame")
        assert median < 0.5


# ------------------------------------------------------------------ bounded book never forgets unemitted audio


def test_cap_evicts_only_emitted_spans_and_keeps_every_unemitted_failsafe_span():
    book = MuteBook(max_spans=8)
    for k in range(6):  # six spans that have already left, then more than the cap of unemitted ones
        book.add_range(MuteRange(f"old{k}", k * 1000, k * 1000 + 500, "silence"))
    book.emitted_through = 6000  # (the old ones are all behind the emit cursor now)
    fail = [MuteRange(f"f{k}", 10_000 + k * 1000, 10_000 + k * 1000 + 500, "silence", True) for k in range(8)]
    for rng in fail:
        book.add_range(rng)
    assert len(book) <= 8
    assert not any(k.startswith("old") for k in book._spans)  # emitted ones went first
    pcm = dc(30_000)
    book.apply(pcm, 6000)
    for rng in fail:  # every failsafe span still silences its own samples
        lo = rng.start - 6000
        assert not pcm[lo + FADE_SAMPLES : lo + 500].any() or rng.end - rng.start < 2 * FADE_SAMPLES


def test_cap_with_every_span_unemitted_unites_the_oldest_and_never_drops_audio():
    book = MuteBook(max_spans=4)
    spans = [
        MuteRange(f"s{k}", 100_000 + k * 20_000, 100_000 + k * 20_000 + 5000, "silence", k == 0)
        for k in range(10)
    ]
    for rng in spans:
        book.add_range(rng)
    assert len(book) == 4 and book.united == 6
    pcm = dc(400_000)
    book.apply(pcm, 0, advance=False)
    for rng in spans:  # every original span is still fully silent between its fades
        assert not pcm[rng.start + FADE_SAMPLES : rng.end - FADE_SAMPLES].any(), rng.span_id
    assert np.array_equal(pcm[:100_000], dc(100_000))  # nothing before the first span is touched


def test_united_span_cannot_be_shrunk_by_a_revision():
    book = MuteBook(max_spans=2)
    book.add_range(MuteRange("a", 1000, 3000, "silence"))
    book.add_range(MuteRange("b", 5000, 7000, "silence"))
    book.add_range(MuteRange("c", 20_000, 22_000, "silence"))  # a and b are united under "a"
    book.add_range(MuteRange("a", 1000, 1500, "silence"))  # a shorter revision of "a"
    covered = np.zeros(8000, bool)
    for r in book.merged_ranges:
        covered[r.start : min(r.end, 8000)] = True
    assert covered[1000:7000].all()


def test_hard_ranges_are_never_dropped_beyond_the_cap():
    book = MuteBook()
    for k in range(200):
        book.add_silence(k * 1000, k * 1000 + 100)
    assert len(book._hard) <= 64
    pcm = dc(200_000)
    book.apply(pcm, 0, advance=False)
    for k in range(200):
        assert not pcm[k * 1000 : k * 1000 + 100].any(), k
