"""Strike guard risk part (T5.2): profanity, risky-text signals, risk aggregator and meter."""

from .aggregator import RiskAggregator, Update
from .meter import LevelTracker
from .profanity import ProfanityDetector, ProfanityMatcher, normalize_token
from .service import RiskService, StreamClock, build
from .sources import SignalSources, risky_text_signal

__all__ = [
    "LevelTracker",
    "ProfanityDetector",
    "ProfanityMatcher",
    "RiskAggregator",
    "RiskService",
    "SignalSources",
    "StreamClock",
    "Update",
    "build",
    "normalize_token",
    "risky_text_signal",
]
