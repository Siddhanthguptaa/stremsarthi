"""Face allowlist store: RAM only by default, with a TTL, a face cap and opt-in encrypted persistence.

Embeddings are biometric-derived data (DPDP Act), so:

* the default is RAM only: a face expires after ``enrol.ttl_s`` (12 h) and nothing touches the disk;
* persistence is opt-in (``enrol.persist``) and needs ``ALLOWLIST_KEY`` from the environment. Without
  the key nothing is written. With it the whole list is one Fernet token (authenticated encryption)
  in a file created with mode 0600 inside a 0700 directory. A persisted face does not expire;
* embeddings and labels are never logged, and ``EnrolledFaceMeta`` (what the REST layer returns) has
  no embedding.

``version`` increases by one on every change (add, remove, expiry). The engine reports the last version
it applied in ``engine.status.allowlist_version``; a difference makes the API resend a snapshot.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from contracts import EnrolledFaceMeta

log = logging.getLogger("streamsarthi.api.allowlist")

FILE_MODE = 0o600
DIR_MODE = 0o700
_KDF_SALT = b"streamsarthi-allowlist-v1"


class AllowlistFull(Exception):
    """The configured maximum number of enrolled faces is reached."""


@dataclass(frozen=True, slots=True)
class StoredFace:
    face_id: str
    label: str  # free text (a name): never logged
    embedding: tuple[float, ...]  # biometric-derived: never logged, never returned over REST
    created_at: float  # epoch seconds
    expires_at: float | None  # None: does not expire (persisted)

    def __repr__(self) -> str:
        return f"StoredFace(face_id={self.face_id!r})"


def iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat(timespec="seconds")


def meta_of(face: StoredFace) -> EnrolledFaceMeta:
    return EnrolledFaceMeta(
        face_id=face.face_id,
        label=face.label,
        created_at=iso(face.created_at) or "",
        expires_at=iso(face.expires_at),
    )


def fernet_for(key: str) -> Fernet:
    """``ALLOWLIST_KEY`` is a Fernet key, or any passphrase (stretched with scrypt)."""
    raw = key.encode("utf-8")
    try:
        return Fernet(raw)
    except ValueError:
        derived = hashlib.scrypt(raw, salt=_KDF_SALT, n=2**14, r=8, p=1, dklen=32)
        return Fernet(base64.urlsafe_b64encode(derived))


class AllowlistStore:
    def __init__(
        self,
        *,
        ttl_s: int,
        max_faces: int,
        persist_path: str | os.PathLike[str] | None = None,
        key: str | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.ttl_s = ttl_s
        self.max_faces = max_faces
        self._clock = clock
        self._faces: dict[str, StoredFace] = {}
        self.version = 0
        self.last_change = float("-inf")  # clock() of the last change
        self._path: Path | None = None
        self._fernet: Fernet | None = None
        self.persist_error: str | None = None
        if persist_path is not None:
            if not key:
                # Opt-in persistence without a key must never fall back to plaintext.
                self.persist_error = "missing_key"
                log.warning("allowlist persistence requested but ALLOWLIST_KEY is not set: RAM only")
            else:
                self._path = Path(persist_path).expanduser()
                self._fernet = fernet_for(key)
                self._load()

    # --- state -----------------------------------------------------------------------------
    @property
    def persistent(self) -> bool:
        return self._path is not None

    def __len__(self) -> int:
        return len(self._faces)

    def __contains__(self, face_id: str) -> bool:
        face = self._faces.get(face_id)
        return face is not None and not self._expired(face, self._clock())

    def _expired(self, face: StoredFace, now: float) -> bool:
        return face.expires_at is not None and face.expires_at <= now

    def _bump(self) -> None:
        self.version += 1
        self.last_change = self._clock()

    # --- reads -----------------------------------------------------------------------------
    def faces(self) -> list[StoredFace]:
        """Live (non-expired) faces, oldest first."""
        now = self._clock()
        return sorted(
            (f for f in self._faces.values() if not self._expired(f, now)), key=lambda f: f.created_at
        )

    def metas(self) -> list[EnrolledFaceMeta]:
        return [meta_of(f) for f in self.faces()]

    def snapshot(self) -> tuple[int, list[StoredFace]]:
        return self.version, self.faces()

    # --- writes ----------------------------------------------------------------------------
    def has_room(self, face_id: str | None = None) -> bool:
        live = {f.face_id for f in self.faces()}
        return len(live) < self.max_faces or (face_id is not None and face_id in live)

    def add(self, face_id: str, label: str, embedding: tuple[float, ...]) -> StoredFace:
        """Add (or replace the same ``face_id``). Raises ``AllowlistFull`` at the cap."""
        self.purge_expired()
        if face_id not in self._faces and len(self._faces) >= self.max_faces:
            raise AllowlistFull
        now = self._clock()
        face = StoredFace(
            face_id=face_id,
            label=label,
            embedding=tuple(float(x) for x in embedding),
            created_at=now,
            expires_at=None if self.persistent else now + self.ttl_s,
        )
        self._faces[face_id] = face
        self._bump()
        self._save()
        return face

    def remove(self, face_id: str) -> bool:
        if self._faces.pop(face_id, None) is None:
            return False
        self._bump()
        self._save()
        return True

    def purge_expired(self) -> list[str]:
        """Drop expired faces. Returns their ids (the caller publishes ``remove`` for each)."""
        now = self._clock()
        gone = [fid for fid, f in self._faces.items() if self._expired(f, now)]
        for fid in gone:
            del self._faces[fid]
            self._bump()
        if gone:
            self._save()
        return gone

    def clear(self) -> list[str]:
        """Forget every face and delete the persisted file (used by the delete-session-data action)."""
        gone = list(self._faces)
        self._faces.clear()
        if gone:
            self._bump()
        self.delete_file()
        return gone

    def delete_file(self) -> None:
        if self._path is not None:
            with contextlib.suppress(OSError):
                self._path.unlink()

    # --- persistence -----------------------------------------------------------------------
    def _save(self) -> None:
        if self._path is None or self._fernet is None:
            return
        payload = json.dumps(
            {
                "v": 1,
                "faces": [
                    {
                        "face_id": f.face_id,
                        "label": f.label,
                        "embedding": list(f.embedding),
                        "created_at": f.created_at,
                    }
                    for f in self._faces.values()
                ],
            },
            separators=(",", ":"),
        ).encode("utf-8")
        token = self._fernet.encrypt(payload)
        tmp = self._path.with_name(self._path.name + ".tmp")
        try:
            self._path.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
            try:
                os.fchmod(fd, FILE_MODE)  # also when the file existed with looser permissions
                with os.fdopen(fd, "wb") as fh:
                    fh.write(token)
                    fh.flush()
                    os.fsync(fh.fileno())
            except BaseException:
                with contextlib.suppress(OSError):
                    os.close(fd)
                raise
            os.replace(tmp, self._path)
        except OSError as exc:  # disk full, read-only: keep serving from RAM
            log.warning("allowlist could not be saved (%s)", type(exc).__name__)
            with contextlib.suppress(OSError):
                tmp.unlink()

    def _load(self) -> None:
        assert self._path is not None and self._fernet is not None
        try:
            token = self._path.read_bytes()
        except FileNotFoundError:
            return
        except OSError as exc:
            log.warning("allowlist file could not be read (%s)", type(exc).__name__)
            return
        try:
            data = json.loads(self._fernet.decrypt(token))
            faces = [
                StoredFace(
                    face_id=str(item["face_id"]),
                    label=str(item["label"]),
                    embedding=tuple(float(x) for x in item["embedding"]),
                    created_at=float(item["created_at"]),
                    expires_at=None,
                )
                for item in data["faces"]
            ]
        except (InvalidToken, ValueError, KeyError, TypeError):
            # Wrong key or damaged file: keep it untouched (never overwrite it) and run RAM only.
            self.persist_error = "unreadable"
            self._path = None
            self._fernet = None
            log.warning("allowlist file could not be decrypted with ALLOWLIST_KEY: ignored, RAM only")
            return
        for face in faces[: self.max_faces]:
            self._faces[face.face_id] = face
        if self._faces:
            self._bump()  # version >= 1 so a fresh engine (version 0) receives them
