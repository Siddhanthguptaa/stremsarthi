"""``MOUTH_OF_SPEAKER`` resolution (docs/specs/engine.md 5.7, T3.4). New code, no upstream.

Audio asks for the speaker's mouth to be blurred while a word is muted (so nobody can lip-read it).
The request carries no geometry: the mouth lives in ``Detection.aux_quads["mouth"]`` of the
*allowlisted* faces that vision reports in ``LaneResult.aux_detections``. For each frame inside a
request window the engine

1. takes, over every lane result that is ``ok``, finished before the frame's deadline, inside the
   coverage window and within its lane's maximum age, the one with the allowlisted mouth quads
   closest to the frame in sequence (newest wins a tie); the delay buffer makes results from frames
   after this one available, which beats extrapolating from an older one;
2. projects those quads from the result's frame to this frame through the same motion path as every
   other mask (a quad that cannot be projected is skipped);
3. pads each quad by ``MOUTH_PAD`` of its own size on every side.

If no allowlisted face is visible nothing is painted: every non-allowlisted face is already masked by
vision. The compositor paints the returned quads with a strong BLUR (``engine/compositor.py``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

from contracts import Detection, Lane, LaneResult, Quad

MOUTH_PAD = 0.25  # fraction of the mouth quad's width/height added on every side
MIN_MOUTH_KERNEL = 51  # strong blur: a lip-reader gets nothing from a 51 px stack blur on a mouth crop

Project = Callable[[list[list[list[float]]], int, int, float | None], list | None]


def mouth_detections(result: LaneResult) -> tuple[Detection, ...]:
    """The allowlisted faces of ``result`` that carry a mouth quad."""
    return tuple(d for d in result.aux_detections if d.allow and "mouth" in d.aux_quads)


def pad_quad(quad: Quad, fraction: float = MOUTH_PAD) -> Quad:
    """Push every corner ``fraction`` of the quad's bounding-box size away from the centre, per axis."""
    x1, y1, x2, y2 = quad.bbox()
    cx, cy = quad.center()
    dx, dy = fraction * (x2 - x1), fraction * (y2 - y1)
    corners = tuple(
        (p[0] + (dx if p[0] >= cx else -dx), p[1] + (dy if p[1] >= cy else -dy)) for p in quad.pts
    )
    return Quad(corners)  # type: ignore[arg-type]


def pick_source(
    results: Iterable[LaneResult],
    index: int,
    deadline: float,
    window: tuple[int, int],
    max_age: Mapping[Lane, int],
) -> LaneResult | None:
    """The usable result with allowlisted mouths nearest to frame ``index`` (newest on a tie)."""
    lo, hi = window
    best: LaneResult | None = None
    best_key: tuple[int, int] | None = None
    for result in results:
        if not result.ok or result.frame_seq < lo or result.frame_seq > hi or result.completed_at > deadline:
            continue
        age = abs(result.frame_seq - index)
        if age > max_age.get(result.lane, 0) or not mouth_detections(result):
            continue
        key = (age, -result.frame_seq)
        if best_key is None or key < best_key:
            best, best_key = result, key
    return best


def resolve_mouth_quads(
    results: Iterable[LaneResult],
    index: int,
    deadline: float,
    window: tuple[int, int],
    max_age: Mapping[Lane, int],
    project: Project,
    pad: float = MOUTH_PAD,
) -> tuple[Quad, ...]:
    """Padded mouth quads of the allowlisted faces, projected onto frame ``index`` (empty if none)."""
    source = pick_source(results, index, deadline, window, max_age)
    if source is None:
        return ()
    quads: Sequence[Quad] = [d.aux_quads["mouth"] for d in mouth_detections(source)]
    flat = [[list(point) for point in quad.pts] for quad in quads]
    projected = project(flat, source.frame_seq, index, deadline)
    if projected is None:
        return ()
    out = [
        pad_quad(Quad(tuple((float(x), float(y)) for x, y in corners)), pad)  # type: ignore[arg-type]
        for corners in projected
    ]
    return tuple(out)
