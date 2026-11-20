"""Compositor and blur on the CPU with synthetic frames (docs/specs/engine.md 5.3, 5.5; tests 10, 11)."""

from __future__ import annotations

import statistics
import time

import cv2
import numpy as np
import pytest

from contracts import Category, MaskKind, MaskStyle, Quad
from engine.blur import blur_kernel, paint_polygon, pixelate_block, polygon_union_ratio, quad_polygon
from engine.compositor import Compositor
from engine.deadline import FrameDecision, ProjectedMask
from engine.testing import region_request

H, W = 720, 1280


def noise_frame(seed: int = 1) -> np.ndarray:
    image = np.random.default_rng(seed).integers(0, 256, (H, W, 3), dtype=np.uint8)
    image.setflags(write=False)
    return image


def decision(
    image,
    masks=(),
    *,
    state="LIVE",
    reason="covered",
    presentation="frame",
    seq=0,
    full_frame=False,
) -> FrameDecision:
    return FrameDecision(
        seq=seq,
        pts=seq / 30,
        image=image,
        state=state,
        reason=reason,
        presentation=presentation,
        masks=tuple(masks),
        full_frame=full_frame,
        mouth_requests=(),
        deadline=0.0,
        emitted_at=0.0,
        protect_age=0.0,
        lane_ages={},
    )


def mask(xyxy, *, style=MaskStyle.BLUR, category=Category.PHONE_NUMBER, rid="r") -> ProjectedMask:
    request = region_request(rid, xyxy, category=category, style=style)
    return ProjectedMask(request, request.quads)


def changed_region(before: np.ndarray, after: np.ndarray) -> tuple[int, int, int, int] | None:
    diff = np.any(before != after, axis=2)
    if not diff.any():
        return None
    ys, xs = np.nonzero(diff)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


# ------------------------------------------------------------------ sizes (spec 5.5)


def test_kernel_and_block_sizes():
    assert blur_kernel(100, 40, Category.PHONE_NUMBER) == 15  # odd(max(15, 10))
    assert blur_kernel(480, 280, Category.FACE) == 71  # odd(0.25 * 280) = 71
    assert blur_kernel(100, 40, Category.AADHAAR) == 51  # at least 51 for cards
    assert blur_kernel(100, 40, Category.UPI_QR) == 51
    assert blur_kernel(100, 40, Category.PAN) == 51
    assert blur_kernel(400, 400, Category.PAN) == 101
    assert all(blur_kernel(w, h) % 2 == 1 for w in (30, 77, 300) for h in (13, 64, 250))
    assert pixelate_block(480, 280) == 35  # 280 // 8
    assert pixelate_block(40, 20) == 8  # floor of 8


# ------------------------------------------------------------------ painting


@pytest.mark.parametrize("style", list(MaskStyle))
def test_styles_change_only_the_quad_and_a_thin_margin(style):
    image = noise_frame()
    comp = Compositor()
    out = comp.compose(decision(image, [mask((300, 200, 700, 400), style=style)])).image
    box = changed_region(image, out)
    assert box is not None
    x1, y1, x2, y2 = box
    # the quad is covered and the change stays within pad + feather (about 6 px)
    assert x1 >= 300 - 6 and y1 >= 200 - 6 and x2 <= 700 + 7 and y2 <= 400 + 7
    interior = (slice(205, 395), slice(305, 695))
    assert not np.array_equal(image[interior], out[interior])
    if style is MaskStyle.SOLID:
        assert (out[interior] == 32).all()
    # bit-identical outside the margin
    outside = np.ones((H, W), bool)
    outside[190:410, 290:710] = False
    assert np.array_equal(image[outside], out[outside])
    assert not np.shares_memory(out, image)
    assert image.flags.writeable is False


def test_blur_destroys_detail_pixelate_is_blocky():
    image = noise_frame()
    comp = Compositor()
    blurred = comp.compose(decision(image, [mask((300, 200, 700, 400))])).image[210:390, 310:690]
    assert blurred.std() < 0.25 * image[210:390, 310:690].std()
    pix = comp.compose(decision(image, [mask((300, 200, 700, 400), style=MaskStyle.PIXELATE)])).image
    row = pix[300, 310:690].reshape(-1, 3)
    assert len({tuple(px) for px in row}) <= 20  # about 16 blocks across the quad; noise has hundreds


def test_rotated_quad_leaves_pixels_outside_the_polygon_alone():
    image = noise_frame()
    quad = Quad(((400, 200), (600, 260), (560, 440), (360, 380)))
    request = region_request("rot", (0, 0, 1, 1), style=MaskStyle.SOLID)
    pm = ProjectedMask(request, (quad,))
    out = Compositor().compose(decision(image, [pm])).image
    assert (out[320, 480] == 32).all()  # centre
    assert np.array_equal(out[210, 380], image[210, 380])  # inside the bbox, well outside the polygon
    assert np.array_equal(out[430, 590], image[430, 590])


