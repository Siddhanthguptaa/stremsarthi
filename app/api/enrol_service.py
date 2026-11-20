"""Enrolment plumbing: request parsing (memory only), enroller loading, allowlist publishing and resync.

Frames (JPEG, from the browser) are parsed from the request body straight into ``bytes``: no temp file,
no spooled upload, no thumbnail. They are decoded in a worker thread, handed to the ``FaceEnroller``
and dropped when the request ends. Only the enroller's embedding is kept, in ``AllowlistStore``.

The enroller is loaded from ``Config.enrol.impl`` (``module:Class``) with ``importlib`` at request
time, so ``app/api`` never imports ``vision`` at start-up and a missing module is a clean 503.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import importlib
import io
import json
import logging
import re
import time
import unicodedata
from collections.abc import Callable
from typing import Any

from contracts import AllowlistUpdate, FaceEnroller, Topic

from .allowlist import AllowlistStore, StoredFace
from .bus import BusClient, Subscription
from .errors import ApiError

log = logging.getLogger("streamsarthi.api.enrol")

MAX_ENROL_FRAMES = 12  # module constant; the minimum is ``Config.enrol.min_frames``
MAX_FRAME_BYTES = 1024 * 1024  # a 640 px JPEG is about 50 KB
MAX_FRAME_PIXELS = 4_200_000
MAX_PARTS = 40
MAX_LABEL_CHARS = 40
FRAME_FIELDS = frozenset({"frames", "frames[]", "frame", "image", "images"})
_IMPL_RE = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")


# --------------------------------------------------------------------------- request parsing
def clean_label(raw: object) -> str:
    if not isinstance(raw, str):
        raise ApiError(422, "invalid_label", "label is required")
    label = " ".join(raw.split())
    if (
        not label
        or len(label) > MAX_LABEL_CHARS
        or any(unicodedata.category(c).startswith("C") for c in label)
    ):
        raise ApiError(422, "invalid_label", f"label must be 1 to {MAX_LABEL_CHARS} printable characters")
    return label


def parse_multipart(content_type: str, body: bytes) -> tuple[str | None, list[bytes]]:
    """Read ``label`` and the frame parts of a multipart body, entirely in memory."""
    from python_multipart.multipart import MultipartParser, parse_options_header

    _, options = parse_options_header(content_type.encode("latin-1", "replace"))
    boundary = options.get(b"boundary")
    if not boundary:
        raise ApiError(422, "bad_multipart", "multipart boundary missing")

    label: bytearray | None = None
    frames: list[bytes] = []
    state: dict[str, Any] = {"parts": 0, "field": b"", "value": b"", "headers": {}, "kind": None, "buf": None}

    def on_part_begin() -> None:
        state["parts"] += 1
        if state["parts"] > MAX_PARTS:
            raise ApiError(422, "bad_multipart", "too many parts")
        state.update(field=b"", value=b"", headers={}, kind=None, buf=None)

    def on_header_field(data: bytes, start: int, end: int) -> None:
        state["field"] += data[start:end]

    def on_header_value(data: bytes, start: int, end: int) -> None:
        state["value"] += data[start:end]

    def on_header_end() -> None:
        state["headers"][state["field"].decode("latin-1").lower()] = state["value"]
        state["field"] = b""
        state["value"] = b""

    def on_headers_finished() -> None:
        disposition = state["headers"].get("content-disposition", b"")
        _, params = parse_options_header(disposition)
        name = params.get(b"name", b"").decode("utf-8", "replace")
        if name == "label":
            state["kind"], state["buf"] = "label", bytearray()
        elif name in FRAME_FIELDS:
            state["kind"], state["buf"] = "frame", bytearray()

    def on_part_data(data: bytes, start: int, end: int) -> None:
        buf = state["buf"]
        if buf is None:
            return
        buf += data[start:end]
        limit = 1024 if state["kind"] == "label" else MAX_FRAME_BYTES
        if len(buf) > limit:
            raise ApiError(
                422, "frame_too_large" if state["kind"] == "frame" else "invalid_label", "part too large"
            )

    def on_part_end() -> None:
        nonlocal label
        if state["kind"] == "label":
            label = state["buf"]
        elif state["kind"] == "frame":
            frames.append(bytes(state["buf"]))
        state["buf"] = None

    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": on_part_begin,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_headers_finished": on_headers_finished,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
        },
        max_size=len(body) + 1,
    )
    try:
        parser.write(body)
        parser.finalize()
    except ApiError:
        raise
    except Exception:
        raise ApiError(422, "bad_multipart", "could not read the multipart body") from None
    text = None if label is None else bytes(label).decode("utf-8", "replace")
    return text, frames


def parse_json_body(body: bytes) -> tuple[str | None, list[bytes]]:
    """``{"label": str, "frames": [<base64 JPEG>, ...]}``."""
    try:
        data = json.loads(body)
    except ValueError:
        raise ApiError(422, "bad_json", "body is not valid JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("frames"), list):
        raise ApiError(422, "bad_json", "expected {label, frames: [base64, ...]}")
    raw_frames = data["frames"]
    if len(raw_frames) > MAX_ENROL_FRAMES:
        raise ApiError(422, "too_many_frames", f"send at most {MAX_ENROL_FRAMES} frames")
    frames: list[bytes] = []
    for item in raw_frames:
        if not isinstance(item, str) or len(item) > MAX_FRAME_BYTES * 4 // 3 + 8:
            raise ApiError(422, "frame_too_large", "frame is not a base64 string within the size limit")
        if item.startswith("data:"):
            item = item.partition(",")[2]
        try:
            frames.append(base64.b64decode(item, validate=True))
        except (binascii.Error, ValueError):
            raise ApiError(422, "bad_frame", "frame is not valid base64") from None
    label = data.get("label")
    return (label if isinstance(label, str) else None), frames


def check_frames(frames: list[bytes], min_frames: int) -> None:
    if len(frames) < min_frames:
        raise ApiError(422, "too_few_frames", f"send at least {min_frames} frames")
    if len(frames) > MAX_ENROL_FRAMES:
        raise ApiError(422, "too_many_frames", f"send at most {MAX_ENROL_FRAMES} frames")
    for raw in frames:
        if not raw or len(raw) > MAX_FRAME_BYTES:
            raise ApiError(422, "frame_too_large", "empty frame or frame over the size limit")
        if raw[:3] != b"\xff\xd8\xff":
            raise ApiError(422, "bad_frame", "frames must be JPEG")


def decode_frames(frames: list[bytes]) -> list[Any]:
    """JPEG bytes -> BGR ``uint8`` arrays. Blocking: call from a worker thread."""
    import cv2
    import numpy as np
    from PIL import Image

    out: list[Any] = []
    for raw in frames:
        try:
            with Image.open(io.BytesIO(raw)) as im:  # header only: refuse decompression bombs
                width, height = im.size
                ok = im.format == "JPEG"
        except Exception:
            raise ApiError(422, "bad_frame", "frame is not a readable JPEG") from None
        if not ok or width * height > MAX_FRAME_PIXELS:
            raise ApiError(422, "bad_frame", "frame must be a JPEG of at most 4.2 megapixels")
        image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ApiError(422, "bad_frame", "frame is not a readable JPEG")
        out.append(image)
    return out


# --------------------------------------------------------------------------- enroller
def load_enroller(impl: str) -> FaceEnroller:
    """Import ``module:Class`` and build it. 503 ``enroller_unavailable`` if it cannot be loaded."""
    unavailable = ApiError(503, "enroller_unavailable", "face enrolment is not available yet")
    if not _IMPL_RE.match(impl):
        raise unavailable
    module_name, _, attr = impl.partition(":")
    try:
        module = importlib.import_module(module_name)
        target = getattr(module, attr)
        enroller = target() if isinstance(target, type) else target
    except Exception as exc:  # missing module, missing model file, bad constructor
        log.warning("enroller %s could not be loaded (%s)", module_name, type(exc).__name__)
        raise unavailable from None
    if not callable(getattr(enroller, "enrol", None)):
        raise unavailable
    return enroller  # type: ignore[no-any-return]


# --------------------------------------------------------------------------- allowlist + bus
class AllowlistService:
    """Keeps the engine's allowlist equal to ``AllowlistStore``: publishes every change, removes
    expired faces, and resends a snapshot when ``engine.status.allowlist_version`` differs."""

    def __init__(
        self,
        store: AllowlistStore,
        bus: BusClient,
        *,
        grace_s: float = 3.0,
        sweep_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.bus = bus
        self.grace_s = grace_s
        self.sweep_s = sweep_s
        self._clock = clock
        self._last_publish = float("-inf")
        self._last_resync = float("-inf")
        self.resyncs = 0

    # --- publishing ------------------------------------------------------------------------
    def _publish(self, update: AllowlistUpdate) -> None:
        self.bus.publish(Topic.ALLOWLIST, update)
        self._last_publish = self._clock()

    def publish_add(self, face: StoredFace) -> None:
        self._publish(
            AllowlistUpdate(
                op="add",
                face_id=face.face_id,
                label=face.label,
                embedding=face.embedding,
                version=self.store.version,
            )
        )

    def publish_remove(self, face_id: str) -> None:
        self._publish(
            AllowlistUpdate(
                op="remove", face_id=face_id, label=None, embedding=None, version=self.store.version
            )
        )

    def sweep(self) -> list[str]:
        """Remove expired faces and tell the engine. Safe to call often."""
        gone = self.store.purge_expired()
        for face_id in gone:
            with contextlib.suppress(RuntimeError, ValueError, TypeError):
                self.publish_remove(face_id)
        return gone

    def resync(self) -> int:
        """Full snapshot: ``snapshot`` then one ``add`` per face, all at the store's version."""
        version, faces = self.store.snapshot()
        self._publish(
            AllowlistUpdate(op="snapshot", face_id=None, label=None, embedding=None, version=version)
        )
        for face in faces:
            self._publish(
                AllowlistUpdate(
                    op="add",
                    face_id=face.face_id,
                    label=face.label,
                    embedding=face.embedding,
                    version=version,
                )
            )
        self._last_resync = self._clock()
        self.resyncs += 1
        return len(faces)

    def on_engine_status(self, allowlist_version: int) -> bool:
        """True when a snapshot was sent. Skipped right after a change (the status may predate it)
        and rate limited, so an engine that ignores snapshots cannot make us flood the bus."""
        if allowlist_version == self.store.version:
            return False
        now = self._clock()
        if now - self._last_publish < self.grace_s or now - self._last_resync < 5.0:
            return False
        try:
            self.resync()
        except (RuntimeError, ValueError, TypeError):
            log.warning("allowlist resync could not be published")
            return False
        return True

    # --- background loops ------------------------------------------------------------------
    def subscribe(self) -> Subscription:
        """Subscribe before the first status can arrive (call from start-up, then ``run``)."""
        return self.bus.subscribe([Topic.ENGINE_STATUS])

    async def _status_loop(self, sub: Subscription) -> None:
        try:
            async for _topic, status in sub:
                self.on_engine_status(status.allowlist_version)
        finally:
            sub.close()

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self.sweep_s)
            self.sweep()

    async def run(self, sub: Subscription) -> None:
        tasks = [asyncio.create_task(self._status_loop(sub)), asyncio.create_task(self._sweep_loop())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
