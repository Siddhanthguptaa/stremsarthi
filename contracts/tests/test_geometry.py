import pytest

from contracts import Quad


def test_from_xyxy_and_bbox():
    q = Quad.from_xyxy(10, 20, 110, 80)
    assert q.bbox() == (10, 20, 110, 80)
    assert q.center() == (60, 50)
    assert q.area_bbox() == 100 * 60
    with pytest.raises(ValueError):
        Quad.from_xyxy(10, 20, 5, 80)


def test_padded_contains_original_and_is_exact_for_boxes():
    q = Quad.from_xyxy(100, 100, 200, 160)
    assert q.padded(12).bbox() == (88, 88, 212, 172)


def test_padded_rotated_quad_still_contains_original():
    q = Quad(((50, 10), (90, 50), (50, 90), (10, 50)))  # diamond
    p = q.padded(5).bbox()
    o = q.bbox()
    assert p[0] <= o[0] and p[1] <= o[1] and p[2] >= o[2] and p[3] >= o[3]


def test_clamped_stays_on_canvas():
    q = Quad.from_xyxy(-20, -5, 1300, 740).clamped()
    assert q.bbox() == (0, 0, 1280, 720)


def test_iou():
    a = Quad.from_xyxy(0, 0, 10, 10)
    assert a.iou(a) == 1.0
    assert a.iou(Quad.from_xyxy(20, 20, 30, 30)) == 0.0
    assert a.iou(Quad.from_xyxy(5, 0, 15, 10)) == pytest.approx(50 / 150)


def test_needs_four_points():
    with pytest.raises(ValueError):
        Quad(((0, 0), (1, 1)))  # type: ignore[arg-type]
