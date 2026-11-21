"""Keyless DEMO ENROLMENT: put the demo reel's 'ENROLLED' person on the face allowlist.

The reel scene 'Enrolled face + stranger' (eval/sender/demo_scenes.py, class DemoFaces) shows person P01 as
the enrolled face (NASA public-domain portraits, eval/data/generated/faces/src). Nothing is enrolled in a fresh
demo session, so that face would be blurred like the stranger. This script enrols P01 through the SAME endpoint
the web app uses (``POST /api/enrol``, streamer token), so the real ``FaceEnroller`` (SFace embeddings) runs and
the API publishes the ``AllowlistUpdate`` to the engine:

- the enrolment frames are rendered in memory from the P01 ``enroll`` portraits only (the dataset generator's
  ``render_enroll_frame``), JPEG-encoded, sent as base64 JSON and never written to disk;
- embeddings stay in the API's RAM allowlist (``enrol.persist`` is off by default, so no key and no file);
- the person is a public-domain NASA portrait; there is no private person and no consent to give. The label
  is fixed (``DEMO ENROLLED``), the stored face expires after ``enrol.ttl_s``;
- only HTTP statuses and the face id/label are printed. The token is read from the API log, never printed.

Usage (inside WSL, repo root on PYTHONPATH):  python scripts/demo_enrol.py
Env: DEMO_API (default http://127.0.0.1:8000), DEMO_API_LOG (default /tmp/ss_api.log).
"""

from __future__ import annotations

import base64
import os
import random
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # `eval.*` imports when run as a plain script
LABEL = "DEMO ENROLLED"
PERSON_ID = (
    "P01"  # the scene's enrolled person (demo_scenes.DemoFaces: sources_for(use="enroll", person_id="P01"))
)
N_FRAMES = 8  # API accepts 5..12
SEED = 2024


def read_token(log: Path) -> str | None:
    m = re.search(r"#token=([A-Za-z0-9_.\-]+)", log.read_text(errors="ignore"))
    return m.group(1) if m else None


def build_frames(cache: Path | None = None, n: int = N_FRAMES, seed: int = SEED) -> list[bytes]:
    """Render ``n`` webcam-style enrolment frames of P01 (RAM only) and return them as JPEG bytes."""
    import cv2

    from eval.data_gen.video import faces as faces_mod

    cache_dir = str(cache or (ROOT / "eval" / "data" / "generated" / "faces" / "src"))
    sources = faces_mod.sources_for(use="enroll", person_id=PERSON_ID)
    if not sources:
        raise RuntimeError(f"no enrolment portraits for {PERSON_ID}")
    frames: list[bytes] = []
    for k in range(n):
        rng = random.Random(f"{seed}:{k}")
        img_rgb, _quad = faces_mod.render_enroll_frame(rng, cache_dir, sources[k % len(sources)])
        ok, buf = cv2.imencode(".jpg", img_rgb[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise RuntimeError("jpeg encode failed")
        frames.append(buf.tobytes())
    return frames


def enrol_demo(
    client: httpx.Client,
    make_frames: Callable[[], list[bytes]] = build_frames,
    log: Callable[[str], None] = lambda s: print(s, flush=True),
) -> bool:
    """Enrol the demo person. Idempotent: a face already enrolled under ``LABEL`` is kept. True on success."""
    r = client.get("/api/enrol")
    log(f"demo enrol: list {r.status_code}")
    if r.status_code != 200:
        return False
    if any(f.get("label") == LABEL for f in r.json().get("faces", [])):
        log("demo enrol: already enrolled")
        return True
    frames = make_frames()
    body = {"label": LABEL, "frames": [base64.b64encode(f).decode("ascii") for f in frames]}
    del frames
    r = client.post("/api/enrol", json=body)
    del body
    if r.status_code == 201:
        d = r.json()
        log(
            f"demo enrol: 201 face_id={d.get('face_id')} label={d.get('label')} expires_at={d.get('expires_at')}"
        )
        return True
    detail = ""
    try:
        detail = str(r.json().get("error", ""))
    except ValueError:
        pass
    log(f"demo enrol: {r.status_code} {detail}")
    return False


def engine_allowlist_version(
    client: httpx.Client, want: int | None = None, wait_s: float = 10.0
) -> int | None:
    """Poll ``/api/status`` until the engine reports ``allowlist_version`` (>= ``want`` when given)."""
    t0 = time.monotonic()
    version: int | None = None
    while True:
        r = client.get("/api/status")
        eng = r.json().get("engine") if r.status_code == 200 else None
        if isinstance(eng, dict) and isinstance(eng.get("d"), dict):
            eng = eng["d"]  # wire envelope {t, v, d}: the EngineStatus fields are under "d"
        if isinstance(eng, dict) and isinstance(eng.get("allowlist_version"), int):
            version = eng["allowlist_version"]
            if want is None or version >= want:
                return version
        if time.monotonic() - t0 >= wait_s:
            return version
        time.sleep(0.5)


def make_client(api: str, token: str, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        base_url=api, headers={"Authorization": f"Bearer {token}"}, timeout=60, transport=transport
    )


def main() -> int:
    api = os.environ.get("DEMO_API", "http://127.0.0.1:8000")
    token = read_token(Path(os.environ.get("DEMO_API_LOG", "/tmp/ss_api.log")))
    if not token:
        print("demo enrol: no token line in the API log", file=sys.stderr)
        return 2
    with make_client(api, token) as client:
        if not enrol_demo(client):
            return 1
        v = engine_allowlist_version(client, want=1)
        print(f"demo enrol: engine allowlist_version={v}", flush=True)
        return 0 if v else 1


if __name__ == "__main__":
    sys.exit(main())
