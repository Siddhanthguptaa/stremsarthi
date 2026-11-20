"""Test doubles for every contract interface. Modules test against these, not each other."""

from .bus import InProcessBus, Subscription
from .chat import FakeChatClassifier
from .detector import FakeDetector, make_fake_detector
from .frames import FakeFrameSource
from .stt import FakeSTT
from .vision import FakeAllowlist, FakeEnroller, FakeResources

__all__ = [
    "FakeAllowlist",
    "FakeChatClassifier",
    "FakeDetector",
    "FakeEnroller",
    "FakeFrameSource",
    "FakeResources",
    "FakeSTT",
    "InProcessBus",
    "Subscription",
    "make_fake_detector",
]
