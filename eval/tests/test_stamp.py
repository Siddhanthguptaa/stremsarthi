"""Stamp encode/decode, x264 survival, scaling, corruption (spec 9 test 1)."""

import cv2
import numpy as np
import pytest

from eval.sender import stamp
from eval.sender.roundtrip import x264_roundtrip


def _blank():
    return np.zeros((stamp.CANVAS_H, stamp.CANVAS_W, 3), dtype=np.uint8)


def test_gray_and_crc_basics():
    for n in (0, 1, 2, 255, 12345, (1 << 24) - 1):
        assert stamp.gray_decode(stamp.gray_encode(n)) == n
    # adjacent counters differ in exactly one Gray bit
    for n in range(1000):
        assert bin(stamp.gray_encode(n) ^ stamp.gray_encode(n + 1)).count("1") == 1
    assert stamp.crc8(b"123456789") == 0xF4  # CRC-8 (poly 0x07) check value


def test_roundtrip_10000_random_stamps():
    g = np.random.default_rng(42)
    f = _blank()
    counters = g.integers(0, 1 << 24, 10_000)
    times = g.integers(0, stamp.MS_MODULUS, 10_000)
    bad = 0
    for c, t in zip(counters, times, strict=True):
        beep = bool(c % 3 == 0)
        stamp.draw_stamp(f, int(c), int(t), beep)
        s = stamp.read_stamp(f)
        if s != stamp.Stamp(int(c), int(t), beep):
            bad += 1
    assert bad == 0


def test_stamp_geometry_and_exclusion_rect():
    f = _blank()
    stamp.draw_stamp(f, 5, 7, True)
    assert stamp.STAMP_EXCLUSION == (0, 0, 256, 32)
    assert f[:, 256:].sum() == 0 and f[32:].sum() == 0  # nothing drawn outside the stamp area
    assert set(np.unique(f[:32, :256])) <= {0, 255}


@pytest.mark.parametrize("crf", [23, 30, 35])
def test_stamp_survives_x264(crf):
    assert x264_roundtrip(crf, n_frames=60) >= 0.999


def test_stamp_survives_downscale_and_blur():
    f = _blank()
    stamp.draw_stamp(f, 4242, 123456, False)
    half = cv2.resize(f, (640, 360), interpolation=cv2.INTER_AREA)
    assert stamp.read_stamp(half) == stamp.Stamp(4242, 123456, False)
    blurred = cv2.GaussianBlur(f, (0, 0), 2.0)
    assert stamp.read_stamp(blurred) == stamp.Stamp(4242, 123456, False)
    gray = cv2.cvtColor(f, cv2.COLOR_RGB2GRAY)
    assert stamp.read_stamp(gray) == stamp.Stamp(4242, 123456, False)


def test_corrupted_cell_fails_crc():
    f = _blank()
    stamp.draw_stamp(f, 77, 999, False)
    f[0:16, 8:16] = 255 - f[0:16, 8:16]  # flip counter cell 1
    assert stamp.read_stamp(f) is None
    stamp.draw_stamp(f, 77, 999, False)
    f[16:32, 120:128] = 255 - f[16:32, 120:128]  # flip a capture-time cell
    assert stamp.read_stamp(f) is None


def test_delay_handles_wrap():
    assert stamp.delay_s(5000.0, 1000) == 4.0
    assert abs(stamp.delay_s(100.0, stamp.MS_MODULUS - 900) - 1.0) < 1e-9
    assert stamp.monotonic_ms(12.345) == 12345


def test_decode_time_budget():
    import time

    f = _blank()
    stamp.draw_stamp(f, 1, 2, False)
    luma = cv2.cvtColor(f, cv2.COLOR_RGB2GRAY)
    t0 = time.perf_counter()
    for _ in range(100):
        stamp.read_stamp(luma)
    assert (time.perf_counter() - t0) / 100 < 0.005  # spec 10: <= 5 ms per frame


def test_wrong_canvas_size_rejected():
    with pytest.raises(ValueError):
        stamp.draw_stamp(np.zeros((100, 100, 3), np.uint8), 1, 1, False)
