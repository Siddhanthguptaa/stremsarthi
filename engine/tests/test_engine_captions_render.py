"""Caption renderer: layout, placement, timing against the frame PTS, toggle, bitmap cache and render
budget, and the privacy rules (after masks, never unmask, text never logged). CPU only."""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace

import numpy as np
import pytest

from contracts import CaptionEvent, Config, ConfigChange, Frame, Lane, MaskStyle, Quad, Topic
from engine import captions as cap
from engine.bus import LocalBus
from engine.captions import CaptionRenderer, char_count
from engine.compositor import Composed
from engine.deadline import DeadlineEngine
from engine.motion import IdentityMotion
from engine.session import Session
from engine.session_fakes import FakeLaneHost, RecordingSink, SyntheticSource, card_detector
from engine.testing import lane_result

W, H = 1280, 720
T0, FPS, D = 10.0, 30, 3.0
HINDI = "नमस्ते दोस्तों, आज हम क्षत्रिय श्रृंगार के बारे में बात करेंगे"
LONG_HI = " ".join(["स्वतंत्रता", "क्षत्रिय", "श्रृंगार", "विद्यार्थी", "प्रश्न"] * 6)


def ev(start=1.0, end=2.0, text="hello there", lang="en", final=True) -> CaptionEvent:
    return CaptionEvent(pts_start=start, pts_end=end, lang=lang, text=text, is_final=final)


def noise(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 256, (H, W, 3), dtype=np.uint8)


