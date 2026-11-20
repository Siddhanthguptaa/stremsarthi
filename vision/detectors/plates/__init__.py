"""Indian number-plate detector (ACCURATE lane). Exports ``DETECTOR`` for the registry."""

from .detector import PlatesDetector

DETECTOR = PlatesDetector

__all__ = ["DETECTOR", "PlatesDetector"]
