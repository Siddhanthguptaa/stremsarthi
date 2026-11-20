"""MOUTH_OF_SPEAKER resolution and painting (docs/specs/engine.md 5.7, T3.4). CPU only, synthetic frames."""

from __future__ import annotations

import time

import numpy as np

from contracts import Category, Detection, Lane, LaneResult, MaskKind, MaskStyle, Quad
from engine.compositor import Compositor
from engine.mouth import MIN_MOUTH_KERNEL, MOUTH_PAD, pad_quad, pick_source, resolve_mouth_quads
from engine.testing import FPS, T0, lane_result, make_engine, make_frame, region_request

W, H = 1280, 720
MAX_AGE = {Lane.FAST: 6, Lane.ACCURATE: 12, Lane.SLOW: 60}


def face(seq: int, mouth: tuple[float, float, float, float], *, allow: bool = True, with_mouth: bool = True):
    x1, y1, x2, y2 = mouth
    return Detection(
        "fake", Category.FACE, Quad.from_xyxy(x1 - 40, y1 - 90, x2 + 40, y2 + 10), 0.9, seq, seq / FPS, 0.0,
        allow=allow, aux_quads={"mouth": Quad.from_xyxy(*mouth)} if with_mouth else {},
    )  # fmt: skip


def result(seq: int, *faces: Detection, lane: Lane = Lane.FAST, ok: bool = True, done: float = 0.0):
    return LaneResult(lane, seq, seq / FPS, done or T0 + seq / FPS + 0.05, ok, aux_detections=tuple(faces))


def mouth_request(start: float, end: float, rid: str = "m1"):
    return region_request(rid, (0, 0, 1, 1), kind=MaskKind.MOUTH_OF_SPEAKER, pts_start=start, pts_end=end,
                          category=Category.SPOKEN_PHONE, frame_pts=start)  # fmt: skip


def textured(seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 256, (H, W, 3), dtype=np.uint8)
    img.setflags(write=False)
    return img


def test_pad_quad_grows_each_side_by_a_quarter_of_the_size():
    padded = pad_quad(Quad.from_xyxy(100, 200, 180, 240))
    x1, y1, x2, y2 = padded.bbox()
    assert (x1, y1, x2, y2) == (100 - 0.25 * 80, 200 - 0.25 * 40, 180 + 0.25 * 80, 240 + 0.25 * 40)
    assert MOUTH_PAD == 0.25


def test_pick_source_takes_nearest_result_with_an_allowlisted_mouth_newest_on_a_tie():
    results = [
        result(10, face(10, (0, 0, 10, 10))),
        result(14, face(14, (1, 1, 11, 11))),
        result(12, face(12, (2, 2, 12, 12), allow=False)),  # not allowlisted: never a source
        result(13, face(13, (3, 3, 13, 13), with_mouth=False)),  # no mouth quad
        result(11, ok=False),
    ]
    window = (0, 40)
    assert pick_source(results, 12, 1e9, window, MAX_AGE).frame_seq == 14  # |14-12| = |10-12|, newest wins
    assert pick_source(results, 10, 1e9, window, MAX_AGE).frame_seq == 10
    assert pick_source(results, 30, 1e9, window, MAX_AGE) is None  # older than the FAST lane's 6 frames
    assert pick_source(results, 12, T0, window, MAX_AGE) is None  # finished after the frame's deadline
    assert pick_source(results, 12, 1e9, (13, 40), MAX_AGE).frame_seq == 14  # outside the window: skipped


def test_nothing_resolved_without_a_visible_allowlisted_face():
    ident = lambda flat, src, dst, dl: flat  # noqa: E731
    none_visible = [result(5), result(6, face(6, (1, 1, 9, 9), allow=False))]
    assert resolve_mouth_quads(none_visible, 6, 1e9, (0, 40), MAX_AGE, ident) == ()