def test_quad_outside_or_partly_outside_the_frame_is_safe():
    image = noise_frame()
    comp = Compositor()
    out = comp.compose(decision(image, [mask((1200, 650, 1500, 900), style=MaskStyle.SOLID)])).image
    assert (out[700, 1250] == 32).all()
    far = comp.compose(decision(image, [mask((-500, -500, -100, -100))]))
    assert far.kind == "frame" and np.array_equal(far.image, image)
    degenerate = Quad(((50.0, 50.0),) * 4)
    request = region_request("deg", (0, 0, 1, 1))
    comp.compose(decision(image, [ProjectedMask(request, (degenerate,))]))  # must not raise


def test_only_roi_crops_are_filtered_never_the_whole_frame(monkeypatch):
    shapes: list[tuple[int, ...]] = []
    for name in ("stackBlur", "GaussianBlur", "blur", "blendLinear", "resize"):
        real = getattr(cv2, name)

        def spy(src, *a, _real=real, **k):
            shapes.append(tuple(src.shape))
            return _real(src, *a, **k)

        monkeypatch.setattr(cv2, name, spy)
    image = noise_frame()
    comp = Compositor()
    comp.compose(
        decision(image, [mask((100 + 120 * k, 100 + 10 * k, 200 + 120 * k, 160 + 10 * k)) for k in range(8)])
    )
    comp.compose(decision(image, [mask((50, 50, 300, 200), style=MaskStyle.PIXELATE)]))
    assert shapes and all(s[0] < H and s[1] < W for s in shapes), shapes


def test_stricter_style_wins_on_overlap():
    image = noise_frame()
    solid = mask((300, 200, 500, 400), style=MaskStyle.SOLID, rid="solid")
    blur = mask((400, 300, 700, 500), style=MaskStyle.BLUR, rid="blur")
    for order in ((solid, blur), (blur, solid)):
        out = Compositor().compose(decision(image, order)).image
        assert (out[320:380, 320:380] == 32).all()  # the solid core is never blended away


def test_style_override_from_config_change():
    image = noise_frame()
    comp = Compositor()
    comp.style_overrides[Category.PHONE_NUMBER] = MaskStyle.SOLID
    out = comp.compose(decision(image, [mask((300, 200, 700, 400))])).image
    assert (out[210:390, 310:690] == 32).all()


# ------------------------------------------------------------------ collapse (spec test 11)


def test_full_frame_request_collapses_to_one_solid_fill():
    result = Compositor().compose(decision(noise_frame(), full_frame=True))
    assert result.kind == "solid" and result.collapsed and result.state == "LIVE"
    assert (result.image == 32).all()


def test_masked_area_above_the_limit_collapses_to_solid():
    image = noise_frame()
    comp = Compositor()
    big = comp.compose(decision(image, [mask((0, 0, 1280, 500))]))  # 69 % of the frame
    assert big.kind == "solid" and big.collapsed and (big.image == 32).all()
    ok = comp.compose(decision(image, [mask((0, 0, 1280, 400))]))  # 55 %
    assert ok.kind == "frame" and not ok.collapsed


def test_overlapping_quads_count_their_union_not_their_sum():
    image = noise_frame()
    stacked = [
        mask((100, 100, 900, 500), rid=f"r{k}") for k in range(4)
    ]  # 4 x 35 % = 140 % summed, 35 % union
    result = Compositor().compose(decision(image, stacked))
    assert result.kind == "frame" and not result.collapsed
    polys = [quad_polygon(np.array(q.quads[0].pts), W, H) for q in (p.request for p in stacked)]
    assert 0.3 < polygon_union_ratio(polys, W, H) < 0.4


def test_config_ratio_is_used():
    comp = Compositor(max_area_ratio=0.2)
    assert comp.compose(decision(noise_frame(), [mask((0, 0, 640, 360))])).collapsed  # 25 %


# ------------------------------------------------------------------ PROTECT, HOLD, fence


def test_protect_is_mean_colour_then_slate_and_hold_is_slate():
    image = noise_frame()
    comp = Compositor()
    mean = comp.compose(decision(image, state="PROTECT", reason="missing-fast", presentation="mean"))
    assert mean.kind == "mean" and mean.state == "PROTECT" and not mean.audio_silent
    assert (mean.image == mean.image[0, 0]).all()  # a single colour: no spatial detail survives
    slate = comp.compose(decision(image, state="PROTECT", reason="missing-fast", presentation="slate"))
    assert slate.kind == "slate" and slate.slate == "PROTECT"
    assert np.array_equal(slate.image, comp.presenter.store.get("PROTECT"))
    held = comp.compose(decision(image, state="HOLD", reason="hotkey", presentation="slate"))
    assert held.kind == "slate" and held.slate == "HOLD" and held.audio_silent
    transport = comp.compose(decision(image, state="HOLD", reason="auto_transport", presentation="slate"))
    assert transport.slate == "TRANSPORT"
    assert not np.shares_memory(held.image, comp.presenter.store.get("HOLD"))  # a copy each time


