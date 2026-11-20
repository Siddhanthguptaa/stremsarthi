"""A classifier finding, shared by the rule, scam and doxxing stages."""

from __future__ import annotations

from dataclasses import dataclass

from contracts import Category


@dataclass(frozen=True, slots=True)
class Candidate:
    category: Category
    score: float
    reason: str  # machine code, never text
    severe: bool = False  # severity-3 abuse / threat: uses the ``severe_abuse`` action
