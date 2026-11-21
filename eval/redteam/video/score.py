"""Pure aggregation of red-team records into leak rates, ranked misses and the regression check (no I/O)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from ...harness import metrics

REGRESSION_POINTS = 0.05  # spec 5.9 (gate_final): a regression of more than 5 points fails


def rate(k: int, n: int) -> dict[str, Any]:
    r = metrics.ratio(k, n)
    return {"k": k, "n": n, "rate": r.value, "ci": [round(r.ci_lo, 4), round(r.ci_hi, 4)]}


def fmt(d: dict[str, Any]) -> str:
    if not d["n"]:
        return "n/a"
    return f"{d['rate']:.1%} ({d['k']}/{d['n']}) CI {d['ci'][0]:.0%}-{d['ci'][1]:.0%}"


def is_confirmed(r: dict[str, Any]) -> bool:
    return r.get("ro") == "confirmed"


def is_leak(r: dict[str, Any]) -> bool:
    """The headline leak: the coverage proxy says uncovered, or the output read-back recovered the target."""
    return bool(r["proxy"]) or is_confirmed(r)


def summarise(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rs = list(records)
    checked = [r for r in rs if "ro" in r]
    return {
        "frames": rate(sum(1 for r in rs if is_leak(r)), len(rs)) | {"what": "leak = proxy or confirmed"},
        "proxy": rate(sum(1 for r in rs if r["proxy"]), len(rs)),
        "confirmed": rate(sum(1 for r in checked if is_confirmed(r)), len(checked)),
        "src_unreadable": sum(1 for r in checked if r["ro"] == "src_unreadable"),
        "src_readable": sum(1 for r in checked if r["ro"] == "src_readable"),
        "checked": len(checked),
    }


def group_by(records: Iterable[dict[str, Any]], *keys: str) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    out: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        out[tuple(r.get(k, "") for k in keys)].append(r)
    return dict(out)


def by_attack(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for (name,), rs in sorted(group_by(records, "attack").items()):
        params = {p: summarise(prs) for (_, p), prs in sorted(group_by(rs, "attack", "param").items())}
        tags = {t: summarise(trs) for (t,), trs in sorted(group_by(rs, "tag").items()) if t}
        out[name] = {"group": rs[0]["group"], **summarise(rs), "params": params, "tags": tags}
    return out


def by_group(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {g: summarise(rs) for (g,), rs in sorted(group_by(records, "group").items())}


def rank_misses(records: Sequence[dict[str, Any]], top: int = 20) -> list[dict[str, Any]]:
    """Real misses, worst first: a read-back-confirmed leak outranks a proxy-only leak; within each, by rate.
    Cases whose only leaks are ``src_unreadable`` (the attack made the target illegible even unmasked) are kept at
    the bottom, flagged, because OCR is weaker than a person and they still mean the detector did not mask."""
    out = []
    for (attack, param), rs in group_by(records, "attack", "param").items():
        leaks = [r for r in rs if is_leak(r)]
        if not leaks:
            continue
        conf = [r for r in rs if is_confirmed(r)]
        checked = [r for r in rs if "ro" in r]
        worst = (conf or [r for r in leaks if r.get("ro") != "src_unreadable"] or leaks)[0]
        out.append(
            {
                "attack": attack,
                "param": param,
                "group": rs[0]["group"],
                "n": len(rs),
                "leak": rate(len(leaks), len(rs)),
                "confirmed": rate(len(conf), len(checked)),
                "kind": "confirmed"
                if conf
                else (
                    "proxy_readable"
                    if any(r.get("ro") in (None, "src_readable") for r in leaks)
                    else "proxy_unreadable"
                ),
                "worst": {k: worst.get(k) for k in ("case", "frame", "j", "cat", "cov", "ro", "tag")},
            }
        )
    order = {"confirmed": 0, "proxy_readable": 1, "proxy_unreadable": 2}
    out.sort(key=lambda m: (order[m["kind"]], -(m["confirmed"]["rate"] or 0.0), -m["leak"]["rate"], -m["n"]))
    return out[:top]


def to_baseline(per_attack: dict[str, Any], per_group: dict[str, Any]) -> dict[str, Any]:
    return {
        "attacks": {k: v["frames"]["rate"] for k, v in per_attack.items()},
        "groups": {k: v["frames"]["rate"] for k, v in per_group.items()},
    }


def regressions(
    current: dict[str, Any], baseline: dict[str, Any], points: float = REGRESSION_POINTS
) -> list[str]:
    """Names of the groups (and attacks) whose leak rate rose by more than ``points`` over the baseline."""
    bad = []
    for kind in ("groups", "attacks"):
        for name, base in (baseline.get(kind) or {}).items():
            cur = (current.get(kind) or {}).get(name)
            if cur is None or base is None:
                continue
            if cur - base > points + 1e-9:
                bad.append(f"{kind[:-1]} {name}: {base:.1%} -> {cur:.1%}")
    return bad
