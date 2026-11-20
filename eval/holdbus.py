"""Send hold commands over the real ZeroMQ bus using the ``contracts`` wire format (D-2, D-35).

Publishers connect to the engine broker's XSUB side, ``ipc:///tmp/streamsarthi-bus-in``; the message is the two
frames ``[b"control.hold", contracts.dumps(HoldCommand(...))]``. A PUB socket only sends what its peer
subscribed to, so this client uses an XPUB socket and waits for the broker's subscription on the hold topic
before the first send (``connect`` returns False when none arrives, which means no engine is listening).
"""

from __future__ import annotations

import time
from typing import Any

from contracts import HoldCommand, HoldSource, Topic, dumps

DEFAULT_XSUB_ENDPOINT = "ipc:///tmp/streamsarthi-bus-in"
DEFAULT_XPUB_ENDPOINT = "ipc:///tmp/streamsarthi-bus"
HOLD_TOPIC = Topic.HOLD.value  # "control.hold"


class HoldClient:
    def __init__(self, xsub_endpoint: str = DEFAULT_XSUB_ENDPOINT, actor: str = "evalharness") -> None:
        self.endpoint = xsub_endpoint
        self.actor = actor
        self._ctx: Any = None
        self._sock: Any = None

    def connect(self, timeout_s: float = 5.0) -> bool:
        import zmq

        self._ctx = zmq.Context()
        self._sock = self._ctx.socket(zmq.XPUB)
        self._sock.setsockopt(zmq.LINGER, 200)
        self._sock.connect(self.endpoint)
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            if self._sock.poll(100):
                msg = self._sock.recv()
                if msg[:1] == b"\x01" and msg[1:] in (HOLD_TOPIC.encode(), b""):
                    return True
        return False

    def send(self, active: bool, source: HoldSource = HoldSource.API) -> float:
        """Publish one hold (or release). Returns ``time.monotonic()`` taken just before the send."""
        if self._sock is None:
            raise RuntimeError("HoldClient.connect() first")
        payload = dumps(HoldCommand(source=source, active=active, actor=self.actor))
        t = time.monotonic()
        self._sock.send_multipart([HOLD_TOPIC.encode(), payload])
        return t

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close(linger=200)
            self._sock = None
        if self._ctx is not None:
            self._ctx.term()
            self._ctx = None
