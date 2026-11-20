"""Score one frame's masks against its ground truth, producing compact records (no images, no raw PII).

Record kinds (``k``):

* ``t``  one visible target (non-aux): ``cov`` = share of its quad under any mask, ``cov_cat`` = under masks of
         the categories its row expects, ``allow`` for allowlisted faces
* ``d``  one visible distractor (must not be masked): ``cov``
* ``m``  one mask quad: ``inside`` = share of the mask inside the ground-truth regions that justify its category,
         ``iou`` = best box IoU with a single justifying target, ``jt`` = how many such regions the frame has
         (0 = a stray mask: the frame holds nothing of that category)
* ``n``  one frame of a hard-negative bucket: ``masks`` = masks of the detector under test, ``any`` = all masks

All records carry ``src`` (img|clip), ``item``, ``bucket``, ``level``, ``frame`` and ``t``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from contracts import Category

from . import raster
from .rows import ALL_ROWS, MASK_JUSTIFIED_BY, NEGATIVE_BUCKETS

#: truth category -> contracts.Category values whose masks count for "category-attributed" recall
TRUTH_MASK_CATS: dict[str, frozenset[str]] = {}
for _r in ALL_ROWS:
    for _t in _r.truth:
        TRUTH_MASK_CATS[_t] = TRUTH_MASK_CATS.get(_t, frozenset()) | frozenset(
            c.value for c in _r.mask_categories
        )
TRUTH_NAMES = frozenset(TRUTH_MASK_CATS)

Pts = tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Mask:
    category: str  # contracts.Category value
    quad: Pts
    detector: str = ""


def masks_from_requests(requests: Iterable[Any]) -> list[Mask]:
    """Region masks of ``contracts.MaskRequest``s (full-frame and mouth requests are not region masks)."""
    out: list[Mask] = []
    for r in requests:
        if str(getattr(r.kind, "value", r.kind)) != "region":
            continue
        for q in r.quads:
            out.append(
                Mask(
                    str(getattr(r.category, "value", r.category)),
                    tuple(tuple(p) for p in q.pts),
                    str(r.source),
                )
            )
    return out


def _attrs(t: dict[str, Any]) -> dict[str, Any]:
    return {k: t[k] for k in ("script", "style", "form", "layout", "lang", "person_id") if k in t}


def score_frame(
    ft: dict[str, Any],
    masks: Sequence[Mask],
    *,
    src: str,
    item: str,
    bucket: str,
    level: str,
    frame: int = 0,
    t: float = 0.0,
    protect: bool = False,
    motion: str | None = None,
) -> list[dict[str, Any]]:
    """Records for one frame. ``protect`` = the engine would have slated this frame (every pixel covered)."""
    base = {"src": src, "item": item, "bucket": bucket, "level": level, "frame": frame, "t": round(t, 4)}
    if motion:
        base["motion"] = motion
    any_c = raster.MaskCanvas()
    if protect:
        any_c.fill_frame()
    else:
        any_c.add_all(m.quad for m in masks)
    cat_canvas: dict[frozenset[str], raster.MaskCanvas] = {}

    def canvas_for(cats: frozenset[str]) -> raster.MaskCanvas:
        if protect:
            return any_c
        if cats not in cat_canvas:
            c = raster.MaskCanvas()
            c.add_all(m.quad for m in masks if m.category in cats)
            cat_canvas[cats] = c
        return cat_canvas[cats]

    out: list[dict[str, Any]] = []
    for t_ in ft.get("targets", []):
        if t_.get("aux") or not t_.get("visible") or t_["category"] not in TRUTH_NAMES:
            continue
        cov = any_c.coverage(t_["quad"])
        cov_cat = canvas_for(TRUTH_MASK_CATS[t_["category"]]).coverage(t_["quad"])
        rec = {
            "k": "t",
            **base,
            "cat": t_["category"],
            "j": t_["j"],
            "cov": round(cov, 4),
            "cov_cat": round(cov_cat, 4),
        }
        if t_["category"] == "face":
            rec["allow"] = bool(t_.get("allowlisted"))
        attrs = _attrs(t_)
        if attrs:
            rec["attr"] = attrs
        out.append(rec)
    for d in ft.get("distractors", []):
        if not d.get("visible"):
            continue
        out.append({"k": "d", **base, "cat": d["category"], "cov": round(any_c.coverage(d["quad"]), 4)})

    if not protect and masks:
        truth_by_cat: dict[str, list[dict[str, Any]]] = {}
        for grp in ("targets", "context"):
            for t_ in ft.get(grp, []):
                truth_by_cat.setdefault(t_["category"], []).append(t_)
        justify: dict[tuple[str, ...], raster.MaskCanvas] = {}
        for m in masks:
            try:
                cats = MASK_JUSTIFIED_BY.get(Category(m.category), ())
            except ValueError:
                cats = ()
            if cats not in justify:
                jc = raster.MaskCanvas()
                for c in cats:
                    jc.add_all(x["quad"] for x in truth_by_cat.get(c, []))
                justify[cats] = jc
            targets = [x for c in cats for x in truth_by_cat.get(c, [])]
            iou = max((raster.bbox_iou(m.quad, x["quad"]) for x in targets), default=0.0)
            out.append(
                {
                    "k": "m", **base, "cat": m.category, "det": m.detector,
                    "inside": round(justify[cats].coverage(m.quad), 4), "iou": round(iou, 4),
                    "jt": len(targets),
                }
            )  # fmt: skip
    detector = NEGATIVE_BUCKETS.get(bucket)
    if detector is not None:
        out.append(
            {
                "k": "n", **base,
                "masks": sum(1 for m in masks if m.detector == detector),
                "any": len(masks),
                "protect": protect,
            }
        )  # fmt: skip
    return out
