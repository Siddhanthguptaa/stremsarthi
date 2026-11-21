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
    #: Doubtful, mostly harmless on its own (reported speech, scam talk, a vague suspicion). With a Groq stage it
    #: goes there; with none (or when Groq is unreachable) the local fallback is ALLOW, not HOLD_FOR_MOD.
    lenient: bool = False
