"""Pure aggregation of scoring records (no I/O, no randomness): per-category recall with a 95 % Wilson interval,
per difficulty level, precision, distractor and hard-negative false masks, flicker on static clips, lane latency.

Same records in, same numbers out: ``phase2-report`` can re-score a run from ``offline-records.jsonl``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np

from ..harness import metrics
from .rows import ALL_ROWS, ALLOWED_MAX_COVER, COVER_MIN, LANE_BUDGET_MS, LEVELS, MASK_JUSTIFIED_BY, Row

SRCS = ("img", "clip")


# ---------------------------------------------------------------- one recall block
def success(rec: dict[str, Any], row: Row, cover_min: float = COVER_MIN, by_category: bool = False) -> bool:
    cov = rec["cov_cat"] if by_category else rec["cov"]
    if row.kind == "face_allowed":
        return bool(cov < ALLOWED_MAX_COVER)
    return bool(cov >= cover_min)


def select(records: Iterable[dict[str, Any]], row: Row) -> list[dict[str, Any]]:
    out = []
    for r in records:
        if r["k"] != "t" or r["cat"] not in row.truth:
            continue
        if row.kind == "face_masked" and r.get("allow"):
            continue
        if row.kind == "face_allowed" and not r.get("allow"):
            continue
        out.append(r)
    return out


def ratio_dict(k: int, n: int) -> dict[str, Any]:
    r = metrics.ratio(k, n)
    return {"k": k, "n": n, "value": r.value, "ci_lo": round(r.ci_lo, 4), "ci_hi": round(r.ci_hi, 4)}


def block(recs: Sequence[dict[str, Any]], row: Row, cover_min: float) -> dict[str, Any]:
    ok = sum(1 for r in recs if success(r, row, cover_min))
    ok_cat = sum(1 for r in recs if success(r, row, cover_min, by_category=True))
    out = ratio_dict(ok, len(recs))
    out["value_by_category"] = (ok_cat / len(recs)) if recs else None
    out["items"] = len({r["item"] for r in recs})
    return out


def by_level(recs: Sequence[dict[str, Any]], row: Row, cover_min: float) -> dict[str, dict[str, Any]]:
    return {lv: block([r for r in recs if r["level"] == lv], row, cover_min) for lv in LEVELS}


def by_attr(
    recs: Sequence[dict[str, Any]], row: Row, cover_min: float
) -> dict[str, dict[str, dict[str, Any]]]:
    groups: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for r in recs:
        for k, v in (r.get("attr") or {}).items():
            if k == "person_id":
                continue
            groups[k][str(v)].append(r)
    return {
        k: {v: block(rs, row, cover_min) for v, rs in sorted(vs.items())}
        for k, vs in sorted(groups.items())
        if len(vs) > 1 or k in ("script", "style")
    }


def missed_items(recs: Sequence[dict[str, Any]], row: Row, cover_min: float) -> list[str]:
    """Ids (never images) of the items with at least one missed target frame, sorted."""
    return sorted({f"{r['item']}" for r in recs if not success(r, row, cover_min)})


def row_stats(
    records: Sequence[dict[str, Any]],
    row: Row,
    cover_min: float = COVER_MIN,
    gate_levels: Iterable[str] = LEVELS,
) -> dict[str, Any]:
    sel = select(records, row)
    gl = set(gate_levels)
    out: dict[str, Any] = {"key": row.key, "title": row.title, "target": row.target, "kind": row.kind}
    for src in SRCS:
        rs = [r for r in sel if r["src"] == src]
        gated = [r for r in rs if r["level"] in gl]
        out[src] = {
            "all": block(rs, row, cover_min),
            "gate": block(gated, row, cover_min),
            "levels": by_level(rs, row, cover_min),
            "attrs": by_attr(rs, row, cover_min) if src == "img" else {},
            "missed": missed_items(gated, row, cover_min)[:60],
        }
    return out


# ---------------------------------------------------------------- precision, distractors, negatives
def precision_stats(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Per (source, mask category): masks, share inside justifying ground truth, share with IoU >= 0.5 against a
    single target (spec 5.5) and ``stray`` = masks on frames holding nothing of that category. Stills and clips are
    kept apart (a clip repeats the same masks on every frame)."""
    cats: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"masks": 0, "inside": 0, "iou": 0, "stray": 0}
    )
    other = 0
    known = {c.value for c in MASK_JUSTIFIED_BY}
    for r in records:
        if r["k"] != "m":
            continue
        if r["cat"] not in known:
            other += 1
            continue
        c = cats[(r["src"], r["cat"])]
        c["masks"] += 1
        c["inside"] += 1 if r["inside"] >= 0.5 else 0
        c["iou"] += 1 if r["iou"] >= 0.5 else 0
        c["stray"] += 1 if r.get("jt", 1) == 0 else 0

    def pack(c: dict[str, int]) -> dict[str, Any]:
        return {
            "masks": c["masks"],
            "precision_inside": ratio_dict(c["inside"], c["masks"]),
            "precision_iou50": ratio_dict(c["iou"], c["masks"]),
            "stray": c["stray"],
            "stray_rate": (c["stray"] / c["masks"]) if c["masks"] else None,
        }

    by: dict[str, dict[str, Any]] = {}
    for (src, cat), c in sorted(cats.items()):
        by[f"{cat}/{src}"] = pack(c)
    allc: dict[str, dict[str, Any]] = {}
    for src in SRCS:
        tot = {
            k: sum(c[k] for (s_, _), c in cats.items() if s_ == src)
            for k in ("masks", "inside", "iou", "stray")
        }
        allc[src] = pack(tot)
    return {"by_category": by, "all": allc, "masks_of_other_categories": other}


