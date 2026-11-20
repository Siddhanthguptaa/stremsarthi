"""Text PII and signboard detector (ACCURATE lane). Exports ``DETECTOR`` for the registry."""

from .detector import TextDetector

DETECTOR = TextDetector

__all__ = ["DETECTOR", "TextDetector"]
