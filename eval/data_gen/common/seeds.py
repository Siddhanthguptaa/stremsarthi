"""Seed derivation so every dataset regenerates bit-for-bit from (generator_version, seed)."""

from __future__ import annotations

import hashlib
import random

import numpy as np


def derive_seed(seed: int, *names: object) -> int:
    """Stable 63-bit sub-seed from a master seed and a path of names (item id, field, ...)."""
    h = hashlib.sha256(repr((int(seed), *names)).encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") >> 1


def py_rng(seed: int, *names: object) -> random.Random:
    return random.Random(derive_seed(seed, *names))


def np_rng(seed: int, *names: object) -> np.random.Generator:
    return np.random.default_rng(derive_seed(seed, *names))
