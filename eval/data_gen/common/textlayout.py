"""Mixed-script text layout on top of Pillow + raqm: measure, wrap and draw lines, and locate substrings.

A line may mix Latin and Devanagari (Hinglish in both scripts). Text is split into runs by script; each run
uses its own Noto font and the run extents are returned so callers can compute the exact box of a substring
(for example the OTP digits inside a notification) without any OCR.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import ImageDraw

from .fonts import load_font


def script_of(ch: str) -> str | None:
    cp = ord(ch)
    if 0x0900 <= cp <= 0x097F:
        return "devanagari"
    if ch.isspace():
        return None
    return "latin"


@dataclass(frozen=True)
class Run:
    text: str
    script: str


def split_runs(text: str) -> list[Run]:
    runs: list[Run] = []
    cur, cur_sc = "", None
    for ch in text:
        sc = script_of(ch) or cur_sc or "latin"
        if sc != cur_sc and cur:
            runs.append(Run(cur, cur_sc or "latin"))
            cur = ""
        cur += ch
        cur_sc = sc
    if cur:
        runs.append(Run(cur, cur_sc or "latin"))
    return runs


def text_width(text: str, size: int, weight: int = 400) -> float:
    return sum(load_font(r.script, size, weight).getlength(r.text) for r in split_runs(text))


def wrap(text: str, size: int, max_w: float, weight: int = 400) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if text_width(trial, size, weight) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def draw_line(
    d: ImageDraw.ImageDraw,
    x: float,
    baseline: float,
    text: str,
    size: int,
    fill: tuple[int, int, int],
    weight: int = 400,
) -> list[tuple[str, float, float]]:
    """Draw one line left-aligned at `x` on the baseline; returns (run text, x1, x2) per run."""
    out = []
    cx = x
    for r in split_runs(text):
        f = load_font(r.script, size, weight)
        d.text((cx, baseline), r.text, font=f, fill=fill, anchor="ls")
        w = f.getlength(r.text)
        out.append((r.text, cx, cx + w))
        cx += w
    return out


def substring_box(
    text: str, sub: str, x: float, baseline: float, size: int, weight: int = 400
) -> tuple[float, float, float, float] | None:
    """Pixel box (x1, y1, x2, y2) of the first occurrence of `sub` in a line drawn by ``draw_line``."""
    i = text.find(sub)
    if i < 0:
        return None
    x1 = x + text_width(text[:i], size, weight)
    x2 = x1 + text_width(sub, size, weight)
    asc = load_font("latin", size, weight).getmetrics()
    return (x1, baseline - asc[0], x2, baseline + asc[1])
