"""Assemble the chat eval set (``manifest.json``): about 2,000 labelled messages, seeded, regenerable.

Same ``(generator_version, seed)`` gives the same manifest hash. The manifest also carries the streamer's private
SPECIMEN terms and phone (the classifier is configured with them, as it would be in a real session).
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

from ..common import specimen
from ..common.seeds import py_rng
from . import GENERATOR_VERSION, abuse, clean, doxxing, scams
from .record import Msg

DEFAULT_SEED = 1234
PRIVATE_TERMS = ["aarav specimenkumar", "lakeview specimen school", "specimenpur colony"]
TARGET_TOTAL = 2000


def build_messages(seed: int = DEFAULT_SEED) -> tuple[list[Msg], dict[str, Any]]:
    phone = specimen.phone_number(py_rng(seed, "streamer_phone"))
    private = {"terms": list(PRIVATE_TERMS), "phones": [phone]}
    parts: list[Msg] = []
    parts += clean.clean_plain(py_rng(seed, "clean"), 360)
    parts += abuse.hard_negatives(py_rng(seed, "hn_abuse"), 150)
    parts += abuse.ambiguous(py_rng(seed, "amb"))
    parts += scams.hard_negatives(py_rng(seed, "hn_scam"), 100)
    parts += doxxing.hard_negatives(py_rng(seed, "hn_dox"), 90)
    parts += doxxing.staff_self(py_rng(seed, "staff_self"), private["terms"], phone, 40)
    parts += abuse.abuse(py_rng(seed, "abuse"))
    parts += scams.scams(py_rng(seed, "scam"))
    parts += scams.qr_lures(py_rng(seed, "qr"), 15)
    parts += scams.fake_upi(py_rng(seed, "upi"))
    parts += doxxing.doxxing(py_rng(seed, "dox"), private["terms"], phone)
    parts += doxxing.staff_dox(py_rng(seed, "staff_dox"), 20)
    seen: set[str] = set()
    uniq: list[Msg] = []
    for m in parts:
        key = m.text.strip().lower()
        if key not in seen:
            seen.add(key)
            uniq.append(m)
    random.Random(seed ^ 0xC4A7).shuffle(uniq)
    for i, m in enumerate(uniq):
        m.id = f"c{i:04d}"
        m.author = f"u{i:04d}"
        m.platform = "youtube" if i % 2 == 0 else "twitch"
    return uniq, private


def totals(msgs: list[Msg]) -> dict[str, Any]:
    return {
        "messages": len(msgs),
        "by_label": dict(sorted(Counter(m.label for m in msgs).items())),
        "by_kind": dict(sorted(Counter(f"{m.label}/{m.kind}" for m in msgs).items())),
        "by_lang": dict(sorted(Counter(m.lang for m in msgs).items())),
        "obfuscated": sum("obfuscated" in m.tags for m in msgs),
        "hard_negatives": sum("hard_negative" in m.tags for m in msgs),
        "staff": sum(bool(m.staff) for m in msgs),
        "not_gating": sum(not m.gate for m in msgs),
    }


def manifest_hash(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def build_suite(out_dir: Path, seed: int = DEFAULT_SEED) -> dict[str, Any]:
    msgs, private = build_messages(seed)
    manifest = {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "streamer_private": private,
        "totals": totals(msgs),
        "messages": [m.to_dict() for m in msgs],
    }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def load(out_dir: Path) -> tuple[dict[str, Any], list[Msg]]:
    m = json.loads((Path(out_dir) / "manifest.json").read_text(encoding="utf-8"))
    return m, [Msg.from_dict(d) for d in m["messages"]]
