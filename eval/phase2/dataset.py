"""Read the T2.1 video suite (``python -m eval.run gen-video``) for scoring.

The manifest holds every image item with its full ground truth; clips carry a per-frame ``*.truth.jsonl`` and a
clip-level truth (target attributes live there, once). Nothing here prints or stores image content.

A *frame truth* is the dict the scorer works on::

    {"targets": [{category, quad, visible, aux, allowlisted, script, style, j}], "distractors": [...], "context": [...]}

``j`` is the target's index in the item's target list (the same index in every frame of a clip).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

ATTR_KEYS = ("allowlisted", "script", "style", "person_id", "form", "layout", "lang")


class DatasetError(RuntimeError):
    """The dataset is missing or unreadable (gate exit 2: nothing was measured)."""


@dataclass
class ImageItem:
    id: str
    bucket: str
    level: str
    path: Path
    truth: dict[str, Any]


@dataclass
class ClipItem:
    id: str
    bucket: str
    level: str
    motion: str
    n_frames: int
    path: Path
    frames: list[dict[str, Any]]  # per-frame truth records, merged with the clip-level attributes
    crf: int | None = None
    fps: int = 30


@dataclass
class Dataset:
    root: Path
    manifest: dict[str, Any]
    images: list[ImageItem] = field(default_factory=list)
    clips: list[ClipItem] = field(default_factory=list)

    @property
    def name(self) -> str:
        return str(self.manifest.get("name", self.root.name))

    def header(self) -> dict[str, Any]:
        m = self.manifest
        return {
            "dataset": self.name,
            "generator_version": m.get("generator_version"),
            "seed": m.get("seed"),
            "manifest_sha256": str(m.get("manifest_sha256", ""))[:16],
            "images": len(self.images),
            "clips": len(self.clips),
            "n_per_bucket": m.get("n_per_bucket"),
        }

    def enroll_sets(self) -> dict[str, Any]:
        return dict(self.manifest.get("enroll_sets") or {})


def _merge_attrs(base: dict[str, Any], keep: dict[str, Any], j: int) -> dict[str, Any]:
    out = {"category": base["category"], "quad": base["quad"], "visible": bool(base.get("visible")), "j": j}
    out["aux"] = bool(keep.get("aux", base.get("aux", False)))
    for k in ATTR_KEYS:
        if k in keep:
            out[k] = keep[k]
        elif k in base:
            out[k] = base[k]
    return out


def frame_truth_from_image(truth: dict[str, Any]) -> dict[str, Any]:
    return {
        "targets": [_merge_attrs(t, t, j) for j, t in enumerate(truth.get("targets", []))],
        "distractors": [_merge_attrs(t, t, j) for j, t in enumerate(truth.get("distractors", []))],
        "context": [_merge_attrs(t, t, j) for j, t in enumerate(truth.get("context", []))],
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load(root: str | Path, *, buckets: set[str] | None = None, max_per_bucket: int | None = None) -> Dataset:
    """Load the manifest (and the per-frame truth of every clip). ``buckets`` and ``max_per_bucket`` thin the
    set for quick runs; the manifest on disk is never changed."""
    root = Path(root)
    mp = root / "manifest.json"
    if not mp.exists():
        raise DatasetError(
            f"no manifest.json in {root} (generate it: python -m eval.run gen-video --out {root})"
        )
    try:
        manifest = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DatasetError(f"cannot read {mp}: {type(exc).__name__}") from exc
    ds = Dataset(root, manifest)
    seen: dict[str, int] = {}
    for it in manifest.get("items", []):
        b = it["bucket"]
        if buckets is not None and b not in buckets:
            continue
        if max_per_bucket is not None and seen.get(b, 0) >= max_per_bucket:
            continue
        seen[b] = seen.get(b, 0) + 1
        ds.images.append(ImageItem(it["id"], b, it["truth"]["level"], root / it["file"], it["truth"]))
    seen = {}
    for c in (manifest.get("clips") or {}).get("items", []):
        b = c["bucket"]
        if buckets is not None and b not in buckets:
            continue
        if max_per_bucket is not None and seen.get(b, 0) >= max_per_bucket:
            continue
        seen[b] = seen.get(b, 0) + 1
        tpath = root / c["truth_file"]
        if not tpath.exists():
            raise DatasetError(f"clip truth missing: {c['truth_file']}")
        attrs = {k: c["truth"].get(k, []) for k in ("targets", "distractors")}
        frames = []
        for rec in _read_jsonl(tpath):
            ft: dict[str, Any] = {"frame": rec["frame"], "t": rec["t"]}
            for key in ("targets", "distractors"):
                kept = attrs[key]
                ft[key] = [
                    _merge_attrs(r, kept[j] if j < len(kept) else {}, j) for j, r in enumerate(rec[key])
                ]
            ft["context"] = [_merge_attrs(r, {}, j) for j, r in enumerate(rec.get("context", []))]
            frames.append(ft)
        ds.clips.append(
            ClipItem(
                c["id"],
                b,
                c["level"],
                c["motion"],
                int(c["n_frames"]),
                root / c["video"],
                frames,
                c.get("crf"),
            )
        )
    return ds


def read_image_bgr(path: Path) -> np.ndarray:
    import cv2

    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise DatasetError(f"cannot read image {path.name}")
    return img


def iter_clip_frames(path: Path) -> Iterator[np.ndarray]:
    """Decoded BGR frames of a clip, in order."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise DatasetError(f"cannot open clip {path.name}")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                return
            yield frame
    finally:
        cap.release()


def validate(ds: Dataset) -> list[str]:
    """Problems that make a run meaningless (empty set, missing files). Empty list = fine."""
    problems: list[str] = []
    if not ds.images and not ds.clips:
        problems.append("the dataset has no images and no clips")
    missing = [i.id for i in ds.images if not i.path.exists()]
    if missing:
        problems.append(f"{len(missing)} image files missing (first: {missing[0]})")
    missing_c = [c.id for c in ds.clips if not c.path.exists()]
    if missing_c:
        problems.append(f"{len(missing_c)} clip files missing (first: {missing_c[0]})")
    return problems
