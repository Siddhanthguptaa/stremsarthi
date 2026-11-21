"""The ``upi_qr`` detector on SPECIMEN QR scenes (CPU, no model files): payload classifier tables, masking of
payment codes, pass-through of other codes, undecodable codes, privacy, perf. Payloads are synthetic."""

from __future__ import annotations

import logging
import time

import numpy as np
import pytest

from contracts import Category, Config, DetectContext, DetectorConfig, Device, Frame, Lane
from vision import registry
from vision.detectors.upi_qr import DETECTOR
from vision.detectors.upi_qr.accept import coverage
from vision.detectors.upi_qr.payload import classify_payload
from vision.detectors.upi_qr.scan import Found, scan
from vision.detectors.upi_qr.testing import (
    QUIET,
    RECIPES,
    QrScene,
    _patch,
    augmented_set,
    make_qr_scene,
    qr_matrix,
)
from vision.resources import VisionResources

W, H = 1280, 720


# --------------------------------------------------------------------------- payload classifier
@pytest.mark.parametrize(
    "text",
    [
        "upi://pay?pa=zz-specimen-1@specimen&pn=SPECIMEN&am=10&cu=INR",
        "UPI://PAY?PA=zz-specimen-1@specimen",
        "Upi://mandate?pa=zz-specimen-1@specimen",
        "upi:pay?pa=zz-specimen-1@specimen",
        "  upi://pay?pa=zz-specimen-1@specimen  ",
        "https://pay.example/pay?pa=zz-specimen-1@specimen&pn=X",
        "https://example.org/go?pa=zz-specimen-1%40specimen",
        "https%3A%2F%2Fexample.org%2Fpay%3Fpa%3Dzz-specimen-1%40specimen",
        "https://example.org/?u=upi%3A%2F%2Fpay%3Fpa%3Dx%40specimen",
        "phonepe://pay?pa=zz-specimen-1@specimen",
        "tez://upi/pay?pa=zz-specimen-1@specimen",
        "bhim://pay?pa=zz-specimen-1@specimen",
        "https://rzp.io/i/SPECIMEN",
        "https://pages.razorpay.com/specimen",
        "https://paytm.me/specimen",
        "zz-specimen-1@oksbi",
        "specimen.shop@ybl",
    ],
)
def test_payment_payloads_are_classified(text: str) -> None:
    assert classify_payload(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "https://example.org/specimen/abcd",
        "https://example.org/menu?table=7",
        "WIFI:T:WPA;S:SPECIMEN-1;P:specimen-not-a-password;;",
        "SPECIMEN contact card 1234",
        "mailto:specimen@example.org",
        "specimen@example.org",  # an e-mail, not a UPI ID
        "BEGIN:VCARD\nFN:SPECIMEN\nEND:VCARD",
        "https://notrzp.io.example.org/x",
        "tel:+910000000000",
        "upimarket.example",
    ],
)
def test_other_payloads_pass(text: str | None) -> None:
    assert classify_payload(text) is None


def test_classifier_returns_a_code_never_the_text() -> None:
    code = classify_payload("upi://pay?pa=zz-specimen-9@specimen")
    assert code in {"upi_scheme", "upi_app_link", "pay_link", "upi_id"}


# --------------------------------------------------------------------------- helpers
def _config(**upi: object) -> Config:
    cfg = Config(profile="dev", _env_file=None)
    if upi:
        cfg.categories[Category.UPI_QR] = cfg.categories[Category.UPI_QR].model_copy(update=upi)
    return cfg


def _detector(cfg: Config):
    det = DETECTOR()
    res = VisionResources(cfg, ocr=lambda f: None)  # type: ignore[arg-type]
    det.configure(DetectorConfig(categories=cfg.categories, device=Device.CPU), res)
    return det


def _run(det, image: np.ndarray, seq: int = 0):
    ctx = DetectContext(lane=Lane.FAST, categories={}, stride=3)
    return det.detect(Frame(seq, seq / 30.0, 0.0, image), ctx)


def _cover(dets, scene: QrScene) -> float:
    assert scene.quad is not None
    return coverage([d.quad.bbox() for d in dets], scene.quad)


