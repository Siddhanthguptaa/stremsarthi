"""``Enroller`` with a scripted fake backend (CPU, no model files)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from contracts import EnrolConfig, FaceEnroller
from vision.detectors.faces.core import EMBEDDING_DIM, as_bgr, l2_normalise
from vision.detectors.faces.enroll import MIN_FACE_PX, Enroller


def _vec(code: int, noise: float = 0.0, seed: int = 0) -> np.ndarray:
    v = np.zeros(EMBEDDING_DIM, np.float32)
    v[code] = 1.0
    if noise:
        v += np.random.default_rng(seed).normal(0, noise, EMBEDDING_DIM).astype(np.float32)
    return l2_normalise(v)


class FakeBackend:
    """Frame pixel [0, 0, 0] is the identity code (0: no face). Face size comes from pixel [0, 0, 1]."""

    def __init__(self) -> None:
        self.calls = 0

    def detect(self, image: np.ndarray, score_threshold: float) -> np.ndarray | None:
        code, size = int(image[0, 0, 0]), int(image[0, 0, 1])
        if code == 0:
            return None
        h, w = image.shape[:2]
        side = float(size)
        x, y = w / 2 - side / 2, h / 2 - side / 2
        return np.array([[x, y, side, side, *([x] * 10), 0.95]], dtype=np.float32)

    def align(self, image: np.ndarray, row: np.ndarray) -> np.ndarray:
        return np.full((112, 112, 3), image[0, 0, 0], np.uint8)

    def embed_aligned(self, aligned: np.ndarray) -> np.ndarray:
        self.calls += 1
        return _vec(int(aligned[0, 0, 0]), noise=0.05, seed=self.calls)

    def embed(self, image: np.ndarray, row: np.ndarray) -> np.ndarray:
        return self.embed_aligned(self.align(image, row))

    def warmup(self) -> None:
        pass


def frame(code: int, size: int = 160, shape: tuple[int, int] = (480, 640)) -> np.ndarray:
    img = np.full((*shape, 3), 128, np.uint8)
    img[0, 0, 0], img[0, 0, 1] = code, size
    return img


def test_enrol_averages_normalises_and_is_deterministic() -> None:
    enr = Enroller(backend=FakeBackend())
    fid, emb = enr.enrol([frame(1) for _ in range(6)], "me")
    assert fid.startswith("f_") and len(fid) == 18
    assert len(emb) == EMBEDDING_DIM
    arr = np.array(emb)
    assert float(np.linalg.norm(arr)) == pytest.approx(1.0, abs=1e-5)
    assert float(arr @ _vec(1)) > 0.95
    fid2, emb2 = Enroller(backend=FakeBackend()).enrol([frame(1) for _ in range(6)], "me")
    assert (fid, emb) == (fid2, emb2)
    assert Enroller(backend=FakeBackend()).enrol([frame(1) for _ in range(6)], "other")[0] != fid


def test_default_minimum_comes_from_config_and_too_few_frames_raise() -> None:
    enr = Enroller(backend=FakeBackend())
    assert enr.min_frames == EnrolConfig().min_frames == 5
    with pytest.raises(ValueError, match="too_few_faces"):
        enr.enrol([frame(1) for _ in range(4)], "me")
    with pytest.raises(ValueError, match="too_few_faces"):
        enr.enrol([], "me")


def test_frames_without_a_usable_face_are_skipped() -> None:
    enr = Enroller(backend=FakeBackend())
    frames = [
        frame(1),
        frame(0),
        frame(1),
        frame(1, size=MIN_FACE_PX - 1),
        frame(1),
        None,
        b"junk",
        frame(1),
        frame(1),
    ]
    fid, _ = enr.enrol(frames, "me")  # 5 usable
    assert fid
    with pytest.raises(ValueError, match="too_few_faces"):
        enr.enrol(frames[:8], "me")  # only 4 usable


def test_an_outlier_frame_somebody_else_in_the_shot_is_dropped() -> None:
    enr = Enroller(backend=FakeBackend())
    _, emb = enr.enrol([frame(1)] * 6 + [frame(2)], "me")
    assert float(np.array(emb) @ _vec(1)) > 0.95
    assert float(np.array(emb) @ _vec(2)) < 0.2


def test_two_people_alternating_is_rejected_when_too_few_remain() -> None:
    enr = Enroller(backend=FakeBackend())
    frames = [frame(1), frame(2), frame(1), frame(2), frame(3), frame(4)]
    with pytest.raises(ValueError, match="too_few_faces"):
        enr.enrol(frames, "me")


def test_min_frames_override_and_validation() -> None:
    assert Enroller(min_frames=2, backend=FakeBackend()).enrol([frame(1), frame(1)], "me")
    with pytest.raises(ValueError):
        Enroller(min_frames=0, backend=FakeBackend())


def test_large_frames_are_detected_downscaled_and_boxes_map_back() -> None:
    seen: list[tuple[int, int]] = []

    class Spy(FakeBackend):
        def detect(self, image: np.ndarray, score_threshold: float):  # type: ignore[no-untyped-def]
            seen.append(image.shape[:2])
            return super().detect(image, score_threshold)

    big = frame(1, size=200, shape=(1080, 1920))
    Enroller(backend=Spy()).enrol([big] * 5, "me")
    assert all(max(s) <= 640 for s in seen)


def test_encoded_bytes_and_bad_inputs() -> None:
    img = np.random.default_rng(0).integers(0, 255, (64, 64, 3), dtype=np.uint8)
    ok, enc = cv2.imencode(".png", img)
    assert ok and as_bgr(enc.tobytes()) is not None
    assert as_bgr(b"not an image") is None
    assert as_bgr(np.zeros((8, 8, 3), np.uint8)) is None  # too small
    assert as_bgr(np.zeros((64, 64), np.uint8)) is None
    assert as_bgr(np.zeros((64, 64, 3), np.float32)) is None
    assert as_bgr("path/to/file.jpg") is None


def test_implements_the_face_enroller_protocol_and_writes_nothing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    enr = Enroller(backend=FakeBackend())
    assert isinstance(enr, FaceEnroller)
    enr.enrol([frame(1)] * 5, "me")
    assert list(tmp_path.iterdir()) == [], "the API owns the allowlist store; the enroller persists nothing"


def test_no_embedding_values_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    Enroller(backend=FakeBackend()).enrol([frame(1)] * 5, "me")
    assert caplog.records == []
