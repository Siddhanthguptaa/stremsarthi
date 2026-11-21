"""The ``text`` detector with fake OCR providers (CPU, no models): logic, privacy, fail-safe, the planner path.

Every string below is a SPECIMEN or a fake: invented numbers, invented IDs, no personal data.
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
from vision.detectors.text import DETECTOR
from vision.planner import planner_factory
from vision.registry import discover
from vision.resources import OcrUnavailableError, VisionResources

BLANK = np.full((720, 1280, 3), 235, np.uint8)
Box = tuple[str, tuple[int, int, int, int]]  # (text, (x1, y1, x2, y2))


def _quad(rect: tuple[int, int, int, int]) -> Quad:
    x1, y1, x2, y2 = rect
    return Quad.from_xyxy(x1, y1, x2, y2)


def _ocr(boxes: list[Box], conf: float = 0.97, scripts: list[str] | None = None):
    result = OcrResult(
        tuple(_quad(r) for _, r in boxes),
        tuple(t for t, _ in boxes),
        (conf,) * len(boxes),
        tuple(scripts or ["latin"] * len(boxes)),
    )
    return lambda frame: result


def _cfg(**cats: dict) -> Config:
    cfg = Config(profile="dev", _env_file=None)
    for name, update in cats.items():
        cat = Category(name)
        cfg.categories[cat] = cfg.categories[cat].model_copy(update=update)
    return cfg


def _detector(cfg: Config, ocr, **params):
    res = VisionResources(cfg, ocr=ocr)
    det = DETECTOR()
    det.configure(DetectorConfig(categories=cfg.categories, device=Device.CPU, params=params), res)
    return det


def _run(det, image=BLANK, seq: int = 0):
    ctx = DetectContext(lane=Lane.ACCURATE, categories={}, stride=1)
    return det.detect(Frame(seq, seq / 4.0, 0.0, image), ctx)


def _only(dets, category: Category):
    return [d for d in dets if d.category is category]


# one text line 28 px tall at y=100
def _line(text: str, x: int = 100, y: int = 100, w: int = 320, h: int = 28) -> Box:
    return (text, (x, y, x + w, y + h))


def test_class_declaration_matches_the_plugin_contract() -> None:
    assert DETECTOR.id == "text" and DETECTOR.lane is Lane.ACCURATE
    assert DETECTOR.categories == frozenset(
        {
            Category.PHONE_NUMBER,
            Category.PIN_CODE,
            Category.UPI_ID,
            Category.EMAIL,
            Category.SIGNBOARD,
            Category.RISKY_TEXT,
        }
    )
    assert DETECTOR.vram_mb == 0  # the shared OCR is declared by ``cards``


# ---------------------------------------------------------------------------- structured PII
@pytest.mark.parametrize(
    ("text", "category", "reason"),
    [
        ("Call: +91 98765 43210", Category.PHONE_NUMBER, "phone_pattern"),
        ("WhatsApp 9876543210", Category.PHONE_NUMBER, "phone_pattern"),
        ("Mob. 98765-43210", Category.PHONE_NUMBER, "phone_pattern"),
        ("Pay: rahul.s@okhdfcbank", Category.UPI_ID, "upi_known"),
        ("UPI ID: 9876543210@ybl", Category.UPI_ID, "upi_known"),
        ("Mail: priya.sharma@gmail.com", Category.EMAIL, "email_pattern"),
        ("PIN: 560 001", Category.PIN_CODE, "pin_cue"),
        ("12, MG Road, Bengaluru - 560001", Category.PIN_CODE, "pin_cue"),
    ],
)
def test_structured_pii_masks_the_text_box(text: str, category: Category, reason: str) -> None:
    box = _line(text)
    det = _detector(_cfg(signboard={"enabled": False}), _ocr([box]))
    out = _run(det)
    assert len(out) == 1
    d = out[0]
    assert d.category is category and d.extra["reason"] == reason and d.detector == "text"
    assert d.quad.bbox() == pytest.approx(box[1])  # the OCR box; the planner adds the padding
    assert 0.5 <= d.confidence <= 0.99


def test_codes_only_in_extra_never_the_text() -> None:
    secret = "98765 43210"
    det = _detector(_cfg(), _ocr([_line(f"Call: +91 {secret}"), _line("Mail: a.b@gmail.com", y=160)]))
    for d in _run(det):
        for value in d.extra.values():
            assert not isinstance(value, str) or (
                secret not in value and "@" not in value and "gmail" not in value
            )
        assert secret not in repr(d) and "gmail" not in repr(d)


def test_indic_digits_in_the_ocr_text_are_normalised() -> None:
    det = _detector(
        _cfg(signboard={"enabled": False}), _ocr([_line("फोन: ९८७६५ ४३२१०"), _line("पिन कोड: ५६०००१", y=160)])
    )
    cats = sorted(d.category.value for d in _run(det))
    assert cats == ["phone_number", "pin_code"]


def test_phone_split_over_two_boxes_of_one_line_is_masked_as_one_quad() -> None:
    a, b = _line("+91", x=100, w=60), _line("98765 43210", x=175, w=200)
    det = _detector(_cfg(signboard={"enabled": False}), _ocr([a, b]))
    out = _run(det)
    phones = _only(out, Category.PHONE_NUMBER)
    assert any(d.extra["boxes"] == 2 and d.quad.bbox()[0] <= 100 and d.quad.bbox()[2] >= 375 for d in phones)
    c, d_ = _line("98765", x=100, w=120), _line("43210", x=240, w=120)  # digits split in the middle
    out = _run(_detector(_cfg(signboard={"enabled": False}), _ocr([c, d_])))
    assert any(d.extra["boxes"] == 2 for d in _only(out, Category.PHONE_NUMBER))


def test_a_phone_number_inside_a_upi_id_is_a_upi_id() -> None:
    out = _run(_detector(_cfg(signboard={"enabled": False}), _ocr([_line("9876543210@ybl")])))
    assert [d.category for d in out] == [Category.UPI_ID]


# ---------------------------------------------------------------------------- PIN gating
def test_pin_without_a_cue_is_masked_only_in_a_short_box_at_sensitivity_half() -> None:
    boxes = [_line("Score 560001")]
    on = _run(_detector(_cfg(signboard={"enabled": False}), _ocr(boxes)))
    assert [(d.category, d.extra["reason"]) for d in on] == [(Category.PIN_CODE, "pin_bare")]
    off = _run(_detector(_cfg(signboard={"enabled": False}, pin_code={"sensitivity": 0.3}), _ocr(boxes)))
    assert off == []
    long_text = "The total amount paid for the whole order was 560001 rupees in the end today"
    assert _run(_detector(_cfg(signboard={"enabled": False}), _ocr([_line(long_text, w=900)]))) == []


def test_pin_cue_in_a_nearby_box_counts_and_a_far_cue_does_not() -> None:
    near = [_line("Pincode", y=100, w=140), _line("560001", y=140, w=140)]
    out = _run(_detector(_cfg(signboard={"enabled": False}, pin_code={"sensitivity": 0.3}), _ocr(near)))
    assert [d.extra["reason"] for d in out] == ["pin_cue"]
    far = [_line("Pincode", y=100, w=140), _line("560001", y=600, w=140)]
    assert _run(_detector(_cfg(signboard={"enabled": False}, pin_code={"sensitivity": 0.3}), _ocr(far))) == []


def test_a_bare_pin_in_a_low_confidence_reading_is_not_trusted() -> None:
    cfg = _cfg(signboard={"enabled": False})
    assert (
        _run(_detector(cfg, _ocr([_line("352150")], conf=0.44))) == []
    )  # hallucinated digits on an Indic sign
    assert [d.extra["reason"] for d in _run(_detector(cfg, _ocr([_line("352150")], conf=0.9)))] == [
        "pin_bare"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Order #AB123456",
        "Call centre 1800 123 456",
        "Invoice no 1234567890",
        "Reference 000123",
        "Total 12,99,999",
    ],
)
def test_pin_lookalikes_are_not_masked(text: str) -> None:
    assert _run(_detector(_cfg(signboard={"enabled": False}), _ocr([_line(text)]))) == []


# ---------------------------------------------------------------------------- hard negatives
@pytest.mark.parametrize(
    "text",
    [
        "Date 12/03/2026 time 10:45",
        "Version 2.4.1 build 880 beta",
        "Follow @streamer_name on social",
        "Visit www.example.com/home now",
        "Server 192.168.1.1 ready",
        "KA01AB1234 parked outside",
        "Player level 87 rank 4321",
        "Price Rs 12,999 only",
        "1234567890",
        "Temperature 36.5 C humidity 70",
    ],
)
def test_lookalikes_produce_no_pii_detection(text: str) -> None:
    assert _run(_detector(_cfg(signboard={"enabled": False}), _ocr([_line(text)]))) == []


# ---------------------------------------------------------------------------- category switches
def test_disabled_categories_yield_nothing_and_skip_the_ocr() -> None:
    cfg = _cfg(
        phone_number={"enabled": False},
        pin_code={"enabled": False},
        upi_id={"enabled": False},
        email={"enabled": False},
        signboard={"enabled": False},
    )
    calls = []

    def ocr(frame: Frame) -> OcrResult:
        calls.append(frame.seq)
        return OcrResult((), (), (), ())

    assert _run(_detector(cfg, ocr)) == [] and calls == []


def test_one_disabled_category_does_not_mute_the_others() -> None:
    boxes = [_line("Call: +91 98765 43210"), _line("Pay: rahul@oksbi", y=160)]
    out = _run(_detector(_cfg(phone_number={"enabled": False}, signboard={"enabled": False}), _ocr(boxes)))
    assert [d.category for d in out] == [Category.UPI_ID]


# ---------------------------------------------------------------------------- signboards
def test_signboard_masks_every_tall_enough_box_in_any_script_without_recognition() -> None:
    scripts = ["devanagari", "kannada", "tamil", "telugu", "bengali"]
    boxes: list[Box] = [("", (100, 60 + 90 * i, 520, 100 + 90 * i)) for i in range(5)]  # not recognised
    result = OcrResult(
        tuple(_quad(r) for _, r in boxes), ("",) * 5, (0.8,) * 5, tuple("unknown" for _ in scripts)
    )
    out = _run(_detector(_cfg(), lambda f: result))
    signs = _only(out, Category.SIGNBOARD)
    assert len(signs) == 5 and all(d.extra["reason"] == "mask_all_text" for d in signs)
    assert all(0.5 < d.confidence <= 0.99 for d in signs)


def test_signboard_size_and_confidence_filters() -> None:
    boxes = [
        ("", (100, 100, 400, 140)),  # tall enough, det conf 0.8
        ("", (100, 200, 400, 210)),  # 10 px: under the 14 px floor
        ("", (100, 300, 400, 340)),  # det conf 0.3: under the floor
        ("", (0, 0, 1000, 700)),  # most of the frame: not a text line
    ]
    result = OcrResult(tuple(_quad(r) for _, r in boxes), ("",) * 4, (0.8, 0.9, 0.3, 0.9), ("unknown",) * 4)
    out = _only(_run(_detector(_cfg(), lambda f: result)), Category.SIGNBOARD)
    assert len(out) == 1 and out[0].quad.bbox()[1] == 100


def test_signboard_does_not_mask_a_picture_sized_square_box_but_keeps_text_lines_and_small_squares() -> None:
    """A big near-square region is a photo or a UI tile (it would blank a face); text lines and a single big
    glyph-sized box (small area) are still masked."""
    boxes = [
        ("", (100, 100, 300, 300)),  # 200x200: picture-sized square, not a text line
        ("", (400, 100, 900, 260)),  # 500x160, aspect 3.1: a text line
        ("", (100, 400, 160, 460)),  # 60x60: a single character, area under the square floor
        ("", (500, 400, 1000, 780 - 40)),  # 500x340, aspect 1.47, area 170k: a very large block
    ]
    out = _run(_detector(_cfg(), _ocr(boxes, conf=0.9)))
    got = sorted(tuple(int(v) for v in d.quad.bbox()) for d in _only(out, Category.SIGNBOARD))
    assert (100, 100, 300, 300) not in got and (500, 400, 1000, 740) not in got
    assert any(abs(b[0] - 400) <= 1 and abs(b[2] - 900) <= 1 for b in got)
    assert any(abs(b[0] - 100) <= 1 and abs(b[3] - 460) <= 1 for b in got)


def test_signboard_masks_a_large_near_square_box_when_the_text_was_read_confidently() -> None:
    """A big stacked two-line sign or a large short word is real text: the near-square skip is only for boxes the
    OCR could not read (a photo or UI tile the detector boxed)."""
    boxes = [
        ("OPEN NOW", (100, 100, 300, 300)),  # 200x200, aspect 1.0, read
        ("WELCOME", (500, 400, 1000, 740)),  # 500x340, area 170k, read
    ]
    out = _run(_detector(_cfg(), _ocr(boxes, conf=0.9)))
    got = {tuple(int(v) for v in d.quad.bbox()) for d in _only(out, Category.SIGNBOARD)}
    assert any(abs(b[0] - 100) <= 1 and abs(b[2] - 300) <= 1 for b in got)
    assert any(abs(b[0] - 500) <= 1 and abs(b[2] - 1000) <= 1 for b in got)
    unread = [("", (100, 100, 300, 300)), ("WELCOME", (500, 400, 1000, 740))]
    out = _run(_detector(_cfg(), _ocr(unread, conf=0.3)))  # low confidence: skipped again
    assert not _only(out, Category.SIGNBOARD)


def test_signboard_min_height_comes_from_the_config() -> None:
    boxes = [("", (100, 100, 400, 120))]  # 20 px tall
    result = OcrResult(tuple(_quad(r) for _, r in boxes), ("",), (0.9,), ("unknown",))
    cfg = _cfg()
    assert len(_only(_run(_detector(cfg, lambda f: result)), Category.SIGNBOARD)) == 1
    cfg2 = Config(profile="dev", _env_file=None, signboard={"min_text_height_px": 30})  # type: ignore[arg-type]
    assert _only(_run(_detector(cfg2, lambda f: result)), Category.SIGNBOARD) == []


def test_signboard_off_masks_no_text_and_a_pii_box_is_not_also_a_signboard() -> None:
    boxes = [_line("Welcome to the shop"), _line("Call: +91 98765 43210", y=160)]
    off = _run(_detector(_cfg(signboard={"enabled": False}), _ocr(boxes)))
    assert [d.category for d in off] == [Category.PHONE_NUMBER]
    on = _run(_detector(_cfg(), _ocr(boxes)))
    assert sorted(d.category.value for d in on) == ["phone_number", "signboard"]  # the phone box only once


# ---------------------------------------------------------------------------- risky text
def test_risky_text_needs_the_category_enabled_and_a_blocklist() -> None:
    boxes = [_line("this stream is sponsored by acme bets")]
    base = {"signboard": {"enabled": False}}
    assert _run(_detector(_cfg(**base), _ocr(boxes))) == []  # category off by default
    cfg_on = _cfg(**base, risky_text={"enabled": True})
    assert _run(_detector(cfg_on, _ocr(boxes))) == []  # enabled but no blocklist
    cfg_on.guard.risky_text_blocklist = ["Acme Bets", "casino"]
    out = _run(_detector(cfg_on, _ocr(boxes)))
    assert [(d.category, d.extra["reason"]) for d in out] == [(Category.RISKY_TEXT, "blocklist")]
    cfg_on.guard.risky_text_blocklist = ["casino"]
    assert _run(_detector(cfg_on, _ocr(boxes))) == []  # no match


def test_the_blocklist_term_never_reaches_a_detection_or_the_log(caplog: pytest.LogCaptureFixture) -> None:
    cfg = _cfg(signboard={"enabled": False}, risky_text={"enabled": True})
    cfg.guard.risky_text_blocklist = ["acme bets"]
    caplog.set_level(logging.DEBUG)
    out = _run(_detector(cfg, _ocr([_line("sponsored by acme bets")])))
    assert out and "acme" not in repr(out) and "acme" not in caplog.text


# ---------------------------------------------------------------------------- OCR budget exhausted
def _digit_run_image() -> tuple[np.ndarray, tuple[int, int, int, int]]:
    import cv2

    img = BLANK.copy()
    cv2.putText(img, "9876543210", (110, 140), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (20, 20, 20), 3, cv2.LINE_AA)
    return img, (100, 100, 420, 155)


def test_an_unread_digit_run_box_is_masked_as_a_phone_number_when_unsure() -> None:
    img, rect = _digit_run_image()
    result = OcrResult((_quad(rect),), ("",), (0.8,), ("unknown",))  # detected, not recognised (budget spent)
    out = _run(_detector(_cfg(signboard={"enabled": False}), lambda f: result), img)
    assert [(d.category, d.extra["reason"]) for d in out] == [(Category.PHONE_NUMBER, "ocr_unsure")]
    off = _run(
        _detector(
            _cfg(
                signboard={"enabled": False},
                phone_number={"mask_when_unsure": False},
                pin_code={"mask_when_unsure": False},
            ),
            lambda f: result,
        ),
        img,
    )
    assert off == []


def test_an_unread_blank_box_is_not_masked_as_a_number() -> None:
    result = OcrResult((_quad((100, 100, 420, 155)),), ("",), (0.8,), ("unknown",))
    assert _run(_detector(_cfg(signboard={"enabled": False}), lambda f: result)) == []


def test_a_low_confidence_reading_counts_as_unread() -> None:
    img, rect = _digit_run_image()
    result = OcrResult((_quad(rect),), ("7",), (0.4,), ("latin",))
    out = _run(_detector(_cfg(signboard={"enabled": False}), lambda f: result), img)
    assert [d.extra["reason"] for d in out] == ["ocr_unsure"]


def test_signboard_covers_unread_boxes_so_they_are_not_masked_twice() -> None:
    img, rect = _digit_run_image()
    result = OcrResult((_quad(rect),), ("",), (0.8,), ("unknown",))
    out = _run(_detector(_cfg(), lambda f: result), img)
    assert [d.category for d in out] == [Category.SIGNBOARD]


# ---------------------------------------------------------------------------- fail-safe and lifecycle
def test_ocr_unavailable_fails_the_lane_run() -> None:
    def broken(frame: Frame) -> OcrResult:
        raise OcrUnavailableError("models missing")

    with pytest.raises(OcrUnavailableError):
        _run(_detector(_cfg(), broken))


def test_detect_before_configure_raises() -> None:
    with pytest.raises(RuntimeError):
        _run(DETECTOR())


def test_no_text_or_numbers_in_the_log_of_a_full_run(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    boxes = [_line("Call: +91 98765 43210"), _line("Pay: rahul@oksbi", y=160), _line("PIN: 560001", y=220)]
    _run(_detector(_cfg(), _ocr(boxes)))
    for secret in ("98765", "43210", "rahul", "560001"):
        assert secret not in caplog.text


# ---------------------------------------------------------------------------- through the host and the planner
def test_text_is_discovered_and_a_phone_number_reaches_the_planner_as_a_mask_request() -> None:
    found = discover(only=["text"])  # not the whole package: other detectors may be mid-build
    assert found["text"] is DETECTOR
    cfg = _cfg(signboard={"enabled": False})
    res = VisionResources(cfg, ocr=_ocr([_line("Call: +91 98765 43210")]))
    host = vhost.VisionHost(
        cfg, InProcessBus(), registry={"text": DETECTOR}, resources=res, assembler_factory=planner_factory
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
    assert req.category is Category.PHONE_NUMBER and req.kind is MaskKind.REGION and req.source == "text"
    x1, y1, x2, y2 = req.quads[0].bbox()
    assert x1 < 100 and y1 < 100 and x2 > 420 and y2 > 128  # the box plus the planner's padding


# --------------------------------------------------------------------------- faint text (signboard)
class _WeakOcr:
    """A provider with the optional ``weak_boxes`` hook of the real OCR service: no normal boxes at all."""

    def __init__(self, rects: list[tuple[int, int, int, int]], scores: list[float]) -> None:
        self._boxes = np.array(
            [[[x1, y1], [x2, y1], [x2, y2], [x1, y2]] for x1, y1, x2, y2 in rects], np.float32
        ).reshape(-1, 4, 2)
        self._scores = np.array(scores, np.float32)

    def __call__(self, frame: Frame) -> OcrResult:
        return OcrResult((), (), (), ())

    def weak_boxes(self, frame: Frame) -> tuple[np.ndarray, np.ndarray]:
        return self._boxes, self._scores


def test_faint_text_regions_are_masked_as_signboard_even_when_the_normal_read_found_nothing() -> None:
    ocr = _WeakOcr([(100, 100, 420, 140), (100, 200, 420, 206), (100, 300, 420, 340)], [0.45, 0.9, 0.2])
    out = _run(_detector(_cfg(), ocr))
    assert len(out) == 1, "a 6 px tall box and a 0.2 score are not text lines to mask"
    d = out[0]
    assert d.category is Category.SIGNBOARD and d.extra["reason"] == "mask_all_text_weak"
    assert d.quad.bbox() == pytest.approx((100, 100, 420, 140))


def test_faint_text_is_ignored_when_signboard_is_off() -> None:
    ocr = _WeakOcr([(100, 100, 420, 140)], [0.45])
    assert _run(_detector(_cfg(signboard={"enabled": False}), ocr)) == []