# --------------------------------------------------------------------------- plugin interface
def test_registry_finds_the_detector() -> None:
    assert registry.discover()["upi_qr"] is DETECTOR
    assert DETECTOR.lane is Lane.FAST and DETECTOR.categories == frozenset({Category.UPI_QR})
    assert DETECTOR.device is Device.CPU and DETECTOR.vram_mb == 0


def test_detect_before_configure_raises() -> None:
    with pytest.raises(RuntimeError):
        DETECTOR().detect(
            Frame(0, 0.0, 0.0, np.zeros((H, W, 3), np.uint8)),
            DetectContext(lane=Lane.FAST, categories={}, stride=3),
        )


# --------------------------------------------------------------------------- masking behaviour
def test_payment_qr_is_masked_with_confidence_and_full_coverage() -> None:
    det = _detector(_config())
    sc = make_qr_scene("upi", 11, module_px=4.5, rot_deg=-12.0, tilt_deg=15.0)
    dets = _run(det, sc.image)
    assert len(dets) == 1
    d = dets[0]
    assert d.category is Category.UPI_QR and d.confidence == pytest.approx(0.95)
    assert d.extra["reason"] in {"upi_scheme", "pay_link", "upi_app_link", "upi_id"}
    assert _cover(dets, sc) >= 0.9, "the quad covers the symbol and its quiet zone"


def test_non_payment_qr_passes_and_qr_free_frames_stay_clean() -> None:
    det = _detector(_config())
    for seed in range(8):
        assert _run(det, make_qr_scene("other", 100 + seed, module_px=4.5).image) == []
    for seed in range(12):
        assert _run(det, make_qr_scene("none", 200 + seed).image) == [], (
            "stripes and nested squares are not QRs"
        )


def test_sensitivity_at_least_0_8_masks_every_qr() -> None:
    det = _detector(_config(sensitivity=0.9))
    sc = make_qr_scene("other", 5, module_px=4.5)
    dets = _run(det, sc.image)
    assert len(dets) == 1 and dets[0].extra["reason"] == "qr_any"
    assert _cover(dets, sc) >= 0.9


def _corrupted_qr(seed: int, frac: float = 0.10) -> QrScene:
    """A well-formed QR whose data modules are flipped at random (finder patterns intact): the finders are
    there, no decoder can read it. Placed upright at (300, 200)."""
    rng = np.random.default_rng(seed)
    mods = qr_matrix(f"upi://pay?pa=zz-specimen-{seed}@specimen", ec="L").copy()
    n = mods.shape[0]
    keep = np.zeros((n, n), bool)
    keep[:9, :9] = keep[:9, -9:] = keep[-9:, :9] = True
    mods = np.where((rng.random((n, n)) < frac) & ~keep, ~mods, mods)
    side = int(round((n + 2 * QUIET) * 5.0))
    image = np.full((H, W, 3), 140, np.uint8)
    image[200 : 200 + side, 300 : 300 + side] = _patch(mods, side)[..., None]
    quad = np.array([[300, 200], [300 + side, 200], [300 + side, 200 + side], [300, 200 + side]], np.float32)
    return QrScene(image, "upi", quad, None, {"undecodable": True})


def test_undecodable_qr_is_masked_when_unsure_and_ignored_otherwise() -> None:
    unsure = _detector(_config(mask_when_unsure=True))
    strict = _detector(_config(mask_when_unsure=False))
    for seed in range(8):
        sc = _corrupted_qr(seed)
        dets = _run(unsure, sc.image)
        assert len(dets) == 1, seed
        assert dets[0].extra["reason"] == "qr_undecodable"
        assert dets[0].confidence == pytest.approx(0.6)
        assert _cover(dets, sc) >= 0.9
        assert _run(strict, sc.image) == [], "undecodable codes are only masked with mask_when_unsure"


def _qr_frame(text: str, seed: int, frac: float = 0.0, module_px: float = 5.0) -> np.ndarray:
    """A QR of ``text`` at (300, 200), with ``frac`` of its data modules flipped (finders intact)."""
    rng = np.random.default_rng(seed)
    mods = qr_matrix(text, ec="L").copy()
    n = mods.shape[0]
    keep = np.zeros((n, n), bool)
    keep[:9, :9] = keep[:9, -9:] = keep[-9:, :9] = True
    if frac:
        mods = np.where((rng.random((n, n)) < frac) & ~keep, ~mods, mods)
    side = int(round((n + 2 * QUIET) * module_px))
    image = np.full((H, W, 3), 140, np.uint8)
    image[200 : 200 + side, 300 : 300 + side] = _patch(mods, side)[..., None]
    return image


