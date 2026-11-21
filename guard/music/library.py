"""Load and save the demo fingerprint library (JSON of ids and fingerprints, never audio)."""

from __future__ import annotations

import json
from pathlib import Path

from .fingerprint import Library

PACKAGE_LIBRARY = Path(__file__).resolve().parent / "library" / "fingerprints.json"
FILE_NAME = "fingerprints.json"


def save_library(library: Library, path: str | Path) -> None:
    """Deterministic JSON: sorted keys, fixed separators, no timestamps."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(library.to_dict(), sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )


def load_library(path: str | Path | None = None) -> Library:
    """Load ``path`` (a file or a directory holding ``fingerprints.json``).

    With no path, or when the configured directory has no library yet, fall back to the demo
    library shipped with the package (synthetic songs, see ``build_library --synthetic``).
    """
    candidates: list[Path] = []
    if path:
        p = Path(path)
        candidates.append(p / FILE_NAME if p.is_dir() else p)
    candidates.append(PACKAGE_LIBRARY)
    for c in candidates:
        if c.is_file():
            return Library.from_dict(json.loads(c.read_text(encoding="utf-8")))
    return Library()
