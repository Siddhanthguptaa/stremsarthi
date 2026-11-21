"""Audio tap: hands every 16 kHz ``AudioChunk`` to an optional service's ``feed`` without ever blocking.

New code. ``Session.on_audio`` runs on the ingest thread, so it may only do a bounded queue put. A
worker thread per tap calls ``feed``. A full queue drops the chunk (counted), an exception is swallowed
and logged once as its class name (never the chunk, never a message), and nothing is fed after ``close``.
The chunk object is shared, not copied: taps must treat it as read-only (the audio host already does).
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger("streamsarthi.engine.audio_tee")

DEFAULT_DEPTH = 256  # 20 ms chunks: about 5 s of audio, the longest a slow tap may lag before it drops


class AudioTee:
    def __init__(self, name: str, feed: Callable[[Any], Any], *, depth: int = DEFAULT_DEPTH) -> None:
        self.name, self._feed = name, feed
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=depth)
        self._closed = threading.Event()
        self._logged = False
        self.fed = 0
        self.dropped = 0
        self.errors = 0
        self._thread = threading.Thread(target=self._run, name=f"engine-tee-{name}", daemon=True)
        self._thread.start()

    def offer(self, chunk: Any) -> bool:
        """Queue ``chunk`` for the tap. Never blocks and never raises; False if it was not queued."""
        if self._closed.is_set():
            return False
        try:
            self._queue.put_nowait(chunk)
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def _run(self) -> None:
        while True:
            chunk = self._queue.get()
            if chunk is None or self._closed.is_set():
                return
            try:
                self._feed(chunk)
                self.fed += 1
            except Exception as exc:  # the tap is optional: contain it, say so once
                self.errors += 1
                if not self._logged:
                    self._logged = True
                    log.warning(
                        "audio tap %r feed failed: %s (further errors are counted, not logged)",
                        self.name,
                        type(exc).__name__,
                    )

    def close(self, join_timeout: float = 1.0) -> None:
        """Stop feeding at once (queued chunks are discarded) and end the worker. Idempotent."""
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._queue.put_nowait(None)
        except (
            queue.Full
        ):  # pragma: no cover - the worker is the only consumer and the queue was just emptied
            pass
        if threading.current_thread() is not self._thread:
            self._thread.join(join_timeout)  # a hung feed() cannot hold shutdown: the thread is a daemon