def box_changed(before: np.ndarray, after: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero((before != after).any(axis=2))
    return None if len(ys) == 0 else (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


@pytest.fixture(scope="module")
def renderer() -> CaptionRenderer:
    return CaptionRenderer(enabled=True)


# ------------------------------------------------------------------ layout


def test_wraps_to_two_lines_of_at_most_42_characters(renderer):
    size, lines, truncated = renderer.layout(HINDI, "hi")
    assert 1 <= len(lines) <= 2 and not truncated
    assert all(char_count(line) <= 42 for line in lines)
    assert " ".join(lines).split() == HINDI.split()  # exactly the text, nothing dropped, nothing split


def test_over_long_text_is_cut_with_an_ellipsis_inside_the_limits(renderer):
    _, lines, truncated = renderer.layout(LONG_HI, "hi")
    assert truncated and len(lines) == 2 and lines[-1].endswith("...")
    assert all(char_count(line) <= 42 for line in lines)


def test_unbroken_long_word_breaks_on_cluster_boundaries(renderer):
    word = "क्षत्रिय" * 12
    _, lines, _ = renderer.layout(word, "hi")
    for line in lines:
        assert line[0] not in "्िीं"  # never starts with a virama or a mark
        assert not line.rstrip(".").endswith("्")


def test_config_max_chars_is_honoured():
    r = CaptionRenderer(Config(_env_file=None, captions={"enabled": True, "max_chars_per_line": 20}))
    _, lines, _ = r.layout("one two three four five six seven eight nine ten", "en")
    assert all(char_count(line) <= 20 for line in lines)


def test_box_sits_in_the_bottom_third_inside_the_safe_area(renderer):
    for text, lang in ((HINDI, "hi"), ("ok", "en"), (LONG_HI, "hi")):
        x, y, w, h = renderer.prepare(ev(text=text, lang=lang)).rect
        assert y >= H * 2 // 3 and y + h <= H * (1 - cap.BOTTOM_MARGIN) + 1
        assert x >= W * cap.SIDE_MARGIN - 1 and x + w <= W * (1 - cap.SIDE_MARGIN) + 1
        assert abs((x + w / 2) - W / 2) <= 1


def test_works_on_other_canvas_sizes():
    r = CaptionRenderer(width=640, height=360, enabled=True)
    img = noise(1)[:360, :640].copy()
    assert r.render(img, 0.0) is False
    r.add(ev(0.0, 1.0, HINDI, "hi"))
    assert r.render(img, 0.5) is True


# ------------------------------------------------------------------ drawing


def test_only_the_caption_box_changes_and_text_is_legible(renderer):
    r = CaptionRenderer(enabled=True)
    r.add(ev(1.0, 2.0, "Hello viewers", "en"))
    base = np.full((H, W, 3), 128, np.uint8)
    out = base.copy()
    assert r.render(out, 1.5)
    x, y, w, h = r._entries[0].prepared.rect
    changed = box_changed(base, out)
    assert changed is not None
    cx0, cy0, cx1, cy1 = changed
    assert cx0 >= x and cy0 >= y and cx1 <= x + w and cy1 <= y + h  # nothing outside the box moved
    roi = out[y : y + h, x : x + w].astype(int)
    assert (roi.min(axis=2) > 235).sum() > 150  # white text pixels
    assert (roi.max(axis=2) < 40).sum() > 150  # dark outline and box pixels
    assert (out[y + h // 2 : y + h // 2 + 1, x - 1] == 128).all()  # left of the box untouched


def test_legible_on_white_and_black_backgrounds(renderer):
    r = CaptionRenderer(enabled=True)
    r.add(ev(0.0, 5.0, "Namaste दोस्तों", "hi"))
    for bg in (0, 255):
        out = np.full((H, W, 3), bg, np.uint8)
        r.render(out, 1.0)
        x, y, w, h = r._entries[0].prepared.rect
        roi = out[y : y + h, x : x + w].astype(int)
        # text (white) and its outline (black) both differ clearly from the background
        assert (np.abs(roi - bg).max(axis=2) > 100).sum() > 200


def test_render_never_reads_anything_but_the_pixel_it_replaces(renderer):
    """Per-pixel and monotone: a brighter input never gives a darker output. Together with 'only inside the
    box' this means a caption cannot reveal what a mask hid: it never sees the unmasked frame."""
    r = CaptionRenderer(enabled=True)
    r.add(ev(0.0, 5.0, HINDI, "hi"))
    lo, hi = noise(2) // 2, noise(2) // 2 + 100
    a, b = lo.copy(), hi.copy()
    r.render(a, 1.0)
    r.render(b, 1.0)
    assert (a.astype(int) <= b.astype(int)).all()
    outside = np.ones((H, W), bool)
    x, y, w, h = r._entries[0].prepared.rect
    outside[y : y + h, x : x + w] = False
    assert np.array_equal(a[outside], lo[outside]) and np.array_equal(b[outside], hi[outside])


def test_read_only_or_wrong_sized_target_is_refused():
    r = CaptionRenderer(enabled=True)
    r.add(ev(0.0, 5.0))
    ro = np.zeros((H, W, 3), np.uint8)
    ro.setflags(write=False)
    with pytest.raises(ValueError):
        r.render(ro, 1.0)
    with pytest.raises(ValueError):
        r.render(np.zeros((10, 10, 3), np.uint8), 1.0)


# ------------------------------------------------------------------ timing and toggle


def test_caption_shows_only_inside_its_pts_window():
    r = CaptionRenderer(enabled=True)
    r.add(ev(1.0, 2.0))
    shown = {pts: r.render(np.zeros((H, W, 3), np.uint8), pts) for pts in (0.966, 1.0, 1.5, 2.0, 2.033)}
    assert shown == {0.966: False, 1.0: True, 1.5: True, 2.0: True, 2.033: False}


def test_newest_revision_wins_and_interims_are_replaced():
    r = CaptionRenderer(enabled=True)
    r.add(ev(1.0, 2.0, "first guess", final=False))
    r.add(ev(1.0, 2.0, "better guess", final=False))
    assert len(r._entries) == 1 and r.active(1.5).text == "better guess"
    r.add(ev(1.0, 2.0, "final", final=True))
    assert r.active(1.5).text == "final"
    r.add(ev(1.5, 2.5, "next utterance"))
    assert r.active(1.2).text == "final" and r.active(1.8).text == "next utterance"


def test_late_events_are_dropped_and_old_ones_pruned():
    r = CaptionRenderer(enabled=True)
    r.add(ev(1.0, 2.0))
    r.render(np.zeros((H, W, 3), np.uint8), 10.0)  # the emitted timeline is at 10 s
    assert r.stats["added"] == 1 and not r._entries  # the old event was pruned
    r.add(ev(3.0, 4.0))
    assert r.stats["late"] == 1 and not r._entries


def test_toggle_via_config_key_takes_effect_next_frame():
    r = CaptionRenderer(Config(_env_file=None), enabled=None)
    assert r.enabled is False  # contracts default: captions.enabled = False
    r.add(ev(0.0, 9.0, HINDI, "hi"))  # arrives while off: kept for later, nothing drawn
    img = np.zeros((H, W, 3), np.uint8)
    assert r.render(img, 1.0) is False and not img.any()
    assert r.apply_config_key("captions.enabled", True) is True
    assert r.render(img, 1.0) is True and img.any()  # shaped lazily on the first frame
    assert r.apply_config_key("captions.enabled", False) is True
    img2 = np.zeros((H, W, 3), np.uint8)
    assert r.render(img2, 1.0) is False and not img2.any()
    with pytest.raises(ValueError):
        r.apply_config_key("captions.enabled", "yes")
    assert r.apply_config_key("categories.face.enabled", False) is False


# ------------------------------------------------------------------ cache and budget


def test_bitmap_is_shaped_once_per_event_not_per_frame(monkeypatch):
    r = CaptionRenderer(enabled=True)
    calls = []
    real = r.prepare
    monkeypatch.setattr(r, "prepare", lambda e: calls.append(1) or real(e))
    r.add(ev(0.0, 3.0, HINDI, "hi"))
    img = np.zeros((H, W, 3), np.uint8)
    for k in range(90):
        assert r.render(img, k / 30.0)
    assert len(calls) == 1


def test_render_budget_under_3_ms_per_frame_at_720p():
    r = CaptionRenderer(enabled=True)
    r.add(ev(0.0, 99.0, HINDI, "hi"))
    t0 = time.perf_counter()
    r.prepare(ev(0.0, 1.0, HINDI, "hi"))
    shape_ms = (time.perf_counter() - t0) * 1000
    frame = noise(3)
    times = []
    for _ in range(200):
        img = frame.copy()
        t = time.perf_counter()
        r.render(img, 1.0)
        times.append((time.perf_counter() - t) * 1000)
    median, p99 = float(np.median(times)), float(np.percentile(times, 99))
    print(
        f"caption render median {median:.3f} ms, p99 {p99:.3f} ms; shaping one 2-line caption {shape_ms:.1f} ms"
    )
    assert median < 3.0 and p99 < 6.0
    assert shape_ms < 250.0  # one-off, off the frame path


# ------------------------------------------------------------------ privacy


MARKER = "ZXQJ-रहस्य-4417"


def test_caption_text_is_never_logged_or_in_errors(caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    r = CaptionRenderer(enabled=True)
    event = ev(0.0, 2.0, MARKER, "hi")
    assert MARKER not in repr(event)
    r.add(event)
    r.render(np.zeros((H, W, 3), np.uint8), 1.0)
    # a layout failure logs the exception type and language only
    monkeypatch.setattr(r, "prepare", lambda e: (_ for _ in ()).throw(RuntimeError(e.text)))
    r.add(ev(5.0, 6.0, MARKER, "hi"))
    assert r.stats["errors"] == 1
    assert MARKER not in caplog.text and "ZXQJ" not in caplog.text
    assert not any(MARKER in str(a) for rec in caplog.records for a in (rec.args or ()))


# ------------------------------------------------------------------ session wiring


def cfg(enabled: bool = True) -> Config:
    return Config(_env_file=None, delay_seconds=D, captions={"enabled": enabled})


def make_session(seed: int, *, enabled=True, mask=None, results=True, bus=None):
    config = cfg(enabled)
    now = [T0]
    host = (
        FakeLaneHost(
            card_detector(
                40,
                mask,
            ),
            style=MaskStyle.SOLID,
            valid_for_s=2.0,
            latency_s=0.02,
        )
        if mask
        else None
    )
    sink = RecordingSink(keep_images=True, clock=lambda: now[0])
    lanes = (Lane.FAST,)
    engine = DeadlineEngine.from_config(config, lanes, motion=IdentityMotion())
    session = Session(
        config, SyntheticSource(1), sink, lane_host=host, engine=engine, bus=bus, clock=lambda: now[0]
    )
    return session, sink, now, host


def drive(session, now, n=30, seed=0, results=True):
    for seq in range(n):
        t = T0 + seq / FPS
        now[0] = t
        pts = seq / FPS
        session.on_frame(Frame(seq, pts, t, noise(seed + seq)), now=t)
        if results and session.lane_host is None:
            session.engine.submit_result(lane_result(Lane.FAST, seq, t + 0.01))
        session.step(t)
    end = now[0] + D + 0.3
    while now[0] < end:
        now[0] += 0.005
        session.step(now[0])


def test_session_burns_captions_on_the_delayed_timeline_and_publishes_timing():
    bus = LocalBus()
    timings = []
    bus.on(Topic.TIMING, lambda t: timings.append(t.stage))
    session, sink, now, _ = make_session(0, bus=bus)
    bus.publish(Topic.CAPTION, ev(0.3, 0.6, HINDI, "hi"))  # source PTS 0.3 to 0.6 = frames 9..18
    drive(session, now, seed=100)
    shown = [
        r.pts
        for r in sink.records
        if session.captions is not None and r.image is not None and _has_caption(r.image, r.pts)
    ]
    assert shown, "no frame carried the caption"
    assert min(shown) >= 0.3 - 1e-9 and max(shown) <= 0.6 + 1e-9
    assert len(shown) == 10  # frames 9..18 inclusive
    assert session.counts["caption_frames"] == 10 and session.counts["caption_errors"] == 0
    assert "caption" in timings
    assert 0 < len(session.caption_ms) and max(session.caption_ms) < 10.0


def _has_caption(image: np.ndarray, pts: float) -> bool:
    # the source frame is noise: a caption box makes the bottom strip much darker on average
    strip = image[600:660, 200:1080].astype(float)
    return strip.mean() < 100.0


def test_captions_off_means_pixels_are_untouched():
    session, sink, now, _ = make_session(0, enabled=False)
    session.on_caption(ev(0.0, 5.0, HINDI, "hi"))
    drive(session, now, seed=100)
    assert session.counts["caption_frames"] == 0
    assert not any(_has_caption(r.image, r.pts) for r in sink.records)


def test_config_change_over_the_bus_toggles_captions():
    bus = LocalBus()
    session, sink, now, _ = make_session(0, enabled=False, bus=bus)
    bus.publish(Topic.CAPTION, ev(0.0, 5.0, HINDI, "hi"))
    bus.publish(Topic.CONFIG, ConfigChange({"captions.enabled": True}, "test"))
    assert session.captions.enabled
    bus.publish(Topic.CONFIG, ConfigChange({"captions.enabled": "maybe"}, "test"))
    assert session.counts["config_ignored"] == 1 and session.captions.enabled


def test_captions_over_a_mask_cannot_unmask_it():
    """A SOLID mask covers the whole caption box. Two runs with different underlying video (noise seeds)
    must emit bit-identical frames in the box: the captions only ever sit on top of the finished mask."""
    mask = Quad.from_xyxy(60, 500, 1220, 710)
    outs = []
    for seed in (1000, 5000):
        session, sink, now, _ = make_session(seed, mask=mask)
        session.on_caption(ev(0.0, 5.0, HINDI, "hi"))
        drive(session, now, seed=seed)
        assert session.counts["caption_frames"] > 5
        outs.append({r.pts: r.image for r in sink.records if session.counts["LIVE"]})
    common = sorted(set(outs[0]) & set(outs[1]))
    assert len(common) > 5
    checked = 0
    for pts in common:
        a, b = outs[0][pts], outs[1][pts]
        if _has_caption(a, pts):
            checked += 1
            assert np.array_equal(a[520:690, 80:1200], b[520:690, 80:1200])  # masked area incl. caption
    assert checked > 5


def test_no_caption_on_protect_hold_or_slate_frames():
    session, _, _, _ = make_session(0)
    session.captions.add(ev(0.0, 5.0, HINDI, "hi"))
    decision = SimpleNamespace(pts=1.0, seq=30)
    for kind, state in (("mean", "PROTECT"), ("slate", "PROTECT"), ("slate", "HOLD")):
        base = np.full((H, W, 3), 7, np.uint8)
        composed = Composed(base, kind, state, None, 0, 0.0, False)
        out = session._burn_captions(decision, composed)
        assert out is composed and (out.image == 7).all()
    live = Composed(np.full((H, W, 3), 7, np.uint8), "frame", "LIVE", None, 0, 0.0, False)
    assert session._burn_captions(decision, live).image.min() == 0 or (live.image != 7).any()
    session._audio_stale = True  # unchecked audio: its caption text is unchecked too
    stale = Composed(np.full((H, W, 3), 7, np.uint8), "frame", "LIVE", None, 0, 0.0, False)
    assert (session._burn_captions(decision, stale).image == 7).all()


def test_a_broken_renderer_never_stops_the_frame(monkeypatch, caplog):
    session, _, _, _ = make_session(0)
    session.captions.add(ev(0.0, 5.0, MARKER, "hi"))
    monkeypatch.setattr(
        session.captions, "render", lambda img, pts: (_ for _ in ()).throw(RuntimeError(MARKER))
    )
    live = Composed(np.full((H, W, 3), 7, np.uint8), "frame", "LIVE", None, 0, 0.0, False)
    with caplog.at_level(logging.DEBUG):
        out = session._burn_captions(SimpleNamespace(pts=1.0, seq=30), live)
    assert out is live and session.counts["caption_errors"] == 1 and MARKER not in caplog.text


def test_pts_reset_clears_pending_captions():
    session, _, _, _ = make_session(0)
    session.captions.add(ev(50.0, 60.0))
    session._on_reset(0)
    assert not session.captions._entries


# ------------------------------------------------------------------ replacement (redaction path) and newlines


def test_same_window_republish_replaces_the_earlier_text_immediately():
    """The caption service republishes a redacted caption when a mute span arrives late: the old text must not
    be drawn on the next frame, even when the earlier event was final and already shaped."""
    r = CaptionRenderer(enabled=True)
    r.add(ev(1.0, 2.0, "my number is 98765", final=True))
    first = np.zeros((H, W, 3), np.uint8)
    assert r.render(first, 1.2)
    r.add(ev(1.0, 2.0, "my number is [redacted]", final=True))
    assert len(r._entries) == 1 and r.active(1.5).text == "my number is [redacted]"
    again = np.zeros((H, W, 3), np.uint8)
    assert r.render(again, 1.3)
    assert not np.array_equal(first, again)  # a different picture: the old words are gone
    # and the old text does not come back after a shorter replacement ends
    r.add(ev(5.0, 6.0, "long unredacted text here", final=True))
    r.add(ev(5.0, 5.5, "[redacted]", final=True))
    assert r.active(5.8) is None and r.active(5.2).text == "[redacted]"


def test_replacement_that_fails_to_shape_draws_nothing_not_the_old_text(monkeypatch):
    r = CaptionRenderer(enabled=True)
    r.add(ev(1.0, 2.0, "secret words"))
    monkeypatch.setattr(r, "prepare", lambda e: (_ for _ in ()).throw(RuntimeError("x")))
    r.add(ev(1.0, 2.0, "redacted words"))
    assert r.render(np.zeros((H, W, 3), np.uint8), 1.5) is False


def test_overlapping_captions_latest_pts_start_wins():
    r = CaptionRenderer(enabled=True)
    r.add(ev(1.0, 3.0, "earlier"))
    r.add(ev(2.0, 4.0, "later"))
    assert r.active(1.5).text == "earlier"
    assert r.active(2.5).text == "later"  # both cover 2.5: the later start wins
    r.add(ev(0.5, 3.5, "even earlier start, arrived last"))
    assert r.active(2.5).text == "later"  # arrival order does not matter, pts_start does


def test_newlines_are_drawn_as_given_two_lines_of_at_most_42_chars(renderer):
    line1, line2 = "यह पहली पंक्ति है जो काफ़ी छोटी है", "and a second line in English"
    _, lines, truncated = renderer.layout(f"{line1}\n{line2}", "hi")
    assert lines == [line1, line2] and not truncated
    # a short first line is NOT merged with the second
    _, lines, _ = renderer.layout("hello\nworld", "en")
    assert lines == ["hello", "world"]
    # full-width 42-character lines stay as given
    a, b = "a" * 20 + " " + "b" * 21, "c" * 42
    assert char_count(a) == 42
    _, lines, truncated = renderer.layout(f"{a}\n{b}", "en")
    assert lines == [a, b] and not truncated
    # three given lines: capped at two, the second ends with an ellipsis
    _, lines, truncated = renderer.layout("one\ntwo\nthree", "en")
    assert len(lines) == 2 and truncated and lines[1].endswith("...")
    # the box has two lines of text
    assert renderer.prepare(ev(text=f"{line1}\n{line2}", lang="hi")).lines == 2
