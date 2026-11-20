"""Face detector with an allowlist (YuNet + SFace, FAST lane) and the enroller. Exports ``DETECTOR``."""

from .detector import FacesDetector
from .enroll import Enroller

DETECTOR = FacesDetector

__all__ = ["DETECTOR", "Enroller", "FacesDetector"]