def _run_at(det, image: np.ndarray, pts: float):
    return det.detect(
        Frame(int(pts * 30), pts, 0.0, image), DetectContext(lane=Lane.FAST, categories={}, stride=3)
    )


def test_undecodable_run_on_a_readable_non_payment_code_is_not_masked() -> None:
    """Root cause of 23 % false masks on a website QR clip: one run in which the code did not decode was masked
    as ``qr_undecodable`` and the planner held that mask for 2 s. A code that decoded as a non-payment moments
    ago is the same code."""
    det = _detector(_config(mask_when_unsure=True))
    text = "https://example.org/specimen/abcd"
    good, bad = _qr_frame(text, 0), _qr_frame(text, 3, 0.10)
    assert _run_at(det, good, 0.0) == []
    undecodable = _run_at(_detector(_config(mask_when_unsure=True)), bad, 0.0)
    assert len(undecodable) == 1 and undecodable[0].extra["reason"] == "qr_undecodable", (
        "the corrupted code is undecodable on its own"
    )
    assert _run_at(det, bad, 0.1) == [], "same place, 0.1 s after a clean read: the same code"
    again = _run_at(det, bad, 0.2)
    assert len(again) == 1, "one undecodable run is let through per clean read, the next one is masked"
    assert _run_at(det, good, 0.3) == [], "a clean read re-arms the memory"
    assert _run_at(det, bad, 0.4) == []
    late = _run_at(det, bad, 0.4 + 1.0)
    assert len(late) == 1 and late[0].extra["reason"] == "qr_undecodable", "the memory expires"


def test_payment_qr_replacing_a_website_qr_stays_masked_within_two_runs_even_undecodable() -> None:
    det = _detector(_config(mask_when_unsure=True))
    assert _run_at(det, _qr_frame("https://example.org/specimen/abcd", 0), 0.0) == []
    pay_bad = _qr_frame("upi://pay?pa=zz-specimen-1@specimen", 5, 0.10)  # same spot, same size, undecodable
    masked = [bool(_run_at(det, pay_bad, 0.1 * k)) for k in (1, 2, 3, 4, 5)]
    assert masked[1:] == [True] * 4, f"masked from the second run on: {masked}"
    assert not _run_at(det, np.full((H, W, 3), 140, np.uint8), 0.7)


def test_resized_or_moved_candidate_is_not_trusted_as_the_website_code() -> None:
    det = _detector(_config(mask_when_unsure=True))
    text = "https://example.org/specimen/abcd"
    assert _run_at(det, _qr_frame(text, 0), 0.0) == []
    bigger = _qr_frame(text, 3, 0.10, module_px=6.5)  # same corner, about 70 % more area
    assert len(_run_at(det, bigger, 0.1)) == 1, "a clearly larger box at the same spot is masked at once"
    det2 = _detector(_config(mask_when_unsure=True))
    assert _run_at(det2, _qr_frame(text, 0), 0.0) == []
    shifted = np.full((H, W, 3), 140, np.uint8)
    shifted[230:430, 300:500] = _qr_frame(text, 3, 0.10)[200:400, 300:500]  # moved down by 30 px
    assert len(_run_at(det2, shifted, 0.1)) == 1, "a moved box is masked at once"


def test_undecodable_sighting_never_refreshes_the_benign_memory() -> None:
    det = _detector(_config(mask_when_unsure=True))
    text = "https://example.org/specimen/abcd"
    bad = _qr_frame(text, 3, 0.10)
    assert _run_at(det, _qr_frame(text, 0), 0.0) == []
    assert _run_at(det, bad, 0.3) == [], "within the TTL of the clean read"
    assert len(_run_at(det, bad, 0.6)) == 1, "the undecodable run at 0.3 did not extend the memory"
    assert len(_run_at(det, bad, 0.7)) == 1


