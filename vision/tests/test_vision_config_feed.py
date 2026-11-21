"""Live category settings reach the vision lanes through the REAL host wiring: ``Topic.CONFIG`` on the bus ->
``VisionHost`` -> shared ``config.categories`` -> lane (skips a disabled detector) and planner (drops tracks).

Regression: nothing subscribed to the config topic, so a category toggle in the UI had no effect on video."""

from __future__ import annotations

import logging
import time

import numpy as np
import pytest

from contracts import Category, Config, ConfigChange, Detection, Frame, Lane, Quad, Topic
from contracts.fakes import FakeDetector, InProcessBus
from vision import host as vhost
from vision.planner import planner_factory

IMG = np.zeros((72, 128, 3), np.uint8)


class _PanDet(FakeDetector):
    id = "pan_fake"
    lane = Lane.FAST
    categories = frozenset({Category.PAN})

    def detect(self, frame, ctx):
        self.calls.append(frame.seq)
        return [
            Detection(
                "pan_fake", Category.PAN, Quad.from_xyxy(100, 100, 300, 200), 0.95, frame.seq, frame.pts, 0.0
            )
        ]


class Rig:
    def __init__(self, *, pan_enabled: bool = True) -> None:
        self.cfg = Config(profile="dev", _env_file=None)
        if not pan_enabled:
            self.cfg.categories[Category.PAN] = self.cfg.categories[Category.PAN].model_copy(
                update={"enabled": False}
            )
        self.bus = InProcessBus()
        self.host = vhost.VisionHost(
            self.cfg, self.bus, registry={"pan_fake": _PanDet}, assembler_factory=planner_factory
        )
        self.host.start()
        self.det = self.host.detectors["pan_fake"]
        self.seq = 0

    def patch(self, diff: dict) -> None:
        self.bus.publish(Topic.CONFIG, ConfigChange(diff, "test"))

    def wait(self, cond, timeout: float = 3.0) -> None:
        end = time.monotonic() + timeout
        while not cond() and time.monotonic() < end:
            time.sleep(0.01)
        assert cond(), "the host never applied the change"

    def run_once(self):
        """Offer the next on-stride frame and wait for the lane result."""
        self.host.poll()
        seq = self.seq
        self.seq += 3
        self.host.submit(Frame(seq, seq / 30.0, time.monotonic(), IMG))
        end = time.monotonic() + 3
        while time.monotonic() < end:
            for r in self.host.poll():
                if r.frame_seq == seq:
                    return r
            time.sleep(0.005)
        raise AssertionError("no lane result")

    def close(self) -> None:
        self.host.close()


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


def _pan_on(r) -> bool:
    return r.cfg.categories[Category.PAN].enabled


def test_switching_a_category_off_stops_its_masks_within_one_run_and_on_resumes(rig: Rig) -> None:
    for _ in range(3):
        assert rig.run_once().requests, "the category masks while enabled"
    calls = len(rig.det.calls)
    rig.patch({"categories.pan.enabled": False})
    rig.wait(lambda: not _pan_on(rig))
    r = rig.run_once()
    assert r.ok and r.requests == (), "no mask (not even a held one) on the run after the switch"
    assert len(rig.det.calls) == calls, "a disabled detector is not run"
    rig.patch({"categories.pan.enabled": True})
    rig.wait(lambda: _pan_on(rig))
    assert rig.run_once().requests, "masking resumes on the next run"
    assert len(rig.det.calls) == calls + 1


def test_switching_off_clears_held_tracks_so_a_quick_switch_back_does_not_resurrect_them(rig: Rig) -> None:
    for _ in range(3):
        rig.run_once()
    rig.patch({"categories.pan.enabled": False})
    rig.wait(lambda: not _pan_on(rig))
    rig.run_once()
    rig.det.calls.clear()
    # switched back on while the old hold (2 s) would still be alive: the first run only has its own detection
    rig.patch({"categories.pan.enabled": True})
    rig.wait(lambda: _pan_on(rig))
    (req,) = rig.run_once().requests
    assert req.reason == "detected" or "held" not in req.reason


