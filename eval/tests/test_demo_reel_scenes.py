"""The demo reel scenes and the sender's --audio-file option: SPECIMEN targets stay clear of the stamp and the
caption band, every scene renders, the preset expands, and a looped WAV replaces the beep track."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from eval.sender import scenes as sc
from eval.sender.audiofile import FileTrack, load_track
from eval.sender.demo_scenes import (
    CAPTION_Y0,
    DEMO_REEL_SCRIPT,
    DEMO_REEL_SECONDS,
    DEMO_UPI_PAYLOAD,
    FACES_CACHE,
    REEL,
    caption_box,
    reel_schedule,
)
from eval.sender.stamp import CANVAS_H, CANVAS_W, STAMP_EXCLUSION

FACES_PRESENT = FACES_CACHE.is_dir() and any(FACES_CACHE.glob("*.jpg"))


def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def test_reel_preset_expands_to_ninety_seconds_of_ten_scenes() -> None:
    entries = sc.parse_script("demo_reel")
    assert [n for n, _ in entries] == [n for n, _, _ in REEL]
    assert abs(sum(d for _, d in entries) - 90.0) < 1e-9 and DEMO_REEL_SECONDS == 90.0
    assert sc.parse_script(DEMO_REEL_SCRIPT) == entries
    assert [round(r["t0"], 1) for r in reel_schedule()][:3] == [0.0, 7.0, 15.0]


def test_upi_payload_is_a_specimen_upi_link() -> None:
    assert (
        DEMO_UPI_PAYLOAD.startswith("upi://pay?")
        and ".zz@" in DEMO_UPI_PAYLOAD
        and "SPECIMEN" in DEMO_UPI_PAYLOAD
    )


@pytest.mark.parametrize("name,dur,caption", REEL)
def test_scene_targets_clear_of_stamp_and_caption(name: str, dur: float, caption: str) -> None:
    if name == "demo_faces" and not FACES_PRESENT:
        pytest.skip("NASA portraits not fetched (scripts/demo_assets.sh fetches them)")
    player = sc.ScenePlayer([(name, dur)], 30)
    cap = caption_box(caption)
    assert cap[1] >= CAPTION_Y0 and cap[3] <= CANVAS_H
    n_targets = 0
    for t in np.arange(0.0, dur, 0.7):
        index = int(t * 30)
        frame, scene, targets = player.render(index)
        assert scene == name and frame.shape == (CANVAS_H, CANVAS_W, 3) and frame.dtype == np.uint8
        _, again = player.targets(index)
        assert [x.quad for x in again] == [x.quad for x in targets]
        for tg in targets:
            x1, y1, x2, y2 = tg.quad
            assert x1 >= 0 and y1 >= 0 and x2 <= CANVAS_W and y2 <= CANVAS_H, (name, t, tg.quad)
            assert y2 <= CAPTION_Y0, (name, t, tg.quad)
            assert not _overlap(tg.quad, STAMP_EXCLUSION), (name, t, tg.quad)
            assert not _overlap(tg.quad, cap)
        n_targets += len(targets)
        # the caption is drawn: white text pixels inside its box
        crop = frame[cap[1] : cap[3], cap[0] : cap[2]]
        assert (crop.min(axis=2) > 230).mean() > 0.02
    assert (n_targets > 0) == (name != "demo_calm")


@pytest.mark.skipif(not FACES_PRESENT, reason="NASA portraits not fetched")
def test_faces_scene_swaps_the_enrolled_face_to_the_other_side() -> None:
    player = sc.ScenePlayer([("demo_faces", 14.0)], 30)
    _, first = player.targets(0)
    _, second = player.targets(int(10 * 30))
    for targets in (first, second):
        assert sorted(t.category for t in targets) == ["face", "face_enrolled"]
    left = lambda ts: min(ts, key=lambda t: t.quad[0]).category  # noqa: E731
    assert left(first) == "face_enrolled" and left(second) == "face"


def test_file_track_loops_and_matches_the_sender_frame_size(tmp_path: Path) -> None:
    wav = tmp_path / "a.wav"
    ramp = (np.arange(48000 // 10) / 48000 * 2 - 0.5).astype(np.float32) * 0.5  # 0.1 s
    sf.write(str(wav), ramp, 48000, subtype="FLOAT")
    track = FileTrack(wav, 48000, 30)
    assert track.samples_per_frame == 1600 and abs(track.duration_s - 0.1) < 1e-6
    f = track.frame_samples(0)
    assert f.shape == (1600,) and f.dtype == np.float32
    n = len(track.samples)
    np.testing.assert_allclose(track.frame_samples(7)[:5], track.samples[(7 * 1600) % n : (7 * 1600) % n + 5])
    wrapped = track.frame_samples(3)  # 4800 samples exactly = the loop point: starts at 0 again
    np.testing.assert_allclose(wrapped[:10], track.samples[:10])


def test_load_track_resamples_and_downmixes(tmp_path: Path) -> None:
    wav = tmp_path / "s.wav"
    t = np.arange(16000) / 16000
    stereo = (
        np.stack([np.sin(2 * np.pi * 440 * t), np.sin(2 * np.pi * 440 * t)], axis=1).astype(np.float32) * 0.5
    )
    sf.write(str(wav), stereo, 16000)
    x = load_track(wav)
    assert x.ndim == 1 and abs(len(x) - 48000) <= 2 and 0.3 < float(np.max(np.abs(x))) <= 0.55
    empty = tmp_path / "e.wav"
    sf.write(str(empty), np.zeros(0, dtype=np.float32), 48000)
    with pytest.raises(ValueError):
        load_track(empty)


def test_sender_writes_a_file_with_an_audio_file_track(tmp_path: Path) -> None:
    import av  # noqa: PLC0415

    from eval.sender.sender import run_sender  # noqa: PLC0415

    wav = tmp_path / "tone.wav"
    sf.write(str(wav), (0.3 * np.sin(2 * np.pi * 300 * np.arange(48000) / 48000)).astype(np.float32), 48000)
    out = tmp_path / "o.mp4"
    stats = run_sender(
        str(out), "demo_calm:2", duration=1.0, realtime=False, truth_path=tmp_path / "t.jsonl", audio_file=wav
    )
    assert stats["frames_sent"] == 30
    with av.open(str(out)) as c:
        audio = [f.to_ndarray() for f in c.decode(audio=0)]
    assert audio and float(np.abs(np.concatenate(audio, axis=1)).max()) > 0.1
    truth = [__import__("json").loads(line) for line in (tmp_path / "t.jsonl").read_text().splitlines()]
    assert not any(r.get("beep") for r in truth if r["type"] == "frame")  # a file track carries no beeps