def test_projection_follows_the_motion_path_and_is_padded():
    def shift(flat, src, dst, deadline):  # camera pans 2 px per frame to the right
        return [[[x + 2.0 * (dst - src), y] for x, y in quad] for quad in flat]

    out = resolve_mouth_quads([result(10, face(10, (100, 200, 180, 240)))], 13, 1e9, (0, 40), MAX_AGE, shift)
    assert len(out) == 1
    assert out[0].bbox() == (106 - 20.0, 200 - 10.0, 186 + 20.0, 240 + 10.0)
    assert resolve_mouth_quads([result(10, face(10, (1, 1, 9, 9)))], 13, 1e9, (0, 40), MAX_AGE,
                               lambda *a: None) == ()  # unresolved motion: skipped, never guessed  # fmt: skip


def test_every_allowlisted_face_gets_its_mouth_blurred():
    ident = lambda flat, src, dst, dl: flat  # noqa: E731
    r = result(4, face(4, (100, 100, 140, 120)), face(4, (500, 300, 540, 320)))
    assert len(resolve_mouth_quads([r], 4, 1e9, (0, 40), MAX_AGE, ident)) == 2


def _engine_with(results, request, *, frames: int = 40):
    engine = make_engine((Lane.FAST,), delay=1.0, max_buffer_frames=60, max_poll_frames=60)
    for seq in range(frames):
        engine.submit(make_frame(seq, size=16), now=T0 + seq / FPS)
    for seq in range(frames):
        engine.submit_result(lane_result(Lane.FAST, seq, T0 + seq / FPS + 0.05))  # heartbeat for coverage
    for res in results:
        engine.submit_result(res)
    engine.submit_request(request)
    engine.end(frames - 1)  # nothing after the last frame: no ingest-gap frames
    return engine


def test_engine_resolves_a_face_moving_across_the_frame_in_exactly_the_request_window():
    # the mouth moves 10 px per frame; vision reports every 3rd frame; the request covers frames 12..24
    results = [
        result(s, face(s, (100 + 10 * s, 300, 160 + 10 * s, 330)), done=T0 + s / FPS + 0.05)
        for s in range(0, 40, 3)
    ]
    engine = _engine_with(results, mouth_request(12 / FPS, 24 / FPS))
    decisions = engine.poll(now=T0 + 5.0)
    by_seq = {d.seq: d for d in decisions}
    assert all(d.live for d in decisions[:36])
    for seq, d in by_seq.items():
        if 12 <= seq <= 24:
            assert len(d.mouth_requests) == 1 and len(d.mouth_quads) == 1, seq
            x1, y1, x2, y2 = d.mouth_quads[0].bbox()
            true_x = 100 + 10 * seq  # where the mouth really is on this frame
            # the nearest result is at most 1 frame (10 px) away: the padded quad (15 px) still covers it
            assert x1 <= true_x and x2 >= true_x + 60, (seq, x1, x2)
            assert y1 < 300 and y2 > 330
        else:
            assert d.mouth_requests == () and d.mouth_quads == (), seq


def test_engine_skips_the_mouth_silently_when_no_allowlisted_face_is_visible():
    results = [result(s, face(s, (1, 1, 9, 9), allow=False)) for s in range(0, 40, 3)] + [
        result(s, lane=Lane.FAST) for s in range(1, 40, 3)
    ]
    engine = _engine_with(results, mouth_request(0.0, 40 / FPS))
    decisions = engine.poll(now=T0 + 5.0)
    assert decisions and all(d.live and d.mouth_quads == () for d in decisions)
    assert any(d.mouth_requests for d in decisions)  # the request is active, there is just no mouth


def test_hold_recheck_drops_the_mouth_quads():
    results = [result(s, face(s, (100, 300, 160, 330))) for s in range(0, 40, 3)]
    engine = _engine_with(results, mouth_request(0.0, 40 / FPS))
    decision = engine.poll(now=T0 + 1.2)[0]
    assert decision.mouth_quads
    engine.hold("hotkey", True, now=T0 + 1.2)
    assert engine.recheck(decision).mouth_quads == ()


