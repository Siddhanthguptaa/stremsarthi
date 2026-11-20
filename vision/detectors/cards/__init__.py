"""Aadhaar and PAN card detector (ACCURATE lane). Exports ``DETECTOR`` for the registry."""

from .detector import CardsDetector

DETECTOR = CardsDetector

__all__ = ["DETECTOR", "CardsDetector"]
