"""The ``screens`` detector, game part (labels and templates), with fake OCR providers (CPU, no models).

The lobbies are SPECIMEN overlays from ``eval`` (our own UI with invented IDs, not the real games), the templates
are derived from them. Every ID below is invented.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from contracts import Category, Config, DetectContext, DetectorConfig, Device, Frame, Lane
from vision.detectors.screens import DETECTOR
from vision.detectors.screens.templates import TemplateMatcher, load_templates
from vision.registry import discover  # noqa: F401  (registry discovery is covered in the OTP test file)
from vision.resources import VisionResources
from vision.testing_screens import Box, coverage_of, fake_ocr, specimen_lobby, specimen_templates

BLANK = np.full((720, 1280, 3), 30, np.uint8)
ROOM_FRAME: list[Box] = [
    ("CUSTOM ROOM", (800, 150, 1100, 190)),
    ("ROOM ID", (790, 225, 900, 257)),
    ("38812514", (790, 262, 980, 307)),
    ("PASSWORD", (790, 345, 920, 377)),
    ("dv4r", (790, 381, 890, 430)),
    ("SLOT 1", (50, 175, 140, 208)),
]


def _detector(
    ocr,
    *,
    enabled: tuple[Category, ...] = (Category.GAME_ID, Category.ROOM_CREDENTIAL),
    unsure: bool = True,
    **params,
):
    cfg = Config(profile="dev", _env_file=None)
    for c in (Category.GAME_ID, Category.ROOM_CREDENTIAL):
        cfg.categories[c] = cfg.categories[c].model_copy(
            update={"enabled": c in enabled, "mask_when_unsure": unsure}
        )
    cfg.categories[Category.OTP_SCREEN] = cfg.categories[Category.OTP_SCREEN].model_copy(
        update={"enabled": False}
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


def _by(dets, cat):
    return [d for d in dets if d.category is cat]


# ------------------------------------------------------------------------------------------ label route
def test_room_id_and_password_are_masked_through_their_labels() -> None:
    out = _run(_detector(fake_ocr(ROOM_FRAME)))
    rooms = _by(out, Category.ROOM_CREDENTIAL)
    assert sorted(d.extra["reason"] for d in rooms) == ["room_id", "room_password"]
    boxes = sorted(tuple(round(v) for v in d.quad.bbox()) for d in rooms)
    assert boxes == [(790, 262, 980, 307), (790, 381, 890, 430)]  # the value boxes, not the labels
    assert _by(out, Category.GAME_ID) == []


@pytest.mark.parametrize(
    "boxes",
    [
        [("ID:59092369191", (110, 68, 310, 99))],
        [("UID: 119754647", (110, 68, 300, 98))],
        [("UID:", (110, 68, 170, 98)), ("119754647", (180, 68, 300, 98))],
        [("ID", (110, 68, 150, 98)), ("5909236919", (110, 104, 290, 134))],
    ],
)
def test_player_id_forms(boxes: list[Box]) -> None:
    out = _run(_detector(fake_ocr([*boxes, *ROOM_FRAME[:1]])))
    ids = _by(out, Category.GAME_ID)
    assert len(ids) == 1 and ids[0].extra["reason"] == "game_id"
    value = [b for b in boxes if b[0][0].isdigit() or b[0].startswith(("ID:", "UID:"))][-1]
    x1, y1, x2, y2 = ids[0].quad.bbox()
    assert y1 <= value[1][1] + 1 and y2 >= value[1][3] - 1  # the box that holds the value, not only the label


def test_the_label_needs_game_context() -> None:
    assert _run(_detector(fake_ocr([("ID: 12345678", (110, 68, 300, 98))]))) == []
    login: list[Box] = [
        ("LOBBY", (50, 20, 140, 50)),
        ("PASSWORD", (500, 300, 640, 330)),
        ("abcd1234", (500, 335, 640, 365)),
    ]
    assert (
        _by(_run(_detector(fake_ocr(login))), Category.ROOM_CREDENTIAL) == []
    )  # a lobby, but no room: a login form


@pytest.mark.parametrize("value", ["1234", "abcdefgh", "ID: 12"])
def test_a_player_id_must_have_8_to_12_digits(value: str) -> None:
    boxes: list[Box] = [
        ("SQUAD", (50, 20, 140, 50)),
        (f"ID: {value}" if not value.startswith("ID") else value, (110, 68, 300, 98)),
    ]
    assert _run(_detector(fake_ocr(boxes), unsure=False)) == []


def test_an_unreadable_value_is_masked_when_unsure_and_not_otherwise() -> None:
    boxes: list[Box] = [
        ("CUSTOM ROOM", (800, 150, 1100, 190)),
        ("ROOM ID", (790, 225, 900, 257)),
        ("3881?%1&@@!4$$", (790, 262, 980, 307)),
    ]
    out = _by(_run(_detector(fake_ocr(boxes))), Category.ROOM_CREDENTIAL)
    assert [d.extra["reason"] for d in out] == ["room_id_unsure"] and out[0].confidence < 0.7
    assert _run(_detector(fake_ocr(boxes), unsure=False)) == []


def test_categories_switch_independently() -> None:
    both = [*ROOM_FRAME, ("ID:59092369191", (110, 68, 310, 99))]
    only_id = _run(_detector(fake_ocr(both), enabled=(Category.GAME_ID,)))
    assert {d.category for d in only_id} == {Category.GAME_ID}
    only_room = _run(_detector(fake_ocr(both), enabled=(Category.ROOM_CREDENTIAL,)))
    assert {d.category for d in only_room} == {Category.ROOM_CREDENTIAL}


def test_game_numbers_that_are_not_ids_are_not_masked() -> None:
    boxes: list[Box] = [
        ("SQUAD", (50, 20, 140, 50)),
        ("x465", (131, 244, 205, 277)),
        ("95560", (1049, 22, 1141, 60)),
        ("MATCH RESULT", (400, 200, 800, 250)),
        ("1752", (831, 22, 907, 61)),
    ]
    assert _run(_detector(fake_ocr(boxes))) == []


def test_no_text_or_ids_in_the_detections() -> None:
    out = _run(_detector(fake_ocr([*ROOM_FRAME, ("ID:59092369191", (110, 68, 310, 99))])))
    assert len(out) == 3
    for d in out:
        assert all(
            not isinstance(v, str) or not any(s in v for s in ("388", "dv4r", "5909"))
            for v in d.extra.values()
        )
        assert "38812514" not in repr(d) and "5909236" not in repr(d)


# ------------------------------------------------------------------------------------------ template route
@pytest.fixture(scope="module")
def lobbies():
    return {s: specimen_lobby(s) for s in ("bgmi", "freefire")}


@pytest.fixture(scope="module")
def templates():
    return specimen_templates()


def _truth_rects(style: str) -> dict[str, tuple[float, float, float, float]]:
    from vision.testing_screens import ROOM_FIELDS

    room, pwd = ROOM_FIELDS[style]
    return {"room": room, "pwd": pwd}


@pytest.mark.parametrize("style", ["bgmi", "freefire"])
def test_templates_mask_the_fields_without_any_ocr_text(style: str, lobbies, templates) -> None:
    det = _detector(fake_ocr([]), templates=templates)
    out = _run(det, lobbies[style])
    assert {d.category for d in out} == {Category.GAME_ID, Category.ROOM_CREDENTIAL}
    assert len(_by(out, Category.ROOM_CREDENTIAL)) == 2 and len(_by(out, Category.GAME_ID)) == 1
    assert all(d.extra["source"] == "template" and d.extra["reason"] == "template_region" for d in out)
    # the regions cover the real value boxes of the SPECIMEN lobby (truth from the generator)
    import random

    from eval.data_gen.video.games import render_lobby

    sprite = render_lobby(random.Random(1), style)
    kinds = {
        "game_player_id": Category.GAME_ID,
        "game_room_id": Category.ROOM_CREDENTIAL,
        "game_room_password": Category.ROOM_CREDENTIAL,
    }
    for t in sprite.targets:
        quads = [np.array(d.quad.pts, np.float32) for d in _by(out, kinds[t.category])]
        assert coverage_of(t.quad, quads) >= 0.95, t.category


@pytest.mark.parametrize("width", [960, 640])
def test_templates_follow_the_scale_of_the_frame(width: int, lobbies, templates) -> None:
    img = cv2.resize(lobbies["freefire"], (width, width * 9 // 16), interpolation=cv2.INTER_AREA)
    out = _run(_detector(fake_ocr([]), templates=templates), img)
    rooms = _by(out, Category.ROOM_CREDENTIAL)
    assert len(rooms) == 2
    k = width / 1280.0
    want = _truth_rects("freefire")["room"]
    x1, y1, x2, y2 = (round(v) for v in sorted(rooms, key=lambda d: d.quad.bbox()[1])[0].quad.bbox())
    assert abs(x1 - want[0] * k) <= 6 * k + 3 and abs(y1 - want[1] * k) <= 6 * k + 3


def test_a_template_hit_and_an_ocr_hit_on_one_field_are_one_detection(lobbies, templates) -> None:
    # the OCR reads the room ID value inside the template's room-ID region
    import random

    from eval.data_gen.video.games import render_lobby

    sprite = render_lobby(random.Random(1), "bgmi")
    rid = next(t for t in sprite.targets if t.category == "game_room_id")
    xs, ys = [p[0] for p in rid.quad], [p[1] for p in rid.quad]
    boxes: list[Box] = [
        ("ROOM ID", (790, 225, 900, 257)),
        (rid.attrs["value"], (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))),
    ]
    out = _by(
        _run(_detector(fake_ocr(boxes), templates=templates), lobbies["bgmi"]), Category.ROOM_CREDENTIAL
    )
    assert len(out) == 2  # room ID (template + OCR merged) and the password region
    assert sorted(d.extra["source"] for d in out) == ["template", "template_ocr"]
    assert max(d.confidence for d in out) >= 0.9


def test_a_flat_or_unrelated_frame_has_no_template_hit(templates) -> None:
    rng = np.random.default_rng(3)
    noise = rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    for img in (BLANK, noise):
        assert _run(_detector(fake_ocr([]), templates=templates), img) == []


def test_a_weak_anchor_needs_other_evidence(lobbies, templates) -> None:
    img = lobbies["bgmi"].copy()
    cv2.rectangle(
        img, (740, 120), (1270, 580), (30, 40, 50), -1
    )  # the room panel is covered: only the profile card is left
    assert _run(_detector(fake_ocr([]), templates=templates), img) == []
    ctx = fake_ocr([("SQUAD", (480, 150, 600, 180))])
    out = _run(_detector(ctx, templates=templates), img)
    assert [d.category for d in out] == [Category.GAME_ID]


def test_shipped_templates_load_and_are_the_specimen_set() -> None:
    names = sorted(t.name for t in load_templates())
    assert names == ["bgmi_room", "freefire_room", "profile"]
    assert all(t.anchor.ndim == 2 for t in load_templates())
    det = _detector(fake_ocr([]), templates_dir=None)  # no "templates" override: the shipped set is used
    assert det is not None


def test_template_matching_is_cheap(lobbies, templates) -> None:
    import time

    m = TemplateMatcher(templates)
    m.match(lobbies["bgmi"])
    t0 = time.perf_counter()
    for _ in range(5):
        m.match(lobbies["bgmi"])
    assert (
        time.perf_counter() - t0
    ) / 5 < 0.12  # generous CPU bound; the measured value is in the T2.9 report
