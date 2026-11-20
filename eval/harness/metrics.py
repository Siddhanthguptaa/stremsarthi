"""Metric definitions (docs/specs/eval.md 5.5). Pure functions over plain numbers, no I/O, no randomness.

* frame-level recall with a 95 % Wilson interval, over frames after the warm-up
* precision of masks (IoU >= 0.5 with a ground-truth target)
* flicker (mask off-then-on inside a window on a continuously visible target)
* mute coverage and over-mute from an output-audio RMS envelope
* delay percentiles, counter continuity, capture gaps, A/V offsets
* visible-target rule (>= 60 % of the quad inside the frame, shortest side >= 24 px)
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

Point = tuple[float, float]

MUTE_THRESHOLD_DBFS = -50.0
MUTE_MIN_FRACTION = 0.9  # a word counts as muted when >= 90 % of its interval is <= -50 dBFS


# ---------------------------------------------------------------- intervals and ratios


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials (95 % for z = 1.96)."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True)
class Ratio:
    k: int
    n: int
    value: float | None
    ci_lo: float
    ci_hi: float


def ratio(k: int, n: int) -> Ratio:
    lo, hi = wilson_interval(k, n)
    return Ratio(k, n, (k / n) if n else None, lo, hi)


def precision(tp: int, fp: int) -> float | None:
    return tp / (tp + fp) if (tp + fp) else None


def recall(tp: int, fn: int) -> float | None:
    return tp / (tp + fn) if (tp + fn) else None


# ---------------------------------------------------------------- frame-level recall


def frame_recall(frames: Iterable[tuple[float, bool, bool]], warmup_s: float = 5.0) -> Ratio:
    """frames: (t_seconds, target_visible, leaked). Recall = 1 - leaked / visible after the warm-up.

    The returned Ratio is over *not leaked* frames, so ``value`` is the recall and the interval is its Wilson CI.
    """
    visible = leaked = 0
    for t, vis, leak in frames:
        if t < warmup_s or not vis:
            continue
        visible += 1
        leaked += 1 if leak else 0
    return ratio(visible - leaked, visible)


# ---------------------------------------------------------------- geometry


def polygon_area(pts: Sequence[Point]) -> float:
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def clip_polygon_to_rect(pts: Sequence[Point], w: float, h: float) -> list[Point]:
    """Sutherland-Hodgman clip against [0,w] x [0,h]."""

    def clip(poly: list[Point], inside, intersect) -> list[Point]:
        out: list[Point] = []
        for i, cur in enumerate(poly):
            prev = poly[i - 1]
            if inside(cur):
                if not inside(prev):
                    out.append(intersect(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(intersect(prev, cur))
        return out

    def x_at(p: Point, q: Point, x: float) -> Point:
        t = (x - p[0]) / (q[0] - p[0])
        return (x, p[1] + t * (q[1] - p[1]))

    def y_at(p: Point, q: Point, y: float) -> Point:
        t = (y - p[1]) / (q[1] - p[1])
        return (p[0] + t * (q[0] - p[0]), y)

    poly = [tuple(map(float, p)) for p in pts]
    for inside, inter in (
        (lambda p: p[0] >= 0, lambda a, b: x_at(a, b, 0.0)),
        (lambda p: p[0] <= w, lambda a, b: x_at(a, b, w)),
        (lambda p: p[1] >= 0, lambda a, b: y_at(a, b, 0.0)),
        (lambda p: p[1] <= h, lambda a, b: y_at(a, b, h)),
    ):
        if not poly:
            break
        poly = clip(poly, inside, inter)
    return poly


def quad_inside_fraction(quad: Sequence[Point], w: float = 1280, h: float = 720) -> float:
    area = polygon_area(quad)
    if area <= 0:
        return 0.0
    clipped = clip_polygon_to_rect(quad, w, h)
    return polygon_area(clipped) / area if len(clipped) >= 3 else 0.0


def shortest_side(quad: Sequence[Point]) -> float:
    return min(math.dist(quad[i], quad[(i + 1) % len(quad)]) for i in range(len(quad)))


def is_visible_target(
    quad: Sequence[Point], w: float = 1280, h: float = 720, min_inside: float = 0.6, min_side: float = 24.0
) -> bool:
    """Spec 5.2: visible when >= 60 % of the quad is in the frame and its shortest side is >= 24 px."""
    return quad_inside_fraction(quad, w, h) >= min_inside and shortest_side(quad) >= min_side


def iou_xyxy(a: Sequence[float], b: Sequence[float]) -> float:
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def mask_precision(
    masks: Sequence[Sequence[float]], truths: Sequence[Sequence[float]], iou_threshold: float = 0.5
) -> Ratio:
    """Fraction of masked boxes whose IoU with some ground-truth box is >= threshold."""
    good = sum(1 for m in masks if any(iou_xyxy(m, t) >= iou_threshold for t in truths))
    return ratio(good, len(masks))


def covered_fraction(box: Sequence[float], masks: Sequence[Sequence[float]]) -> float:
    """Fraction of `box` (xyxy, integer grid) covered by the union of `masks` (xyxy)."""
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    grid = np.zeros((y2 - y1, x2 - x1), dtype=bool)
    for m in masks:
        mx1, my1 = max(x1, int(round(m[0]))), max(y1, int(round(m[1])))
        mx2, my2 = min(x2, int(round(m[2]))), min(y2, int(round(m[3])))
        if mx2 > mx1 and my2 > my1:
            grid[my1 - y1 : my2 - y1, mx1 - x1 : mx2 - x1] = True
    return float(grid.mean())


# ---------------------------------------------------------------- flicker


def flicker_fraction(
    mask_on: Sequence[bool], visible: Sequence[bool], fps: int = 30
) -> tuple[int, int, float]:
    """(windows, flickering windows, fraction) over 1-second windows of a static target.

    Only windows in which the target is visible on every frame count. A window flickers when the mask
    goes on, then off, then on again inside it.
    """
    windows = flick = 0
    for start in range(0, len(mask_on) - fps + 1, fps):
        if not all(visible[start : start + fps]):
            continue
        windows += 1
        seg = list(mask_on[start : start + fps])
        seen_on = seen_off_after_on = False
        for v in seg:
            if v and not seen_on:
                seen_on = True
            elif not v and seen_on:
                seen_off_after_on = True
            elif v and seen_off_after_on:
                flick += 1
                break
    return windows, flick, (flick / windows) if windows else 0.0


# ---------------------------------------------------------------- audio


def rms_dbfs_envelope(pcm: np.ndarray, sample_rate: int, hop_s: float = 0.01) -> np.ndarray:
    """RMS in dBFS per hop of a float mono signal in [-1, 1]. Silence maps to -120."""
    hop = max(1, int(round(hop_s * sample_rate)))
    n = len(pcm) // hop
    if n == 0:
        return np.zeros(0)
    x = pcm[: n * hop].astype(np.float64).reshape(n, hop)
    rms = np.sqrt((x * x).mean(axis=1))
    return 20.0 * np.log10(np.maximum(rms, 1e-6))


def _hop_slice(start: float, end: float, hop_s: float, n: int) -> slice:
    return slice(max(0, int(round(start / hop_s))), min(n, int(round(end / hop_s))))


def muted_fraction(
    env_db: np.ndarray, hop_s: float, start: float, end: float, thresh_db: float = MUTE_THRESHOLD_DBFS
) -> float:
    seg = env_db[_hop_slice(start, end, hop_s, len(env_db))]
    return float((seg <= thresh_db).mean()) if seg.size else 0.0


@dataclass(frozen=True)
class MuteCoverage:
    coverage: float | None  # duration-weighted
    n_words: int
    n_covered: int
    covered_s: float
    total_s: float


def mute_coverage(
    pii_intervals: Sequence[tuple[float, float]],
    env_db: np.ndarray,
    hop_s: float,
    thresh_db: float = MUTE_THRESHOLD_DBFS,
    min_fraction: float = MUTE_MIN_FRACTION,
) -> MuteCoverage:
    """Sum of durations of PII words whose output RMS stays <= thresh for >= 90 % of the word,
    over the sum of durations of all PII words."""
    total = covered = 0.0
    n_cov = 0
    for s, e in pii_intervals:
        d = max(0.0, e - s)
        total += d
        if muted_fraction(env_db, hop_s, s, e, thresh_db) >= min_fraction:
            covered += d
            n_cov += 1
    return MuteCoverage((covered / total) if total > 0 else None, len(pii_intervals), n_cov, covered, total)


def over_mute(
    non_pii_speech: Sequence[tuple[float, float]],
    env_db: np.ndarray,
    hop_s: float,
    thresh_db: float = MUTE_THRESHOLD_DBFS,
) -> float | None:
    """Seconds of non-PII speech with output RMS <= thresh divided by total non-PII speech seconds."""
    total = muted = 0.0
    for s, e in non_pii_speech:
        sl = _hop_slice(s, e, hop_s, len(env_db))
        seg = env_db[sl]
        total += seg.size * hop_s
        muted += float((seg <= thresh_db).sum()) * hop_s
    return (muted / total) if total > 0 else None


# ---------------------------------------------------------------- delay and stream integrity


def delay_percentiles(delays_s: Sequence[float]) -> dict[str, float | int | None]:
    """p50 / p95 / max (and n) of per-frame delays in seconds (linear interpolation)."""
    if not len(delays_s):
        return {"n": 0, "p50_s": None, "p95_s": None, "max_s": None}
    a = np.asarray(delays_s, dtype=np.float64)
    return {
        "n": int(a.size),
        "p50_s": round(float(np.percentile(a, 50)), 4),
        "p95_s": round(float(np.percentile(a, 95)), 4),
        "max_s": round(float(a.max()), 4),
    }


def counter_continuity(seqs: Sequence[int]) -> dict[str, float | int | None]:
    """How many of the counters between the first and last seen were received."""
    if not len(seqs):
        return {"expected": 0, "received": 0, "missing": 0, "received_fraction": None}
    uniq = sorted(set(seqs))
    expected = uniq[-1] - uniq[0] + 1
    return {
        "expected": expected,
        "received": len(uniq),
        "missing": expected - len(uniq),
        "received_fraction": len(uniq) / expected,
    }


def capture_gaps(stamp_ms: Sequence[int], modulus: int, over_ms: float = 100.0) -> dict[str, float | int]:
    """Gaps between the capture times of consecutive received frames (loss shows up as a large gap)."""
    if len(stamp_ms) < 2:
        return {"max_gap_ms": 0.0, "n_over_100ms": 0}
    gaps = [(b - a) % modulus for a, b in zip(stamp_ms, stamp_ms[1:], strict=False)]
    return {"max_gap_ms": float(max(gaps)), "n_over_100ms": sum(1 for g in gaps if g > over_ms)}


def av_offsets_ms(
    audio_onsets_s: Sequence[float], video_flag_pts_s: Sequence[float], max_pair_s: float = 0.5
) -> dict[str, float | int | None]:
    """Pair every audio onset with the nearest beep-flag frame; offset = audio - video, in ms."""
    offs: list[float] = []
    for a in audio_onsets_s:
        if not len(video_flag_pts_s):
            break
        v = min(video_flag_pts_s, key=lambda x: abs(x - a))
        if abs(v - a) <= max_pair_s:
            offs.append((a - v) * 1000.0)
    if not offs:
        return {"n": 0, "mean_ms": None, "max_abs_ms": None}
    return {
        "n": len(offs),
        "mean_ms": round(float(np.mean(offs)), 3),
        "max_abs_ms": round(float(np.max(np.abs(offs))), 3),
    }


# ---------------------------------------------------------------- checks and baselines


def check(value: float | None, op: str, target: float) -> bool:
    """Evaluate ``value <op> target`` (>=, <=, ==, <, >). None never passes."""
    if value is None:
        return False
    return {
        ">=": value >= target,
        "<=": value <= target,
        "==": value == target,
        "<": value < target,
        ">": value > target,
    }[op]


def regression(current: float, baseline: float, tolerance: float, higher_is_worse: bool = True) -> bool:
    """True when `current` is worse than `baseline` by more than `tolerance`."""
    delta = current - baseline if higher_is_worse else baseline - current
    return delta > tolerance