def distractor_stats(records: Iterable[dict[str, Any]], masked_at: float = 0.5) -> dict[str, Any]:
    cats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in records:
        if r["k"] != "d":
            continue
        cats[r["cat"]][0] += 1
        cats[r["cat"]][1] += 1 if r["cov"] >= masked_at else 0
    n = sum(v[0] for v in cats.values())
    m = sum(v[1] for v in cats.values())
    return {
        "all": ratio_dict(m, n) | {"meaning": "visible distractors with >= 50 % under a mask"},
        "by_category": {k: ratio_dict(v[1], v[0]) for k, v in sorted(cats.items())},
    }


def negative_stats(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    b: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "false": 0, "any": 0, "protect": 0})
    for r in records:
        if r["k"] != "n":
            continue
        for key in (r["bucket"] + "/" + r["src"],):
            d = b[key]
            d["n"] += 1
            d["false"] += 1 if r["masks"] > 0 else 0
            d["any"] += 1 if r["any"] > 0 else 0
            d["protect"] += 1 if r.get("protect") else 0
    return {k: v | {"rate": (v["false"] / v["n"]) if v["n"] else None} for k, v in sorted(b.items())}


# ---------------------------------------------------------------- flicker
def flicker_stats(
    records: Iterable[dict[str, Any]],
    rows: Sequence[Row] = ALL_ROWS,
    cover_min: float = COVER_MIN,
    fps: int = 30,
) -> dict[str, Any]:
    """Mask on -> off -> on inside one second on a continuously visible target of a static clip."""
    series: dict[tuple[str, int], dict[str, Any]] = {}
    key_of: dict[str, str] = {}
    for r in rows:
        if not r.gated or r.kind == "face_allowed":
            continue
        for t in r.truth:
            key_of.setdefault(t, r.key)
    nmax: dict[str, int] = defaultdict(int)
    for r in records:
        if r["k"] != "t" or r["src"] != "clip" or r.get("motion") != "static" or r["cat"] not in key_of:
            continue
        if r.get("allow"):
            continue
        s = series.setdefault((r["item"], r["j"]), {"cat": r["cat"], "level": r["level"], "obs": {}})
        s["obs"][r["frame"]] = r["cov"] >= cover_min
        nmax[r["item"]] = max(nmax[r["item"]], r["frame"] + 1)
    tot_w = tot_f = 0
    by_key: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_level: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    flickering: list[str] = []
    for (item, j), s in sorted(series.items()):
        n = nmax[item]
        vis = [f in s["obs"] for f in range(n)]
        on = [bool(s["obs"].get(f, False)) for f in range(n)]
        w, f, _ = metrics.flicker_fraction(on, vis, fps)
        tot_w += w
        tot_f += f
        by_key[key_of[s["cat"]]][0] += w
        by_key[key_of[s["cat"]]][1] += f
        by_level[s["level"]][0] += w
        by_level[s["level"]][1] += f
        if f:
            flickering.append(f"{item}#{j}")
    return {
        "windows": tot_w,
        "flickering": tot_f,
        "fraction": (tot_f / tot_w) if tot_w else None,
        "ci": [round(x, 4) for x in metrics.wilson_interval(tot_f, tot_w)],
        "static_clips": len({i for i, _ in series}),
        "by_row": {k: {"windows": v[0], "flickering": v[1], "fraction": (v[1] / v[0]) if v[0] else None} for k, v in sorted(by_key.items())},
        "by_level": {k: {"windows": v[0], "flickering": v[1], "fraction": (v[1] / v[0]) if v[0] else None} for k, v in sorted(by_level.items())},
        "flickering_ids": flickering[:60],
    }  # fmt: skip


# ---------------------------------------------------------------- latency
def pct(values: Sequence[float], q: float) -> float | None:
    return round(float(np.percentile(np.asarray(values, dtype=np.float64), q)), 2) if len(values) else None


def latency_table(samples_ms: dict[str, Sequence[float]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for k, v in sorted(samples_ms.items()):
        out[k] = {
            "n": len(v),
            "p50_ms": pct(v, 50),
            "p95_ms": pct(v, 95),
            "max_ms": round(float(max(v)), 2) if len(v) else None,
        }
    return out


def lane_latency(lane_ms: dict[str, Sequence[float]]) -> dict[str, dict[str, Any]]:
    out = latency_table(lane_ms)
    for lane, row in out.items():
        budget = LANE_BUDGET_MS.get(lane)
        row["budget_ms"] = budget
        row["within_budget"] = None if budget is None or row["p95_ms"] is None else row["p95_ms"] <= budget
    return out
