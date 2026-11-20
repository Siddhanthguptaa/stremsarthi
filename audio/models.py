"""Model file locations and verified downloads for the audio module.

Setup-time helpers only (blocking HTTP, never called on the audio hot path). Every download is
verified before it is moved into place: the Silero VAD model by a pinned SHA-256, and Hugging Face
files by the SHA-256 (LFS files) or git blob id (small files) that the Hub API reports for the
pinned commit. Nothing downloaded is ever executed.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Silero VAD (MIT, snakers4/silero-vad). Tag v6.2.3, file src/silero_vad/data/silero_vad.onnx.
# The git blob id was cross-checked against the GitHub contents API when this pin was made.
SILERO_VAD_TAG = "v6.2.3"
SILERO_VAD_URL = f"https://raw.githubusercontent.com/snakers4/silero-vad/{SILERO_VAD_TAG}/src/silero_vad/data/silero_vad.onnx"
SILERO_VAD_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
SILERO_VAD_SIZE = 2_327_524

# faster-whisper models converted for CTranslate2 (MIT, Systran). Used by audio.stt.local.
WHISPER_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
}
WHISPER_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")


class ModelIntegrityError(RuntimeError):
    """A downloaded or cached model file did not match its pinned hash."""


def weights_dir() -> Path:
    """Directory for downloaded audio weights (gitignored). Override with ``SS_AUDIO_WEIGHTS``."""
    override = os.environ.get("SS_AUDIO_WEIGHTS")
    return Path(override) if override else REPO_ROOT / "models" / "weights" / "audio"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git_blob_sha1(path: Path) -> str:
    size = path.stat().st_size
    digest = hashlib.sha1(f"blob {size}\0".encode())  # noqa: S324 - git object id, not security
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def silero_vad_path(dest_dir: Path | None = None) -> Path:
    return (dest_dir or weights_dir()) / "silero_vad.onnx"


def _download(url: str, dest: Path, *, timeout: float = 60.0) -> None:
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=dest.name + ".", suffix=".part")
    tmp = Path(tmp_name)
    try:
        with (
            os.fdopen(fd, "wb") as out,
            httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as resp,
        ):
            resp.raise_for_status()
            for block in resp.iter_bytes(1 << 20):
                out.write(block)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def ensure_silero_vad(dest_dir: Path | None = None, *, download: bool = True) -> Path:
    """Return the path of the verified Silero VAD ONNX model, downloading it when absent."""
    path = silero_vad_path(dest_dir)
    if path.exists():
        if sha256_file(path) != SILERO_VAD_SHA256:
            raise ModelIntegrityError(f"{path} does not match the pinned SHA-256")
        return path
    if not download:
        raise FileNotFoundError(f"{path} is missing (run audio.models.ensure_silero_vad())")
    part = path.with_name(path.name + ".dl")
    try:
        _download(SILERO_VAD_URL, part)
        if sha256_file(part) != SILERO_VAD_SHA256:
            raise ModelIntegrityError("downloaded Silero VAD model does not match the pinned SHA-256")
        part.replace(path)
    finally:
        part.unlink(missing_ok=True)
    return path


def fetch_hf_model(
    repo_id: str,
    dest_dir: Path,
    files: Sequence[str] = WHISPER_FILES,
    *,
    revision: str = "main",
) -> tuple[Path, str]:
    """Download ``files`` of a Hugging Face model repo into ``dest_dir`` and verify each one.

    The Hub API is asked for the commit id and per-file hashes of ``revision``; the files are then
    fetched from that exact commit. Returns ``(dest_dir, commit_sha)``.
    """
    import httpx

    api = f"https://huggingface.co/api/models/{repo_id}/revision/{revision}?blobs=true"
    meta = httpx.get(api, timeout=30.0, follow_redirects=True)
    meta.raise_for_status()
    info = meta.json()
    commit = str(info["sha"])
    siblings = {s["rfilename"]: s for s in info.get("siblings", [])}
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name in files:
        entry = siblings.get(name)
        if entry is None:
            raise FileNotFoundError(f"{repo_id}@{commit[:8]} has no file {name}")
        target = dest_dir / name
        lfs = entry.get("lfs") or {}
        if not target.exists():
            _download(f"https://huggingface.co/{repo_id}/resolve/{commit}/{name}", target, timeout=300.0)
        if lfs.get("sha256"):
            ok = sha256_file(target) == lfs["sha256"]
        else:
            ok = git_blob_sha1(target) == entry.get("blobId")
        if not ok:
            target.unlink(missing_ok=True)
            raise ModelIntegrityError(f"{repo_id}/{name} failed hash verification")
    (dest_dir / "COMMIT").write_text(commit + "\n")
    return dest_dir, commit
