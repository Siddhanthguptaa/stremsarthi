"""The ``screens`` detector, OTP part, with fake OCR providers (CPU, no models).

Every code, number and sentence below is invented SPECIMEN text: nothing here is a real notification.
"""

from __future__ import annotations

import logging
import time

import cv2
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
)
from contracts.fakes import InProcessBus
from vision import host as vhost
from vision.detectors.screens import DETECTOR
from vision.planner import planner_factory
from vision.registry import discover
from vision.resources import OcrUnavailableError, VisionResources
from vision.testing_screens import Box, fake_ocr

BLANK = np.full((720, 1280, 3), 235, np.uint8)


def _detector(ocr, *, unsure: bool = True, enabled: bool = True, **params):
    cfg = Config(profile="dev", _env_file=None)
    cfg.categories[Category.OTP_SCREEN] = cfg.categories[Category.OTP_SCREEN].model_copy(
        update={"enabled": enabled, "mask_when_unsure": unsure}
    )
    res = VisionResources(cfg, ocr=ocr)
    det = DETECTOR()
    det.configure(
        DetectorConfig(categories=cfg.categories, device=Device.CPU, params={"templates": [], **params}), res
    )
    return det


def _run(det, image=BLANK, seq: int = 0):
    return det.detect(
        Frame(seq, seq / 4.0, 0.0, image), DetectContext(lane=Lane.ACCURATE, categories={}, stride=1)
    )


def _otp(dets):
    return [d for d in dets if d.category is Category.OTP_SCREEN]


def test_class_declaration_matches_the_plugin_contract() -> None:
    assert DETECTOR.id == "screens" and DETECTOR.lane is Lane.ACCURATE and DETECTOR.vram_mb == 0
    assert DETECTOR.categories == frozenset({Category.OTP_SCREEN, Category.GAME_ID, Category.ROOM_CREDENTIAL})


@pytest.mark.parametrize(
    "text",
    [
        "Your OTP is 482913. Do not share it.",
        "482913 is your one time password for SPECIMEN BANK",
        "Your verification code is 4829",
        "SPECIMEN PAY: use 4829 as OTP to confirm your payment.",
        "Aapka OTP 48291345 hai. Ise kisi ke saath share na karein.",
        "आपका ओटीपी 482913 है। इसे किसी से साझा न करें।",
        "आपका वन टाइम पासवर्ड 482913 है",
    ],
)
def test_a_cue_with_a_code_in_one_box_is_masked(text: str) -> None:
    out = _otp(_run(_detector(fake_ocr([(text, (500, 300, 900, 335))]))))
    assert len(out) == 1
    d = out[0]
    assert d.detector == "screens" and d.extra["reason"] == "otp_same_box" and 0.6 <= d.confidence <= 0.95
    x1, y1, x2, y2 = d.quad.bbox()
    assert x1 <= 500 and y1 <= 300 and x2 >= 900 and y2 >= 335


def test_cue_and_code_on_adjacent_lines_of_one_message() -> None:
    boxes: list[Box] = [
        ("Your verification code is", (500, 300, 800, 330)),
        ("482913. SPECIMEN will never", (500, 334, 830, 364)),
    ]
    out = _otp(_run(_detector(fake_ocr(boxes))))
    assert len(out) == 1 and out[0].extra["reason"] == "otp_paragraph"
    x1, y1, x2, y2 = out[0].quad.bbox()
    assert y1 <= 300 and y2 >= 364 and x2 >= 830  # both lines, whole message


@pytest.mark.parametrize(
    "text",
    [
        "Order #482913 has been delivered. Rate your experience.",
        "Ticket 482913 is confirmed for Saturday.",
        "Your parcel 482913 will arrive tomorrow.",
        "Here is your invoice 482913",
        "Call 9876543210 now",
        "Stream starts 12:47 on 20/11/2026",
        "Battery at 20%. Connect charger.",
        "Do not share this clip with anyone",
        "Light rain expected this evening.",
    ],
)
def test_ordinary_notifications_and_numbers_are_not_masked(text: str) -> None:
    assert _otp(_run(_detector(fake_ocr([(text, (500, 300, 900, 335))])))) == []


def test_a_cue_far_from_any_code_is_not_a_hit_but_a_cue_alone_is_masked_when_unsure() -> None:
    boxes: list[Box] = [("Enter your OTP to continue", (500, 300, 800, 335))]
    out = _otp(_run(_detector(fake_ocr(boxes))))
    assert len(out) == 1 and out[0].extra["reason"] == "otp_cue_only" and out[0].confidence < 0.7
    assert _otp(_run(_detector(fake_ocr(boxes), unsure=False))) == []


def test_otp_entry_screen_heading_and_a_code_only_box_below() -> None:
    boxes: list[Box] = [
        ("Verification code", (520, 200, 760, 240)),
        ("Enter the one time password sent by", (500, 250, 780, 280)),
        ("483912", (540, 330, 740, 390)),
        ("VERIFY", (600, 450, 700, 480)),
    ]
    out = _otp(_run(_detector(fake_ocr(boxes))))
    assert len(out) == 1 and out[0].extra["reason"] == "otp_near_code"
    x1, y1, x2, y2 = out[0].quad.bbox()
    assert y2 >= 390 and y1 <= 200
    # an OCR-damaged code box (O for 0, B for 8) still counts, a long word does not
    damaged = [*boxes[:2], ("BO39I2", (540, 330, 740, 390))]
    assert _otp(_run(_detector(fake_ocr(damaged, conf=0.5))))[0].extra["reason"] == "otp_near_code"
    word = [*boxes[:2], ("SPECIMEN", (540, 330, 740, 390))]
    assert [d.extra["reason"] for d in _otp(_run(_detector(fake_ocr(word))))] == ["otp_cue_only"]


