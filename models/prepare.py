#!/usr/bin/env python3
"""Download the reviewed, hash-pinned vision models. Inference stays offline.

Adapted from IRL Shield ``models/prepare.py`` (MIT, Copyright (c) 2026 Elan Nash; see
``models/licenses/IRL-Shield-MIT.txt``). Changes: ``--profile`` selection, ``--list``, https-only
URLs with retries, size check, archive members extracted into a fresh empty directory with a
path-traversal guard, static-graph derivation only for profile runs, licence receipts per artifact.

    python models/prepare.py --profile dev          # download + verify the dev set, derive static graphs
    python models/prepare.py --verify-only          # hash-check what is on disk, no network
    python models/prepare.py ppocr-v6-tiny          # one artifact

Weights go to ``models/weights/`` (gitignored). A file that exists is re-hashed, never trusted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DEFAULT_CACHE = ROOT / "weights"
USER_AGENT = "streamsarthi-model-prepare/1"
RETRIES = 3


def load_manifest() -> dict[str, Any]:
    return json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def profile_names(profile: str, manifest: dict[str, Any] | None = None) -> list[str]:
    profiles = (manifest or load_manifest())["profiles"]
    if profile not in profiles:
        raise KeyError(f"unknown profile {profile!r}; choose one of {sorted(profiles)}")
    return list(profiles[profile])


def artifact_path(name: str, cache_dir: str | os.PathLike[str] = DEFAULT_CACHE) -> Path:
    artifact = load_manifest()["artifacts"][name]
    return Path(cache_dir).expanduser().resolve() / artifact["filename"]


def verified_path(
    name: str, cache_dir: str | os.PathLike[str] = DEFAULT_CACHE
) -> tuple[Path, dict[str, Any]]:
    """Return ``(path, manifest entry)`` after re-hashing the file against the pinned SHA-256."""
    artifact = load_manifest()["artifacts"][name]
    path = artifact_path(name, cache_dir)
    if not path.is_file():
        raise FileNotFoundError(f"Missing {name}: {path}. Run python models/prepare.py {name} first.")
    actual = sha256_file(path)
    if actual != artifact["sha256"]:
        raise ValueError(f"SHA-256 mismatch for {path}: {actual}")
    return path, artifact


def _fetch(url: str, destination: Path) -> None:
    if urlparse(url).scheme != "https":
        raise ValueError(f"refusing non-https model URL: {url}")
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as out:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    out.write(chunk)
            return
        except OSError as exc:  # URLError and timeouts are OSErrors
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"download failed after {RETRIES} attempts: {url}: {last}")


def extract_member(archive: Path, member: str, fmt: str, destination: Path) -> None:
    """Extract one member of a zip/tar.gz into ``destination``.

    The archive is unpacked into a brand-new empty directory (never into the cache), members with
    absolute or ``..`` paths are refused, and only the requested member is copied out.
    """
    if fmt not in {"zip", "tar.gz"}:
        raise ValueError(f"unsupported archive format {fmt!r}")
    member_path = Path(member)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise ValueError(f"unsafe archive member path {member!r}")
    scratch = Path(tempfile.mkdtemp(prefix="extract-", dir=destination.parent))
    try:
        if fmt == "zip":
            with zipfile.ZipFile(archive) as zf:
                _check_names(zf.namelist())
                zf.extract(member, scratch)
        else:
            with tarfile.open(archive, "r:gz") as tf:
                _check_names(tf.getnames())
                info = tf.getmember(member)
                if not info.isfile():
                    raise ValueError(f"archive member {member!r} is not a regular file")
                tf.extract(info, scratch, filter="data")
        shutil.move(str(scratch / member), destination)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _check_names(names: list[str]) -> None:
    for n in names:
        p = Path(n)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(f"unsafe archive entry {n!r}")


def download(name: str, cache_dir: str | os.PathLike[str] = DEFAULT_CACHE) -> Path:
    artifact = load_manifest()["artifacts"][name]
    cache = Path(cache_dir).expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / artifact["filename"]
    if destination.exists():
        verified_path(name, cache)
        return destination

    # A unique temporary name allows separate setup processes to share the cache.
    handle, temporary = tempfile.mkstemp(prefix=artifact["filename"] + ".", dir=cache)
    os.close(handle)
    tmp = Path(temporary)
    try:
        spec = artifact.get("archive")
        if spec:
            archive_tmp = Path(tempfile.mkdtemp(prefix="dl-", dir=cache)) / "archive"
            try:
                _fetch(artifact["url"], archive_tmp)
                if spec.get("sha256") and sha256_file(archive_tmp) != spec["sha256"]:
                    raise ValueError(f"SHA-256 mismatch for archive of {name}")
                tmp.unlink(missing_ok=True)
                extract_member(archive_tmp, spec["member"], spec["format"], tmp)
            finally:
                shutil.rmtree(archive_tmp.parent, ignore_errors=True)
        else:
            _fetch(artifact["url"], tmp)
        actual = sha256_file(tmp)
        if actual != artifact["sha256"]:
            raise ValueError(f"SHA-256 mismatch downloading {name}: {actual}")
        if "bytes" in artifact and tmp.stat().st_size != artifact["bytes"]:
            raise ValueError(f"size mismatch downloading {name}: {tmp.stat().st_size} != {artifact['bytes']}")
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)
    return destination


def derive_static(name: str, cache_dir: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Derive the fixed-shape detector graphs listed under ``static_shapes`` for a det artifact."""
    artifact = load_manifest()["artifacts"][name]
    shapes = artifact.get("static_shapes") or []
    if not shapes:
        return []
    if str(ROOT.parent) not in sys.path:
        sys.path.insert(0, str(ROOT.parent))
    from vision.ort_static import prepare_static

    source, _ = verified_path(name, cache_dir)
    records = []
    for height, width in shapes:
        path, record = prepare_static(source, artifact["sha256"], height, width, cache_dir)
        records.append(
            {"path": str(path), "height": height, "width": width, "sha256": record["derived_sha256"]}
        )
    return records


def main(argv: list[str] | None = None) -> int:
    manifest = load_manifest()
    artifacts = manifest["artifacts"]
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("names", nargs="*", help="Artifact ids (default: the profile set)")
    parser.add_argument("--profile", choices=sorted(manifest["profiles"]), help="Config profile to prepare")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--verify-only", action="store_true", help="Hash-check local files, no network")
    parser.add_argument("--list", action="store_true", help="List artifacts and licences, then exit")
    parser.add_argument("--no-static", action="store_true", help="Skip static detector graph derivation")
    args = parser.parse_args(argv)

    if args.list:
        for key, a in artifacts.items():
            print(f"{key:28s} {a['task']:9s} {a['license']:11s} {a.get('bytes', 0):>10d} B  {a['filename']}")
        return 0
    if args.names:
        names = args.names
    else:
        names = profile_names(args.profile or "dev", manifest)
    unknown = set(names) - artifacts.keys()
    if unknown:
        parser.error(f"Unknown artifacts: {', '.join(sorted(unknown))}")
    for name in names:
        if args.verify_only:
            path, artifact = verified_path(name, args.cache_dir)
        else:
            path = download(name, args.cache_dir)
            artifact = artifacts[name]
        print(
            json.dumps(
                {
                    "name": name,
                    "path": str(path),
                    "sha256": artifact["sha256"],
                    "bytes": path.stat().st_size,
                    "license": artifact["license"],
                }
            )
        )
    if not args.no_static and not args.verify_only:
        for name in names:
            for record in derive_static(name, args.cache_dir):
                print(json.dumps({"name": f"{name}-static", **record}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
