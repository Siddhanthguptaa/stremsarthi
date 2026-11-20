"""UPI payment QR detector (FAST lane). Exports ``DETECTOR`` for the registry."""

from .detector import UpiQrDetector

DETECTOR = UpiQrDetector

__all__ = ["DETECTOR", "UpiQrDetector"]