def test_a_code_box_far_below_the_cue_is_not_paired() -> None:
    boxes: list[Box] = [("Verification code", (520, 100, 760, 140)), ("483912", (540, 450, 740, 500))]
    out = _otp(_run(_detector(fake_ocr(boxes), unsure=False)))
    assert out == []


def _banner_image() -> np.ndarray:
    img = np.full((720, 1280, 3), (70, 40, 50), np.uint8)
    cv2.rectangle(img, (440, 260), (860, 420), (245, 245, 248), -1)  # the banner
    cv2.putText(img, "x", (480, 320), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 25), 2)
    return img


def test_the_mask_covers_the_whole_banner_not_just_the_text() -> None:
    boxes: list[Box] = [
        ("Messages", (470, 270, 600, 295)),
        ("Your OTP is 482913. Do not share.", (470, 305, 830, 340)),
    ]
    out = _otp(_run(_detector(fake_ocr(boxes)), image=_banner_image()))
    assert len(out) == 1
    x1, y1, x2, y2 = out[0].quad.bbox()
    assert abs(x1 - 440) <= 12 and abs(x2 - 860) <= 12 and abs(y1 - 260) <= 14 and abs(y2 - 420) <= 14
    assert out[0].extra["shape"] == "otp_banner"


def _phone_image() -> np.ndarray:
    img = np.full((720, 1280, 3), (90, 110, 90), np.uint8)
    cv2.rectangle(img, (475, 20), (805, 700), (22, 22, 25), -1)  # phone body, 330x680
    cv2.rectangle(img, (487, 36), (793, 684), (150, 90, 60), -1)  # screen
    cv2.rectangle(img, (500, 300), (780, 380), (245, 245, 248), -1)  # banner
    return img


def test_a_phone_screen_with_enough_text_is_masked_whole() -> None:
    boxes: list[Box] = [
        ("12:47", (500, 40, 560, 62)),
        ("Fri, 20 Nov", (600, 140, 700, 165)),
        ("Messages", (510, 308, 600, 330)),
        ("Your OTP is 482913. Do not share.", (510, 335, 770, 365)),
        ("Weather", (510, 420, 600, 442)),
        ("Light rain expected", (510, 450, 740, 475)),
    ]
    out = _otp(_run(_detector(fake_ocr(boxes)), image=_phone_image()))
    assert len(out) == 1 and out[0].extra["shape"] == "otp_phone_screen"
    x1, y1, x2, y2 = out[0].quad.bbox()
    assert x1 <= 490 and x2 >= 790 and y1 <= 40 and y2 >= 680
    few = _otp(_run(_detector(fake_ocr(boxes[2:4])), image=_phone_image()))  # two boxes: banner only
    assert len(few) == 1 and few[0].extra["shape"] == "otp_banner"
    off = _otp(_run(_detector(fake_ocr(boxes), screen_mask=False), image=_phone_image()))
    assert off[0].extra["shape"] == "otp_banner"


def test_disabled_category_yields_nothing_and_skips_the_ocr() -> None:
    calls: list[int] = []

    def ocr(frame: Frame) -> OcrResult:
        calls.append(frame.seq)
        return OcrResult((), (), (), ())

    cfg = Config(profile="dev", _env_file=None)
    for c in (Category.OTP_SCREEN, Category.GAME_ID, Category.ROOM_CREDENTIAL):
        cfg.categories[c] = cfg.categories[c].model_copy(update={"enabled": False})
    det = DETECTOR()
    det.configure(
        DetectorConfig(categories=cfg.categories, device=Device.CPU, params={"templates": []}),
        VisionResources(cfg, ocr=ocr),
    )
    assert _run(det) == [] and calls == []


def test_codes_only_in_the_detection_never_the_code_or_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    out = _otp(_run(_detector(fake_ocr([("Your OTP is 482913. Do not share it.", (500, 300, 900, 335))]))))
    assert out
    for d in out:
        for value in d.extra.values():
            assert not isinstance(value, str) or ("482913" not in value and "OTP" not in value)
        assert "482913" not in repr(d)
    assert "482913" not in caplog.text and "Do not share" not in caplog.text


def test_ocr_unavailable_fails_the_lane_run_and_detect_before_configure_raises() -> None:
    def broken(frame: Frame) -> OcrResult:
        raise OcrUnavailableError("models missing")

    with pytest.raises(OcrUnavailableError):
        _run(_detector(broken))
    with pytest.raises(RuntimeError):
        _run(DETECTOR())


def test_screens_is_discovered_and_reaches_the_planner_as_a_mask_request() -> None:
    assert discover(only=["screens"])["screens"] is DETECTOR
    cfg = Config(profile="dev", _env_file=None)
    res = VisionResources(cfg, ocr=fake_ocr([("Your OTP is 482913. Do not share it.", (500, 300, 900, 335))]))
    host = vhost.VisionHost(
        cfg,
        InProcessBus(),
        registry={"screens": DETECTOR},
        resources=res,
        assembler_factory=planner_factory,
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
    assert req.category is Category.OTP_SCREEN and req.kind is MaskKind.REGION and req.source == "screens"