def test_undecodable_code_elsewhere_or_after_a_payment_is_still_masked() -> None:
    det = _detector(_config(mask_when_unsure=True))
    other = _qr_frame("https://example.org/specimen/abcd", 0)
    assert _run_at(det, other, 0.0) == []
    far = np.full((H, W, 3), 140, np.uint8)
    far[400:600, 700:900] = _qr_frame("https://example.org/specimen/abcd", 4, 0.10)[200:400, 300:500]
    assert len(_run_at(det, far, 0.1)) == 1, "an undecodable code in another place is masked"
    pay = _qr_frame("upi://pay?pa=zz-specimen-1@specimen", 0)
    assert len(_run_at(det, pay, 0.2)) == 1
    pay_bad = _qr_frame("upi://pay?pa=zz-specimen-1@specimen", 5, 0.10)
    assert len(_run_at(det, pay_bad, 0.3)) == 1, "a payment code that stops decoding stays masked"


def test_stray_finder_lookalike_is_not_masked_as_a_qr() -> None:
    """Nested squares (a logo, a target) must not trigger the undecodable path."""
    det = _detector(_config(mask_when_unsure=True))
    image = np.full((H, W, 3), 140, np.uint8)
    for k, col in enumerate((10, 240, 10, 240, 10)):
        m = k * 9
        image[300 + m : 400 - m, 500 + m : 600 - m] = col
    assert _run(det, image) == []


def test_disabled_category_returns_nothing() -> None:
    det = _detector(_config(enabled=False))
    assert _run(det, make_qr_scene("upi", 3, module_px=5.0).image) == []


def test_payment_and_other_qr_in_one_frame_only_the_payment_is_masked() -> None:
    left = make_qr_scene("upi", 21, module_px=4.0, tilt_deg=5.0, centre=(300.0, 360.0))
    right = make_qr_scene("other", 22, module_px=4.0, tilt_deg=5.0, centre=(980.0, 360.0))
    image = left.image.copy()
    image[:, 640:] = right.image[:, 640:]
    dets = _run(_detector(_config()), image)
    assert len(dets) == 1
    assert _cover(dets, left) >= 0.9
    assert dets[0].quad.bbox()[2] < 640


def test_recipe_set_recall_is_at_least_98_percent() -> None:
    """The T2.7 recipes (tilt to 25 deg, blur, glare, noise, far codes, partial cover): mask >= 98 %."""
    scenes = [s for s in augmented_set(2 * len(RECIPES), 0, 0, seed=99)]
    det = _detector(_config())
    hit = sum(_cover(_run(det, sc.image), sc) >= 0.9 for sc in scenes)
    assert hit / len(scenes) >= 0.98, f"{hit}/{len(scenes)}"


# --------------------------------------------------------------------------- privacy
def test_payload_never_appears_in_detections_logs_or_found(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    payload = "upi://pay?pa=zz-specimen-deadbeef@specimen&pn=SPECIMEN"
    sc = make_qr_scene("upi", 7, module_px=5.0, payload=payload)
    dets = _run(_detector(_config()), sc.image)
    assert dets
    blob = repr(dets) + "\n".join(r.getMessage() for r in caplog.records)
    for secret in ("deadbeef", "specimen@", "pa=", "upi://"):
        assert secret not in blob
    for d in dets:
        assert all(isinstance(v, (int, float, str, bool)) for v in d.extra.values())
        assert set(d.extra) <= {"reason", "decoder", "side_px"}
    found = scan(sc.image, __import__("cv2").QRCodeDetectorAruco())
    assert found and all(isinstance(f, Found) for f in found)
    assert not any("deadbeef" in repr(f) for f in found)
    assert set(Found.__slots__) == {"quad", "decoded", "reason", "decoder"}


# --------------------------------------------------------------------------- latency
@pytest.mark.slow
def test_fast_lane_scan_p95_within_budget() -> None:
    det = _detector(_config())
    det.warmup()
    scenes = augmented_set(24, 8, 16, seed=7)
    times = []
    for sc in scenes:
        t0 = time.perf_counter()
        _run(det, sc.image)
        times.append((time.perf_counter() - t0) * 1000.0)
    assert float(np.percentile(times, 95)) <= 25.0, f"p95 {np.percentile(times, 95):.1f} ms"
