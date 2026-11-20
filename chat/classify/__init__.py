"""Chat classifier cascade (T4.2): rules, scam, doxxing, Groq LLM with a local-rules fallback."""

from .cascade import ChatClassifier, FastOutcome
from .doxxing import DoxxingDetector, find_numbers, verhoeff_valid
from .llm import (
    GroqTransport,
    LlmError,
    LlmRateLimited,
    LlmResult,
    LlmStage,
    LlmTransport,
    groq_transport_from_env,
)
from .normalize import Normalized, normalize
from .rules import RuleMatcher, RuleResult, default_matcher
from .scam import detect_scam

__all__ = [
    "ChatClassifier",
    "DoxxingDetector",
    "FastOutcome",
    "GroqTransport",
    "LlmError",
    "LlmRateLimited",
    "LlmResult",
    "LlmStage",
    "LlmTransport",
    "Normalized",
    "RuleMatcher",
    "RuleResult",
    "default_matcher",
    "detect_scam",
    "find_numbers",
    "groq_transport_from_env",
    "normalize",
    "verhoeff_valid",
]
