"""Spoken-PII detection (T3.3): number words, UPI ids, address cues, Groq fallback. See ``docs/specs/audio.md``."""

from audio.pii.detector import SpokenPiiDetector, build_detector
from audio.pii.llm import GroqChatBackend, LlmBackend, PiiLlm
from audio.pii.rules import SpokenRules

__all__ = ["GroqChatBackend", "LlmBackend", "PiiLlm", "SpokenPiiDetector", "SpokenRules", "build_detector"]
