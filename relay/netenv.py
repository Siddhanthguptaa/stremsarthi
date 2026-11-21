"""Ingest-exposure settings from the process environment, with a ``.env`` fallback (new code).

Only the names in ``KEYS`` are ever read from ``.env``; the process environment always wins. Values
are returned stripped. Nothing here logs or prints a value.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

DOTENV = Path(__file__).resolve().parents[1] / ".env"
KEYS = ("INGEST_BIND_HOST", "INGEST_SRT_PASSPHRASE", "INGEST_PUBLISH_USER", "INGEST_PUBLISH_PASSWORD", "PAIRING_HOST")


def read_dotenv(path: Path | str = DOTENV) -> dict[str, str]:
    """``KEY=VALUE`` lines for ``KEYS`` only (quotes stripped, comments and other keys ignored)."""
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key in KEYS and value:
            out[key] = value
    return out


def effective_env(environ: Mapping[str, str] | None = None, dotenv: Path | str | None = None) -> dict[str, str]:
    """The environment overlaid on the ``.env`` subset (the environment wins).

    ``environ=None`` means the real process environment and the repo ``.env``. An explicit mapping
    (tests, callers that already merged) reads no file unless ``dotenv`` is given.
    """
    if environ is None:
        base, path = dict(os.environ), (DOTENV if dotenv is None else dotenv)
    else:
        base, path = dict(environ), dotenv
    if path is not None:
        for key, value in read_dotenv(path).items():
            if not base.get(key):
                base[key] = value
    return base
