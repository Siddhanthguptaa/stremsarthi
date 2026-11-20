"""Optical stamp: a machine-readable block in the top-left of every frame.

Layout (canvas 1280x720, but the reader scales coordinates to any frame size):
  strip 1, y 0..16:  24-bit Gray-coded frame counter (MSB first) + 8-bit CRC-8
  strip 2, y 16..32: 31-bit capture time in ms (CLOCK_MONOTONIC, modulo 2**31) + 1 beep-flag bit
Each strip has 32 cells, each 8 px wide and 16 px tall, white = 1, black = 0. The CRC covers the
3 counter bytes and the 4 time bytes, so a corrupted cell in either strip is detected.

Deviations from docs/specs/eval.md 5.3 (documented in the T0.2 report): cells are 8x16 instead of
8x8, the beep flag lives in strip 2 (so the counter keeps 24 bits), and capture time is the shared
monotonic clock in ms modulo 2**31 (about 24.8 days) rather than ms since sender start, so the
reader needs no side channel. Delay = (t_read_ms - stamp_ms) mod 2**31.

The stamp region (STAMP_EXCLUSION) must never be masked or covered; scenes keep targets away from it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

CELL_W = 8
STRIP_H = 16
N_CELLS = 32
STAMP_W = CELL_W * N_CELLS  # 256
STAMP_H = STRIP_H * 2  # 32
CANVAS_W, CANVAS_H = 1280, 720
#: x1, y1, x2, y2 of the stamp area on the canonical canvas; compositors and scenes keep clear of it.
STAMP_EXCLUSION = (0, 0, STAMP_W, STAMP_H)
MS_BITS = 31
MS_MASK = (1 << MS_BITS) - 1
MS_MODULUS = 1 << MS_BITS
COUNTER_MASK = (1 << 24) - 1
MIN_CONTRAST = 80.0  # darkest and brightest cell must differ by this much (0-255) for a stamp to count


@dataclass(frozen=True)
class Stamp:
    counter: int  # frame counter, 24 bits
    capture_ms: int  # monotonic ms modulo 2**31
    beep: bool


def crc8(data: bytes, poly: int = 0x07, init: int = 0x00) -> int:
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def gray_encode(n: int) -> int:
    return n ^ (n >> 1)


def gray_decode(g: int) -> int:
    n = 0
    while g:
        n ^= g
        g >>= 1
    return n


def monotonic_ms(t: float | None = None) -> int:
    """Capture time as the stamp stores it."""
    return int((time.monotonic() if t is None else t) * 1000.0) & MS_MASK


def delay_s(read_ms: float, stamp_ms: int) -> float:
    """Seconds between capture (stamp) and read; handles the 2**31 ms wrap."""
    return ((read_ms - stamp_ms) % MS_MODULUS) / 1000.0


def _payload_bytes(counter: int, word2: int) -> bytes:
    return (counter & COUNTER_MASK).to_bytes(3, "big") + word2.to_bytes(4, "big")


def encode_bits(counter: int, capture_ms: int, beep: bool) -> np.ndarray:
    """2 x 32 boolean cell matrix for the stamp."""
    counter &= COUNTER_MASK
    word2 = ((capture_ms & MS_MASK) << 1) | int(bool(beep))
    word1 = (gray_encode(counter) << 8) | crc8(_payload_bytes(counter, word2))
    bits = np.zeros((2, N_CELLS), dtype=bool)
    for i in range(N_CELLS):
        bits[0, i] = (word1 >> (N_CELLS - 1 - i)) & 1
        bits[1, i] = (word2 >> (N_CELLS - 1 - i)) & 1
    return bits


def stamp_sprite(counter: int, capture_ms: int, beep: bool) -> np.ndarray:
    """STAMP_H x STAMP_W uint8 image (0 or 255)."""
    bits = encode_bits(counter, capture_ms, beep)
    cells = np.repeat(np.repeat(bits, STRIP_H, axis=0), CELL_W, axis=1)
    return cells.astype(np.uint8) * 255


def draw_stamp(frame: np.ndarray, counter: int, capture_ms: int, beep: bool) -> None:
    """Draw the stamp into an HxWx3 (or HxW) uint8 frame in place. Frame must be 1280x720."""
    if frame.shape[1] != CANVAS_W or frame.shape[0] != CANVAS_H:
        raise ValueError("stamp is drawn on the 1280x720 canvas")
    sprite = stamp_sprite(counter, capture_ms, beep)
    if frame.ndim == 3:
        frame[0:STAMP_H, 0:STAMP_W, :] = sprite[..., None]
    else:
        frame[0:STAMP_H, 0:STAMP_W] = sprite


def read_stamp(frame: np.ndarray) -> Stamp | None:
    """Decode the stamp from an HxW (luma) or HxWx3 frame of any size (scaled from 1280x720).

    Samples the centre of every cell, thresholds at mid-grey, checks the CRC. Returns None when the CRC
    fails (the caller counts that as ``stamp_unreadable``).
    """
    h, w = frame.shape[:2]
    sx, sy = w / CANVAS_W, h / CANVAS_H
    # only the stamp corner is needed: crop before converting to luma (keeps decode well under 5 ms)
    crop = frame[: int(np.ceil(STAMP_H * sy)) + 2, : int(np.ceil(STAMP_W * sx)) + 2]
    if crop.ndim == 3:
        luma = crop[..., :3].mean(axis=2) if crop.shape[2] >= 3 else crop[..., 0]
    else:
        luma = crop
    xs0 = (np.arange(N_CELLS) * CELL_W + 2) * sx
    xs1 = (np.arange(N_CELLS) * CELL_W + CELL_W - 2) * sx
    bits = np.zeros((2, N_CELLS), dtype=bool)
    means = np.zeros((2, N_CELLS))
    for row in range(2):
        y0 = int(round((row * STRIP_H + 4) * sy))
        y1 = max(y0 + 1, int(round((row * STRIP_H + STRIP_H - 4) * sy)))
        strip = luma[y0:y1]
        for i in range(N_CELLS):
            a = int(round(xs0[i]))
            b = max(a + 1, int(round(xs1[i])))
            means[row, i] = strip[:, a:b].mean()
            bits[row, i] = means[row, i] > 127.5
    if means.max() - means.min() < MIN_CONTRAST:
        return (
            None  # a flat frame (slate, black or one colour) has no stamp: all-zero cells would pass the CRC
        )
    word1 = 0
    word2 = 0
    for i in range(N_CELLS):
        word1 = (word1 << 1) | int(bits[0, i])
        word2 = (word2 << 1) | int(bits[1, i])
    counter = gray_decode(word1 >> 8)
    if crc8(_payload_bytes(counter, word2)) != (word1 & 0xFF):
        return None
    return Stamp(counter=counter, capture_ms=word2 >> 1, beep=bool(word2 & 1))
