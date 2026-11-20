"""AllowlistStore: RAM only by default, TTL, face cap, opt-in encrypted persistence (mode 0600)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from app.api.allowlist import AllowlistFull, AllowlistStore, fernet_for

EMB = (0.6, 0.8)


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def make(clock: Clock, **kw) -> AllowlistStore:
    return AllowlistStore(ttl_s=kw.pop("ttl_s", 100), max_faces=kw.pop("max_faces", 3), clock=clock, **kw)


def test_ttl_expiry_removes_the_face_and_reports_it():
    clock = Clock()
    store = make(clock)
    face = store.add("f1", "Asha", EMB)
    assert face.expires_at == clock.t + 100
    assert [m.face_id for m in store.metas()] == ["f1"]
    clock.t += 99
    assert "f1" in store and store.purge_expired() == []
    clock.t += 2
    assert "f1" not in store
    assert store.faces() == [] and store.metas() == []  # hidden even before the sweep
    assert store.purge_expired() == ["f1"]
    assert len(store) == 0


def test_version_bumps_on_add_remove_and_expiry():
    clock = Clock()
    store = make(clock)
    assert store.version == 0
    store.add("a", "A", EMB)
    store.add("b", "B", EMB)
    assert store.version == 2
    assert store.remove("a") and store.version == 3
    assert not store.remove("a") and store.version == 3
    clock.t += 1000
    assert store.purge_expired() == ["b"] and store.version == 4


def test_max_faces_is_enforced_and_replacing_the_same_id_is_not_a_new_face():
    clock = Clock()
    store = make(clock, max_faces=2)
    store.add("a", "A", EMB)
    store.add("b", "B", EMB)
    assert not store.has_room()
    with pytest.raises(AllowlistFull):
        store.add("c", "C", EMB)
    store.add("a", "A again", EMB)  # same id: replaces
    assert len(store) == 2
    store.remove("b")
    store.add("c", "C", EMB)
    assert {f.face_id for f in store.faces()} == {"a", "c"}


def test_expired_faces_free_room():
    clock = Clock()
    store = make(clock, max_faces=1)
    store.add("a", "A", EMB)
    clock.t += 500
    store.add("b", "B", EMB)  # "a" expired, so there is room
    assert [f.face_id for f in store.faces()] == ["b"]


def test_persistence_is_off_by_default_and_writes_nothing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    store = make(Clock())
    store.add("a", "A", EMB)
    assert not store.persistent
    assert list(tmp_path.rglob("*")) == []


def test_persist_requested_without_a_key_stays_in_ram(tmp_path: Path):
    path = tmp_path / "allowlist.enc"
    store = make(Clock(), persist_path=path, key=None)
    store.add("a", "A", EMB)
    assert not store.persistent and store.persist_error == "missing_key"
    assert not path.exists()


def test_persisted_file_is_0600_encrypted_and_reloads(tmp_path: Path):
    path = tmp_path / "state" / "allowlist.enc"
    clock = Clock()
    store = make(clock, persist_path=path, key="correct horse battery staple")
    face = store.add("f1", "Distinct-Label-Zed", (0.123456789, 0.5))
    assert store.persistent and face.expires_at is None  # a persisted face does not expire
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    raw = path.read_bytes()
    assert b"Distinct-Label-Zed" not in raw and b"0.123456789" not in raw and b"embedding" not in raw
    assert not list(path.parent.glob("*.tmp"))

    clock.t += 10_000_000  # no expiry
    reopened = make(clock, persist_path=path, key="correct horse battery staple")
    assert [(f.face_id, f.label, f.embedding) for f in reopened.faces()] == [
        ("f1", "Distinct-Label-Zed", (0.123456789, 0.5))
    ]
    assert reopened.version >= 1  # a fresh engine (version 0) will be sent it


def test_tight_mode_is_restored_if_the_file_was_world_readable(tmp_path: Path):
    path = tmp_path / "allowlist.enc"
    store = make(Clock(), persist_path=path, key="k")
    store.add("a", "A", EMB)
    os.chmod(path, 0o644)
    store.add("b", "B", EMB)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_file_is_not_readable_without_the_key_and_is_never_overwritten(tmp_path: Path):
    path = tmp_path / "allowlist.enc"
    make(Clock(), persist_path=path, key="right-key").add("a", "A", EMB)
    before = path.read_bytes()
    wrong = make(Clock(), persist_path=path, key="wrong-key")
    assert wrong.faces() == [] and wrong.persist_error == "unreadable" and not wrong.persistent
    wrong.add("x", "X", EMB)  # RAM only: must not clobber the old file
    assert path.read_bytes() == before


def test_removing_a_face_updates_the_file_and_clear_deletes_it(tmp_path: Path):
    path = tmp_path / "allowlist.enc"
    store = make(Clock(), persist_path=path, key="k")
    store.add("a", "A", EMB)
    store.add("b", "B", EMB)
    store.remove("a")
    assert [f.face_id for f in make(Clock(), persist_path=path, key="k").faces()] == ["b"]
    store.clear()
    assert not path.exists() and len(store) == 0


def test_passphrase_and_fernet_key_both_work():
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    assert fernet_for(key).decrypt(fernet_for(key).encrypt(b"x")) == b"x"
    assert fernet_for("any passphrase").decrypt(fernet_for("any passphrase").encrypt(b"y")) == b"y"


def test_repr_never_shows_the_embedding_or_label():
    store = make(Clock())
    face = store.add("f1", "Secret-Name", (0.111111, 0.222222))
    text = repr(face) + repr(store.metas()[0])
    assert "Secret-Name" not in text and "0.111111" not in text