def test_compositor_blurs_the_mouth_strongly_and_leaves_everything_else_untouched():
    comp = Compositor(width=W, height=H)
    engine = _engine_with([result(3, face(3, (600, 400, 660, 430)))], mouth_request(0.0, 40 / FPS))
    engine.submit_request(mouth_request(0.0, 40 / FPS, "m2"))
    base = textured()
    decision = engine.poll(now=T0 + 1.2)[0]
    decision.image = base
    out = comp.compose(decision).image
    x1, y1, x2, y2 = (int(v) for v in decision.mouth_quads[0].bbox())
    core = (slice(400, 430), slice(600, 660))  # the mouth itself
    assert base[core].std() > 70 and out[core].std() < 20  # noise in, a flat smear out
    mask = np.ones((H, W), bool)
    mask[max(0, y1 - 8) : y2 + 8, max(0, x1 - 8) : x2 + 8] = False
    assert np.array_equal(out[mask], base[mask])  # nothing else was touched
    assert MIN_MOUTH_KERNEL == 51


def test_mouth_blur_ignores_the_face_style_override_and_stays_blur():
    comp = Compositor(width=W, height=H)
    comp.style_overrides[Category.FACE] = MaskStyle.SOLID
    engine = _engine_with([result(3, face(3, (600, 400, 660, 430)))], mouth_request(0.0, 40 / FPS))
    decision = engine.poll(now=T0 + 1.2)[0]
    decision.image = textured()
    out = comp.compose(decision).image
    assert out[400:430, 600:660].std() > 0.5  # blurred, not a flat solid fill (std 0)


def test_mouth_paint_cost_is_under_three_milliseconds_per_frame():
    """Acceptance T3.4: mouth paint < 3 ms per frame (two mouths, 720p), measured as compose with minus without."""
    comp = Compositor(width=W, height=H)
    engine = _engine_with(
        [result(s, face(s, (600, 400, 680, 440)), face(s, (200, 300, 270, 335))) for s in range(0, 40, 3)],
        mouth_request(0.0, 40 / FPS),
    )
    decisions = engine.poll(now=T0 + 5.0)
    base = textured()
    with_mouth, without = [], []
    for d in decisions * 3:
        d.image = base
        assert d.mouth_quads
        began = time.perf_counter()
        comp.compose(d)
        with_mouth.append(time.perf_counter() - began)
        quads, d.mouth_quads = d.mouth_quads, ()
        began = time.perf_counter()
        comp.compose(d)
        without.append(time.perf_counter() - began)
        d.mouth_quads = quads
    paint_ms = 1000 * (float(np.median(with_mouth)) - float(np.median(without)))
    print(f"mouth paint: {paint_ms:.3f} ms per frame (median, 2 mouths, 720p); "
          f"compose with {1000 * np.median(with_mouth):.3f} ms, without {1000 * np.median(without):.3f} ms")  # fmt: skip
    assert paint_ms < 3.0


def test_allowlisted_face_without_a_mouth_quad_gets_the_lower_half_of_its_box_blurred():
    ident = lambda flat, src, dst, dl: flat  # noqa: E731
    no_mouth = face(5, (0, 0, 1, 1), with_mouth=False)  # its face box is (-40, -90, 41, 11)
    box = no_mouth.quad.bbox()
    out = resolve_mouth_quads([result(5, no_mouth)], 5, 1e9, (0, 40), MAX_AGE, ident)
    assert len(out) == 1
    x1, y1, x2, y2 = out[0].bbox()
    mid = (box[1] + box[3]) / 2.0
    assert y2 >= box[3] and y1 <= mid  # covers the lower half (plus padding)
    assert x1 <= box[0] and x2 >= box[2]
    assert y1 > box[1]  # ... and is not the whole face


def test_a_result_with_real_mouth_quads_beats_a_closer_one_with_only_face_boxes():
    results = [result(12, face(12, (0, 0, 1, 1), with_mouth=False)), result(14, face(14, (1, 1, 11, 11)))]
    assert pick_source(results, 12, 1e9, (0, 40), MAX_AGE).frame_seq == 14
    only_box = [result(12, face(12, (0, 0, 1, 1), with_mouth=False))]
    assert pick_source(only_box, 12, 1e9, (0, 40), MAX_AGE).frame_seq == 12  # the fallback still counts
