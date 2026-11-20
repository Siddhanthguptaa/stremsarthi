"""models/manifest.json consistency and models/prepare.py download/verify/extract (no network)."""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
import zipfile
from pathlib import Path

import pytest

from models import prepare

MODELS = Path(prepare.__file__).parent


def test_manifest_entries_are_pinned_and_licensed() -> None:
    m = prepare.load_manifest()
    assert m["artifacts"]
    for key, a in m["artifacts"].items():
        assert a["id"] == key
        assert re.fullmatch(r"[0-9a-f]{64}", a["sha256"]), key
        assert a["url"].startswith("https://"), key
        assert a["license"] in {"MIT", "Apache-2.0"}, key
        assert a["license_source"].startswith("https://") and a["source"].startswith("https://"), key
        assert (MODELS / a["license_file"]).is_file(), key
        assert a["bytes"] > 0 and a["task"] in {"det", "rec", "face_det", "face_rec"}
        if key.startswith(("yunet", "sface")):
            assert "/raw/" in a["url"] and re.search(r"/[0-9a-f]{40}/", a["url"]), "must pin a commit"


def test_manifest_has_required_models_and_profiles() -> None:
    m = prepare.load_manifest()
    need = {
        "yunet-2023mar",
        "sface-2021dec",
        "ppocr-v5-mobile",
        "ppocr-v5-english-rec",
        "ppocr-v5-devanagari-rec",
        "ppocr-v5-tamil-rec",
        "ppocr-v5-telugu-rec",
    }
    assert need <= set(m["artifacts"])
    for profile in ("dev", "demo"):
        names = prepare.profile_names(profile, m)
        assert need <= set(names) and set(names) <= set(m["artifacts"])
    with pytest.raises(KeyError):
        prepare.profile_names("nope", m)


def _manifest_for(tmp: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes, **extra: object) -> dict:
    art = {
        "filename": "m.onnx",
        "url": "https://example.invalid/m.onnx",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        **extra,
    }
    manifest = {"artifacts": {"m": art}, "profiles": {"dev": ["m"]}}
    monkeypatch.setattr(prepare, "load_manifest", lambda: manifest)
    return manifest


def test_download_verifies_hash_and_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"weights" * 100
    _manifest_for(tmp_path, monkeypatch, payload)
    calls = []
    monkeypatch.setattr(prepare, "_fetch", lambda url, dest: (calls.append(url), dest.write_bytes(payload)))
    path = prepare.download("m", tmp_path)
    assert path.read_bytes() == payload and len(calls) == 1
    prepare.download("m", tmp_path)  # exists + verified: no second fetch
    assert len(calls) == 1
    assert prepare.verified_path("m", tmp_path)[0] == path
    assert [p.name for p in tmp_path.iterdir()] == ["m.onnx"]  # no temp litter


def test_download_rejects_tampered_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _manifest_for(tmp_path, monkeypatch, b"good")
    monkeypatch.setattr(prepare, "_fetch", lambda url, dest: dest.write_bytes(b"evil"))
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        prepare.download("m", tmp_path)
    assert list(tmp_path.iterdir()) == []  # nothing left behind


def test_existing_corrupt_file_is_not_trusted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _manifest_for(tmp_path, monkeypatch, b"good")
    (tmp_path / "m.onnx").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        prepare.verified_path("m", tmp_path)
    with pytest.raises(FileNotFoundError):
        prepare.verified_path("m", tmp_path / "empty")


def test_fetch_refuses_non_https(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="non-https"):
        prepare._fetch("http://example.invalid/x", tmp_path / "x")
    with pytest.raises(ValueError, match="non-https"):
        prepare._fetch("file:///etc/passwd", tmp_path / "x")


def test_archive_member_extracted_in_fresh_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"inner-model"
    zpath = tmp_path / "src.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("pkg/inner.onnx", payload)
        zf.writestr("pkg/other.txt", b"x")
    _manifest_for(tmp_path, monkeypatch, payload, archive={"format": "zip", "member": "pkg/inner.onnx"})
    cache = tmp_path / "cache"
    monkeypatch.setattr(prepare, "_fetch", lambda url, dest: dest.write_bytes(zpath.read_bytes()))
    path = prepare.download("m", cache)
    assert path.read_bytes() == payload
    assert [p.name for p in cache.iterdir()] == ["m.onnx"]


def test_extract_refuses_traversal_and_bad_format(tmp_path: Path) -> None:
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escape.txt", b"x")
        zf.writestr("ok.txt", b"y")
    with pytest.raises(ValueError, match="unsafe"):
        prepare.extract_member(evil, "ok.txt", "zip", tmp_path / "out.bin")
    with pytest.raises(ValueError, match="unsafe"):
        prepare.extract_member(evil, "../escape.txt", "zip", tmp_path / "out.bin")
    with pytest.raises(ValueError, match="unsupported"):
        prepare.extract_member(evil, "ok.txt", "rar", tmp_path / "out.bin")
    tpath = tmp_path / "a.tar.gz"
    src = tmp_path / "f.bin"
    src.write_bytes(b"tar-member")
    with tarfile.open(tpath, "w:gz") as tf:
        tf.add(src, arcname="d/f.bin")
    prepare.extract_member(tpath, "d/f.bin", "tar.gz", tmp_path / "out2.bin")
    assert (tmp_path / "out2.bin").read_bytes() == b"tar-member"
    assert not (tmp_path / "escape.txt").exists()


def test_manifest_is_valid_json_with_registry_pin() -> None:
    data = json.loads((MODELS / "manifest.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"[0-9a-f]{64}", data["registry"]["sha256"])
