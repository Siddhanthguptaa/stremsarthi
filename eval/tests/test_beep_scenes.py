"""Beep track and scene scripts."""

import numpy as np
import pytest

from eval.sender import beep, stamp
from eval.sender.scenes import SCENE_NAMES, ScenePlayer, parse_script


def test_beep_track_level_timing_and_continuity():
    track = beep.BeepTrack()
    pcm = np.concatenate([track.frame_samples(i) for i in range(90)])  # 3 s
    assert pcm.shape[0] == 90 * 1600
    assert abs(20 * np.log10(np.abs(pcm).max()) + 12.0) < 0.1
    # bursts begin exactly on 1.000 s boundaries and last 50 ms
    for k in range(3):
        a = k * 48000
        assert np.abs(pcm[a + 100 : a + 2300]).max() > 0.2
        assert np.abs(pcm[a + 2500 : a + 40000]).max() == 0.0
    assert beep.beep_frame_flag(0) and beep.beep_frame_flag(30) and not beep.beep_frame_flag(1)


def test_beep_detector_onsets_streamed_in_odd_chunks():
    track = beep.BeepTrack()
    pcm = np.concatenate([track.frame_samples(i) for i in range(100)])
    det = beep.BeepDetector(48000)
    pos = 0
    for size in [1024] * 200:
        if pos >= len(pcm):
            break
        det.feed(pcm[pos : pos + size], pos / 48000)
        pos += size
    assert len(det.onsets) == 4
    assert all(abs(o - k) < 0.006 for k, o in enumerate(det.onsets))


def test_beep_detector_ignores_quiet_noise():
    g = np.random.default_rng(1)
    noise = (g.standard_normal(48000) * 0.005).astype(np.float32)  # about -46 dBFS
    assert beep.detect_beeps(noise, 48000) == []


def test_parse_script():
    assert parse_script("card_absent:10,card_static") == [("card_absent", 10.0), ("card_static", 40.0)]
    with pytest.raises(ValueError):
        parse_script("nope:1")
    with pytest.raises(ValueError):
        parse_script("")


@pytest.mark.parametrize("name", SCENE_NAMES)
def test_scene_targets_clear_of_stamp_and_inside_frame(name):
    player = ScenePlayer(f"{name}:6", 30)
    sx1, sy1, sx2, sy2 = stamp.STAMP_EXCLUSION
    for i in range(0, 180, 2):
        frame, scene, targets = player.render(i)
        assert scene == name and frame.shape == (720, 1280, 3)
        for t in targets:
            x1, y1, x2, y2 = t.quad
            assert 0 <= x1 < x2 <= 1280 and 0 <= y1 < y2 <= 720
            assert x1 > sx2 + 8 or y1 > sy2 + 8  # clear of the stamp strip with a margin
        # the stamp corner itself is left untouched by every scene (the sender draws it later)
        assert frame[:32, :256].std() < 40  # plain backdrop there, no target pixels


def test_scene_script_switches_and_ground_truth():
    p = ScenePlayer("card_absent:1,card_static:1,card_moving:1", 30)
    names = [p.render(i)[1] for i in (0, 29, 30, 59, 60, 89)]
    assert names == ["card_absent", "card_absent", "card_static", "card_static", "card_moving", "card_moving"]
    assert p.render(0)[2] == []
    assert [t.category for t in p.render(30)[2]] == ["aadhaar"]
    assert len(ScenePlayer("many_targets:1", 30).render(0)[2]) == 5
    # moving card really moves
    assert p.render(60)[2][0].quad != p.render(80)[2][0].quad
