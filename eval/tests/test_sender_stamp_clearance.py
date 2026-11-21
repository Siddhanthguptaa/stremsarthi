"""Sender scenes keep their targets clear of the optical stamp.

A held, moving card track is masked with a swept hull that reaches up to ~100 px beyond the card, so the G1 scenes keep
the card at least STAMP_CLEARANCE (120 px) below the bottom of the stamp band: a CORRECT card mask can then never cover
the stamp (that made stamp_unreadable 3.1 % instead of <= 1 %). The demo reel's targets are pinned (they must not change
with the G1 geometry) and its clearances are recorded.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from eval import phase1
from eval.sender import demo_scenes, stamp
from eval.sender.scenes import STAMP_CLEARANCE, ScenePlayer

SWEEP_MARGIN_PX = 100  # how far a held, moving track's swept hull reaches beyond the card
G1_SCRIPT = "card_static:12,card_moving:12,card_static:12,card_absent:9"
G1_SCRIPTS = {
    "lead G1 script": G1_SCRIPT,
    "main": phase1.MAIN_SCRIPT,
    "failsafe": phase1.FAILSAFE_SCRIPT,
    "hold": phase1.HOLD_SCRIPT,
    "soak": phase1.SOAK_SCRIPT,
    "live": phase1.LIVE_SCRIPT,
}
DEMO_REEL_TARGETS_SHA256 = "53784242e0da2ab95e18a3b47dd7545477fc1b1ac94ceced00086dc56c360e77"
DEMO_REEL_TARGET_COUNT = 762


def min_clearance(script: str, step: int = 3) -> dict[str, float]:
    """Per scene: the smallest distance (px) between any target corner and the bottom of the stamp band."""
    player = ScenePlayer(script, 30)
    out: dict[str, float] = {}
    for k in range(0, int(player.total_s * 30), step):
        name, targets = player.targets(k)
        for t in targets:
            gap = min(y for _, y in t.polygon()) - stamp.STAMP_EXCLUSION[3]
            out[name] = min(out.get(name, 1e9), gap)
    return out


def test_clearance_constant_covers_the_sweep_margin():
    assert STAMP_CLEARANCE >= SWEEP_MARGIN_PX + 20


@pytest.mark.parametrize("label", list(G1_SCRIPTS))
def test_g1_scenes_keep_targets_a_sweep_clear_of_the_stamp(label):
    gaps = min_clearance(G1_SCRIPTS[label])
    assert gaps, "the script has no target at all"
    for scene, gap in gaps.items():
        assert gap >= STAMP_CLEARANCE, f"{label}: {scene} comes within {gap:.0f} px of the stamp band"


def test_card_moving_keeps_its_size_speed_and_always_visible():
    player = ScenePlayer("card_moving:40", 30)
    sizes = {
        (t.quad[2] - t.quad[0], t.quad[3] - t.quad[1])
        for k in range(0, 1200, 7)
        for t in player.targets(k)[1]
    }
    assert len(sizes) == 1  # same card size throughout
    first = [player.targets(k)[1] for k in range(0, 1200, 3)]
    assert all(len(f) == 1 for f in first)  # a card in every frame: the visible-frame count is unchanged
    ys = [f[0].quad[1] for f in first]
    assert max(ys) - min(ys) > 150  # it still travels a long way vertically


def test_the_g1_script_still_gives_enough_card_visible_frames():
    player = ScenePlayer(G1_SCRIPT, 30)
    visible = sum(1 for k in range(int(player.total_s * 30)) if player.targets(k)[1])
    assert visible == 36 * 30  # 12 + 12 + 12 s of card, 9 s absent


def reel_targets() -> list[tuple[str, tuple[tuple[float, float], ...], str]]:
    p = demo_scenes.demo_player(30)
    out = []
    for k in range(0, int(demo_scenes.DEMO_REEL_SECONDS * 30), 5):
        name, tg = p.targets(k)
        out += [(name, t.polygon(), t.category) for t in tg]
    return out


def test_demo_reel_targets_are_unchanged_by_the_g1_geometry():
    h = hashlib.sha256()
    items = reel_targets()
    for name, poly, cat in items:
        h.update(json.dumps([name, cat, [[round(x, 2), round(y, 2)] for x, y in poly]]).encode())
    assert len(items) == DEMO_REEL_TARGET_COUNT
    assert h.hexdigest() == DEMO_REEL_TARGETS_SHA256


def test_demo_reel_targets_clear_of_the_stamp_and_the_caption_band():
    for name, poly, _cat in reel_targets():
        ys = [y for _, y in poly]
        assert min(ys) - stamp.STAMP_EXCLUSION[3] >= 24, (
            name
        )  # the existing 8 px rule with the reel's own margin
        assert max(ys) <= demo_scenes.CAPTION_Y0, name  # captions are drawn at y >= 668


def test_demo_reel_stamp_clearances_are_recorded():
    """Recorded, not asserted to 120 px: demo_aadhaar_moving keeps its original path (pinned above). If it starts
    to move or sweep, mask-reach into the stamp is possible there; the lead decides whether to move the reel."""
    gaps = {k: round(v) for k, v in min_clearance(demo_scenes.DEMO_REEL_SCRIPT, 5).items()}
    assert gaps["demo_aadhaar_moving"] == 24
    assert all(v >= 24 for v in gaps.values())
