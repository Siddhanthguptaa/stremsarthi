"""Phone-screen and game-screen detector (ACCURATE lane). Exports ``DETECTOR`` for the registry."""

from .detector import ScreensDetector

DETECTOR = ScreensDetector

__all__ = ["DETECTOR", "ScreensDetector"]