def test_slates_never_contain_source_pixels():
    image = noise_frame()
    comp = Compositor()
    for state in ("PROTECT", "HOLD"):
        out = comp.compose(decision(image, state=state, presentation="slate")).image
        assert abs(float(out.mean()) - float(image.mean())) > 20  # noise averages ~127; slates are dark
        assert np.count_nonzero(out == image) < 0.01 * out.size


def test_gap_frame_without_image_and_wrong_shape_are_slates():
    comp = Compositor()
    none = comp.compose(decision(None, state="PROTECT", reason="ingest-gap", presentation="mean"))
    assert none.kind == "slate"
    small = np.zeros((32, 32, 3), np.uint8)
    wrong = comp.compose(decision(small))  # a LIVE decision on a frame of the wrong size never passes through
    assert wrong.kind == "slate" and wrong.slate == "FAILURE" and wrong.state == "HOLD"


def test_fence_slates_older_live_frames_after_a_hold():
    comp = Compositor()
    image = noise_frame()
    comp.compose(
        decision(image, state="HOLD", reason="hotkey", presentation="slate", seq=10), capture_highwater=40
    )
    stale = comp.compose(decision(image, seq=30), capture_highwater=45)
    assert stale.kind == "slate" and stale.state == "HOLD" and stale.audio_silent
    fresh = comp.compose(decision(image, seq=41), capture_highwater=45)
    assert fresh.kind == "frame" and fresh.state == "LIVE"


def test_protect_does_not_fence_by_default():
    comp = Compositor()
    image = noise_frame()
    comp.compose(decision(image, state="PROTECT", presentation="slate", seq=10), capture_highwater=40)
    assert comp.compose(decision(image, seq=11), capture_highwater=45).kind == "frame"


# ------------------------------------------------------------------ readability and speed


def _specimen_card() -> np.ndarray:
    card = np.full((280, 480, 3), 235, np.uint8)
    cv2.putText(card, "SPECIMEN", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (30, 30, 30), 3)
    cv2.putText(card, "0123 4567 8901", (20, 160), cv2.FONT_HERSHEY_DUPLEX, 1.6, (20, 20, 20), 3)
    return card


@pytest.mark.parametrize("style", list(MaskStyle))
def test_ocr_cannot_read_a_masked_specimen_card(style):
    rapidocr = pytest.importorskip("rapidocr")
    ocr = rapidocr.RapidOCR()
    frame = np.full((H, W, 3), 60, np.uint8)
    frame[200:480, 400:880] = _specimen_card()
    frame.setflags(write=False)
    digits = lambda img: "".join(ch for t in (ocr(img).txts or ()) for ch in t if ch.isdigit())  # noqa: E731
    assert len(digits(frame)) >= 12, "sanity: the unmasked specimen is readable"
    request = region_request("card", (400, 200, 880, 480), category=Category.AADHAAR, style=style)
    out = Compositor().compose(decision(frame, [ProjectedMask(request, request.quads)])).image
    assert len(digits(out)) < 12
    assert digits(out) == "" or style is MaskStyle.PIXELATE  # blur and solid leave nothing to read
    outside = np.ones((H, W), bool)
    outside[170:510, 370:910] = False
    assert np.array_equal(out[outside], frame[outside])


def test_forty_quads_fit_the_30_ms_budget():
    image = noise_frame()
    rng = np.random.default_rng(3)
    masks = []
    for k in range(40):
        x, y = int(rng.integers(0, 1000)), int(rng.integers(0, 640))
        w, h = int(rng.integers(150, 300)), int(rng.integers(30, 80))
        masks.append(mask((x, y, x + w, y + h), rid=f"q{k}"))
    comp = Compositor()
    d = decision(image, masks)
    comp.compose(d)  # warm up
    times = []
    for _ in range(40):
        began = time.perf_counter()
        out = comp.compose(d)
        times.append((time.perf_counter() - began) * 1000.0)
    assert out.quads_painted == 40 and out.kind == "frame"
    p50 = statistics.median(times)
    print(
        f"compositor 40 quads BLUR 720p: p50 {p50:.1f} ms, p95 {sorted(times)[37]:.1f} ms, max {max(times):.1f} ms"
    )
    assert p50 <= 30.0


def test_polygon_union_ratio_and_quad_polygon_are_bounded():
    huge = np.array([[-1e9, -1e9], [1e9, -1e9], [1e9, 1e9], [-1e9, 1e9]])
    poly = quad_polygon(huge, W, H)
    assert polygon_union_ratio([poly], W, H) > 0.99
    image = noise_frame()
    out = image.copy()
    assert paint_polygon(out, huge, MaskStyle.SOLID)
    assert (out == 32).all() or (out[10:-10, 10:-10] == 32).all()
    assert MaskKind.FULL_FRAME is not MaskKind.REGION
