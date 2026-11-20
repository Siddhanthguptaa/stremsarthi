"""The ``plates`` detector with fake OCR providers (CPU, no models): logic, privacy, fail-safe, the planner path.

Every plate below is a SPECIMEN: an invented registration, never matched against anything.
"""

from __future__ import annotations

import logging
import time

import numpy as np
import pytest

from contracts import (
    Category,
    Config,
    DetectContext,
    DetectorConfig,
    Device,
    Frame,
    Lane,
    MaskKind,
    OcrResult,
    Quad,
)
from contracts.fakes import InProcessBus
from vision import host as vhost
from vision.detectors.plates import DETECTOR
from vision.planner import planner_factory
from vision.registry import discover
from vision.resources import OcrUnavailableError, VisionResources

BLANK = np.full((720, 1280, 3), 235, np.uint8)
Box = tuple[str, tuple[int, int, int, int]]


def _ocr(boxes: list[Box], conf: float = 0.97):
    result = OcrResult(
        tuple(Quad.from_xyxy(*r) for _, r in boxes),
        tuple(t for t, _ in boxes),
        (conf,) * len(boxes),
        ("latin",) * len(boxes),
    )
    return lambda frame: result


def _detector(ocr, *, enabled: bool = True, **params):
    cfg = Config(profile="dev", _env_file=None)
    cfg.categories[Category.PLATE] = cfg.categories[Category.PLATE].model_copy(update={"enabled": enabled})
    res = VisionResources(cfg, ocr=ocr)
    det = DETECTOR()
    det.configure(DetectorConfig(categories=cfg.categories, device=Device.CPU, params=params), res)
    return det


def _run(det, image=BLANK, seq: int = 0):
    return det.detect(
        Frame(seq, seq / 4.0, 0.0, image), DetectContext(lane=Lane.ACCURATE, categories={}, stride=1)
    )


ONE_LINE: Box = ("KA 01 AB 1234", (500, 400, 800, 450))  # 300x50 text: a 3.7:1 plate once grown


def test_class_declaration_matches_the_plugin_contract() -> None:
    assert DETECTOR.id == "plates" and DETECTOR.lane is Lane.ACCURATE
    assert DETECTOR.categories == frozenset({Category.PLATE}) and DETECTOR.vram_mb == 0


def test_a_one_line_plate_is_masked_as_the_whole_plate() -> None:
    out = _run(_detector(_ocr([ONE_LINE])))
    assert len(out) == 1
    d = out[0]
    assert d.category is Category.PLATE and d.detector == "plates"
    assert d.extra["reason"] == "plate_standard" and d.extra["layout"] == "one_line" and d.extra["boxes"] == 1
    x1, y1, x2, y2 = d.quad.bbox()
    assert x1 < 500 and y1 < 400 and x2 > 800 and y2 > 450  # grown past the text: border and IND strip
    assert 0.5 <= d.confidence <= 0.99


def test_a_bharat_series_plate() -> None:
    out = _run(_detector(_ocr([("22 BH 1234 AA", (500, 400, 800, 450))])))
    assert [(d.category, d.extra["reason"], d.extra["series"]) for d in out] == [
        (Category.PLATE, "plate_bh", "bh")
    ]


def test_plate_split_over_boxes_of_one_line() -> None:
    boxes: list[Box] = [
        ("KA 01", (500, 400, 590, 450)),
        ("AB", (610, 400, 680, 450)),
        ("1234", (700, 400, 800, 450)),
    ]
    out = _run(_detector(_ocr(boxes)))
    assert len(out) == 1 and out[0].extra["boxes"] == 3
    x1, _, x2, _ = out[0].quad.bbox()
    assert x1 < 500 and x2 > 800


def test_two_line_plate_is_joined_top_to_bottom() -> None:
    boxes: list[Box] = [("KA 01", (520, 400, 600, 440)), ("AB 1234", (505, 445, 615, 485))]
    out = _run(_detector(_ocr(boxes)))
    assert len(out) == 1 and out[0].extra["layout"] == "two_line" and out[0].extra["boxes"] == 2
    x1, y1, x2, y2 = out[0].quad.bbox()
    assert x1 <= 505 and y1 <= 400 and x2 >= 615 and y2 >= 485
    swapped = [boxes[1], boxes[0]]  # the OCR order does not matter, the geometry does
    assert len(_run(_detector(_ocr(swapped)))) == 1


