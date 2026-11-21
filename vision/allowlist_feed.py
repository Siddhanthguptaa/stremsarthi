"""Bus feed for the face allowlist: ``Topic.ALLOWLIST`` (``AllowlistUpdate``) -> ``VisionResources.allowlist``.

The API publishes ``control.allowlist`` (enrolment, removal, resync snapshots); the engine bridges it onto its
``LocalBus`` and hands that same bus to ``vision.host:build(config, bus)``. Nothing used to read it on the vision
side, so the faces detector's allowlist stayed empty and every face was masked (``face_no_allowlist``).

Semantics (``contracts.AllowlistUpdate``): ``snapshot`` empties the list (its ``add`` updates, at the same version,
follow), ``add`` upserts one ``face_id`` -> embedding, ``remove`` deletes one. After every accepted change the
full list is pushed to ``allowlist.update(...)``, which bumps ``allowlist.version``; the faces detector then drops
every track vote, so a removed face is masked at once and a new one is verified from scratch.

Ordering: ``applied_version`` is the highest ``AllowlistUpdate.version`` applied (0 = nothing / reset). An ``add`` or
``remove`` older than it is dropped (a replayed ``add`` must not bring a removed face back); a ``snapshot`` is
applied only when it is not older than ``applied_version`` (equal is the same state again). The engine reports
``applied_version`` so the API can resync when it differs.

Privacy and fail-closed: embeddings live only in RAM (float32, L2-normalised copies); nothing here logs them, only
``op``, ``version`` and the entry count. A malformed update (wrong dimension, non-finite, zero norm, not an
``AllowlistUpdate``), an ``add`` beyond ``MAX_ENTRIES`` or a stale update is ignored: it never adds an entry, so an
unknown face stays masked. If the feed cannot subscribe, its thread dies, or the bus reports dropped events, the
list is CLEARED (every face masked) and ``applied_version`` goes to 0 so the API resyncs; after a drop only a
``snapshot`` is accepted (a stale ``add`` still queued must not re-enable a removed face).
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from contracts import AllowlistUpdate, Topic

from .bus_feed import BusFeed

log = logging.getLogger("vision.allowlist")

EMBEDDING_DIM = 128  # SFace; same constant as ``vision.detectors.faces.core``
MAX_ENTRIES = 64  # every unique face_id costs an O(n) rebuild of the detector matrix


class AllowlistFeed(BusFeed):
    """Applies allowlist updates from a bus subscription to ``resources.allowlist`` on a daemon thread."""

    topic = Topic.ALLOWLIST
    name = "vision-allowlist"

    def __init__(self, bus: Any, allowlist: Any) -> None:
        super().__init__(bus, log)
        self._allowlist = allowlist
        self._entries: dict[str, np.ndarray] = {}
        self.applied_version = 0
        self._need_snapshot = False
        self._cap_logged = False
        self.applied = 0
        self.ignored = 0

    def handle(self, event: Any) -> None:
        self.apply(event)

    # ------------------------------------------------------------------ fail closed
    def _clear(self) -> None:
        self._entries = {}
        self.applied_version = 0
        self._allowlist.update(())

    def on_failure(self) -> None:
        log.error("allowlist feed unavailable: list cleared, every face is masked")
        self._clear()

    def on_drop(self) -> None:
        self._need_snapshot = True
        self._clear()

    # ------------------------------------------------------------------ update
    def apply(self, event: Any) -> bool:
        """Apply one update; True when the allowlist changed. Never raises, never logs an embedding."""
        try:
            changed = self._apply(event)
        except Exception:  # noqa: BLE001 - fail closed: keep the list as it was
            log.warning("allowlist update ignored (error)", exc_info=False)
            self.ignored += 1
            return False
        if changed:
            self._allowlist.update([(fid, vec) for fid, vec in self._entries.items()])
            self.applied += 1
            log.info(
                "allowlist op=%s version=%s count=%d",
                getattr(event, "op", "?"),
                getattr(event, "version", "?"),
                len(self._entries),
            )
        return changed

    def _apply(self, event: Any) -> bool:
        if not isinstance(event, AllowlistUpdate):
            self.ignored += 1
            return False
        if event.op == "snapshot":
            if event.version < self.applied_version:
                self.ignored += 1  # an old snapshot must not roll the list back
                return False
            self._entries = {}
            self.applied_version = event.version
            self._need_snapshot = False
            self._cap_logged = False
            return True
        if self._need_snapshot or event.version < self.applied_version:
            self.ignored += 1  # stale (replayed / out of order), or waiting for the resync snapshot
            return False
        assert event.face_id is not None
        if event.op == "remove":
            self.applied_version = max(self.applied_version, event.version)
            return self._entries.pop(event.face_id, None) is not None
        vec = np.asarray(event.embedding, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vec))
        if vec.size != EMBEDDING_DIM or not np.all(np.isfinite(vec)) or not norm > 1e-6:
            log.warning("allowlist add ignored: malformed embedding (version=%s)", event.version)
            self.ignored += 1
            return False
        if event.face_id not in self._entries and len(self._entries) >= MAX_ENTRIES:
            if not self._cap_logged:
                log.error("allowlist full (%d entries): further adds ignored", len(self._entries))
                self._cap_logged = True
            self.ignored += 1
            return False
        self._entries[event.face_id] = (vec / norm).astype(np.float32)
        self.applied_version = max(self.applied_version, event.version)
        return True