def test_a_detector_that_started_disabled_is_warmed_when_first_switched_on() -> None:
    r = Rig(pan_enabled=False)
    try:
        assert r.det.configured and not r.det.warmed
        assert r.run_once().requests == () and r.det.calls == []
        r.patch({"categories.pan.enabled": True})
        r.wait(lambda: _pan_on(r))
        assert r.det.warmed
        assert r.run_once().requests
    finally:
        r.close()


def test_sensitivity_mask_when_unsure_and_padding_are_applied_live(rig: Rig) -> None:
    rig.patch(
        {
            "categories.pan.sensitivity": 0.9,
            "categories.pan.mask_when_unsure": False,
            "categories.pan.padding_px": 30,
        }
    )
    rig.wait(lambda: rig.cfg.categories[Category.PAN].padding_px == 30)
    c = rig.cfg.categories[Category.PAN]
    assert c.sensitivity == 0.9 and c.mask_when_unsure is False and c.enabled is True


@pytest.mark.parametrize(
    "diff",
    [
        {"categories.pan.enabled": "false"},
        {"categories.pan.enabled": 0},
        {"categories.pan.enabled": None},
        {"categories.pan.sensitivity": 5.0},
        {"categories.pan.sensitivity": True},
        {"categories.pan.padding_px": -3},
        {"categories.pan.padding_px": 2.5},
        {"categories.nonsense.enabled": False},
        {"categories.pan.bogus": False},
        {"categories.pan": False},
        {"categories.pan.enabled.extra": False},
        {"audio.languages": ["hi"]},
    ],
)
def test_invalid_or_foreign_diffs_never_change_a_category(rig: Rig, diff: dict, caplog) -> None:
    before = rig.cfg.categories[Category.PAN].model_dump()
    feed = rig.host._config_feed
    with caplog.at_level(logging.DEBUG, logger="vision.config"):
        assert feed.apply(ConfigChange(diff, "test")) == 0
    assert rig.cfg.categories[Category.PAN].model_dump() == before
    assert "5.0" not in caplog.text and "false" not in caplog.text.lower().replace("categories", "")
    assert rig.run_once().requests, "still masking"


def test_non_config_events_are_ignored(rig: Rig) -> None:
    assert rig.host._config_feed.apply("not a change") == 0
    assert _pan_on(rig)


def test_style_is_left_to_the_engine(rig: Rig) -> None:
    style = rig.cfg.categories[Category.PAN].style
    assert rig.host._config_feed.apply(ConfigChange({"categories.pan.style": "solid"}, "t")) == 0
    assert rig.cfg.categories[Category.PAN].style == style


def test_dropped_config_events_reset_to_the_startup_defaults_and_log_an_error(rig: Rig, caplog) -> None:
    feed = rig.host._config_feed
    rig.patch({"categories.pan.enabled": False, "categories.pan.padding_px": 7})
    rig.wait(lambda: not _pan_on(rig))
    startup_pad = feed._startup[Category.PAN].padding_px
    with caplog.at_level(logging.ERROR, logger="vision.config"):
        feed.on_drop()
    assert _pan_on(rig), "a category that was on at start is on again (more masking, never less)"
    assert rig.cfg.categories[Category.PAN].padding_px == startup_pad
    assert "dropped" in caplog.text
    assert rig.run_once().requests


def test_a_category_that_was_off_at_startup_stays_off_after_a_drop() -> None:
    r = Rig(pan_enabled=False)
    try:
        r.host._config_feed.on_drop()
        assert not _pan_on(r)
    finally:
        r.close()


def test_a_real_bus_overflow_triggers_the_reset(caplog) -> None:
    cfg = Config(profile="dev", _env_file=None)
    bus = InProcessBus(maxsize=2)
    host = vhost.VisionHost(cfg, bus, registry={"pan_fake": _PanDet}, assembler_factory=planner_factory)
    host.start()
    try:
        with caplog.at_level(logging.ERROR):
            for _ in range(10):
                bus.publish(Topic.CONFIG, ConfigChange({"categories.pan.enabled": False}, "test"))
            end = time.monotonic() + 3
            while host._config_feed.resets == 0 and time.monotonic() < end:
                time.sleep(0.01)
        assert host._config_feed.resets >= 1
        time.sleep(0.3)  # the feed thread may still be applying queued 'off' events: a drop always wins last
        host._config_feed.on_drop()
        assert cfg.categories[Category.PAN].enabled
    finally:
        host.close()