def test_two_lines_far_apart_or_side_by_side_are_not_a_two_line_plate() -> None:
    far: list[Box] = [("KA 01", (520, 300, 600, 340)), ("AB 1234", (505, 500, 615, 540))]
    assert _run(_detector(_ocr(far))) == []
    wrong_order: list[Box] = [("AB 1234", (520, 400, 630, 440)), ("KA 01", (505, 445, 585, 485))]
    assert _run(_detector(_ocr(wrong_order))) == []  # reads AB1234KA01: not a plate


@pytest.mark.parametrize(
    "text",
    [
        "KA01AB1234 parked outside",
        "SPECIMEN CAR",
        "Order #AB123456",
        "1234 5678",
        "KA 01 123",
        "Call 9876543210",
    ],
)
def test_plate_lookalikes_are_not_masked(text: str) -> None:
    assert _run(_detector(_ocr([(text, (500, 400, 800, 450))]))) == []


def test_the_shape_must_be_plate_like() -> None:
    too_wide: Box = ("KA 01 AB 1234", (100, 400, 1000, 440))  # a 20:1 line of text
    assert _run(_detector(_ocr([too_wide]))) == []
    too_tall: Box = ("KA 01 AB 1234", (500, 100, 540, 500))
    assert _run(_detector(_ocr([too_tall]))) == []
    tiny: Box = ("KA 01 AB 1234", (500, 400, 560, 406))  # 6 px high: not legible
    assert _run(_detector(_ocr([tiny]))) == []
    huge: Box = ("KA 01 AB 1234", (0, 0, 1280, 500))  # most of the frame: not a plate
    assert _run(_detector(_ocr([huge]))) == []


def test_a_vehicle_gate_can_veto_and_the_default_accepts_on_format_alone() -> None:
    seen: list[tuple[int, ...]] = []

    def gate(image: np.ndarray, quad: np.ndarray) -> bool:
        seen.append(quad.shape)
        return False

    assert _run(_detector(_ocr([ONE_LINE]), vehicle_gate=gate)) == [] and seen == [(4, 2)]
    out = _run(_detector(_ocr([ONE_LINE]), vehicle_gate=lambda image, quad: True))
    assert len(out) == 1 and out[0].extra["vehicle_gate"] is True
    assert _run(_detector(_ocr([ONE_LINE])))[0].extra["vehicle_gate"] is False


def test_disabled_category_yields_nothing_and_skips_the_ocr() -> None:
    calls: list[int] = []

    def ocr(frame: Frame) -> OcrResult:
        calls.append(frame.seq)
        return OcrResult((), (), (), ())

    assert _run(_detector(ocr, enabled=False)) == [] and calls == []


def test_codes_only_in_the_detection_never_the_plate_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    out = _run(_detector(_ocr([ONE_LINE])))
    assert out
    for d in out:
        for value in d.extra.values():
            assert not isinstance(value, str) or ("KA" not in value and "1234" not in value)
        assert "AB1234" not in repr(d) and "AB 1234" not in repr(d)
    assert "1234" not in caplog.text and "KA01" not in caplog.text


def test_ocr_unavailable_fails_the_lane_run_and_detect_before_configure_raises() -> None:
    def broken(frame: Frame) -> OcrResult:
        raise OcrUnavailableError("models missing")

    with pytest.raises(OcrUnavailableError):
        _run(_detector(broken))
    with pytest.raises(RuntimeError):
        _run(DETECTOR())


def test_plates_is_discovered_and_reaches_the_planner_as_a_mask_request() -> None:
    assert discover(only=["plates"])["plates"] is DETECTOR
    cfg = Config(profile="dev", _env_file=None)
    res = VisionResources(cfg, ocr=_ocr([ONE_LINE]))
    host = vhost.VisionHost(
        cfg, InProcessBus(), registry={"plates": DETECTOR}, resources=res, assembler_factory=planner_factory
    )
    host.start()
    try:
        host.submit(Frame(0, 0.0, 0.0, BLANK))
        got = None
        end = time.monotonic() + 5.0
        while got is None and time.monotonic() < end:
            for r in host.poll():
                if r.lane is Lane.ACCURATE and r.frame_seq == 0:
                    got = r
            time.sleep(0.005)
    finally:
        host.close()
    assert got is not None and got.ok and len(got.requests) == 1
    req = got.requests[0]
    assert req.category is Category.PLATE and req.kind is MaskKind.REGION and req.source == "plates"
    x1, y1, x2, y2 = req.quads[0].bbox()
    assert x1 < 500 and y1 < 400 and x2 > 800 and y2 > 450
