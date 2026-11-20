"""Shared chat plumbing: secrets, fixed-origin HTTP, rate limiting, connector base, bus publisher.

Frozen after T1.5: later tasks import from here and do not edit it.
"""

from .backoff import Backoff
from .bucket import TokenBucket
from .bus import ChatPublisher
from .clock import Clock, ManualClock, SystemClock
from .connector import Connector, FatalConnectorError, FinishedError, RetryableError
from .errors import ChatError, SecretError
from .http import FixedOriginClient, HttpResponse
from .logsafe import hash_id, log_event
from .results import ActionResult
from .secret import Secret

__all__ = [
    "ActionResult",
    "Backoff",
    "ChatError",
    "ChatPublisher",
    "Clock",
    "Connector",
    "FatalConnectorError",
    "FinishedError",
    "FixedOriginClient",
    "HttpResponse",
    "ManualClock",
    "RetryableError",
    "Secret",
    "SecretError",
    "SystemClock",
    "TokenBucket",
    "hash_id",
    "log_event",
]
