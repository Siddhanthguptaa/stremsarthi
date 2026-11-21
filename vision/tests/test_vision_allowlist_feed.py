"""Face enrolment reaches the faces detector through the REAL host wiring: ``Topic.ALLOWLIST`` on the bus ->
``VisionHost`` -> ``VisionResources.allowlist`` -> votes -> ``allow=True`` (CPU, fake backend, no model files).

Regression: nothing subscribed to the allowlist topic, so an enrolled person was masked as ``face_no_allowlist``."""

from __future__ import annotations

import logging
import time

import numpy as np
import pytest

from contracts import (
    AllowlistUpdate,
    Config,
    DetectContext,
    DetectorConfig,
    Device,
    Frame,
    Lane,
    Topic,
)
from contracts.fakes import InProcessBus
from vision import host as vhost
from vision.detectors.faces import FacesDetector
from vision.detectors.faces.core import EMBEDDING_DIM
from vision.resources import VisionResources

W, H = 1280, 720
IDENT = {"alice": 1, "bob": 2}
BOX = (300, 200, 420, 340)


def _vec(code: int) -> np.ndarray:
    v = np.zeros(EMBEDDING_DIM, np.float32)
    v[code] = 1.0
    return v


class _Backend:
    """One face per frame; its embedding is the unit vector of the identity in ``who``."""

    who = "alice"

    def detect(self, image, score_threshold):
        h, w = image.shape[:2]
        x1, y1, x2, y2 = BOX
        bw, bh = x2 - x1, y2 - y1
        cx, cy = x1 + bw / 2, y1 + bh / 2
        lm = [
            (cx - 0.2 * bw, cy - 0.15 * bh),
            (cx + 0.2 * bw, cy - 0.15 * bh),
            (cx, cy + 0.05 * bh),
            (cx - 0.15 * bw, cy + 0.25 * bh),
            (cx + 0.15 * bw, cy + 0.25 * bh),
        ]
        row = np.array([x1, y1, bw, bh, *[c for p in lm for c in p], 0.9], dtype=np.float32)
        row[[0, 2, 4, 6, 8, 10, 12]] *= w / W
        row[[1, 3, 5, 7, 9, 11, 13]] *= h / H
        return row[None, :]

    def align(self, image, row):
        return np.full((112, 112, 3), IDENT[type(self).who], np.uint8)

    def embed_aligned(self, aligned):
        return _vec(int(aligned[0, 0, 0]))

    def embed(self, image, row):
        return self.embed_aligned(self.align(image, row))

    def warmup(self):
        pass


class _Faces(FacesDetector):
    def __init__(self) -> None:
        super().__init__(_Backend())


class Rig:
    def __init__(self, bus: object | None = None) -> None:
        self.cfg = Config(profile="dev", _env_file=None)
        self.bus = bus or InProcessBus()
        self.host = vhost.VisionHost(
            self.cfg,
            self.bus,
            registry={"faces": _Faces},
            resources=VisionResources(self.cfg, ocr=lambda f: None),  # type: ignore[arg-type]
        )
        self.host.start()
        self.det = self.host.detectors["faces"]
        self.det.configure(  # synchronous embedding: one vote per run
            DetectorConfig(categories=self.cfg.categories, device=Device.CPU, params={"embed_async": False}),
            self.host.resources,
        )
        self.seq = 0
        self.image = np.full((H, W, 3), 100, np.uint8)

    def publish(self, update: AllowlistUpdate) -> None:
        self.bus.publish(Topic.ALLOWLIST, update)

    def wait_version(self, version: int, timeout: float = 3.0) -> None:
        end = time.monotonic() + timeout
        while self.host.resources.allowlist.version < version and time.monotonic() < end:
            time.sleep(0.01)
        assert self.host.resources.allowlist.version >= version, "the host never applied the update"

    def run(self, who: str, n: int = 12):
        _Backend.who = who
        d = None
        for _ in range(n):
            frame = Frame(self.seq, self.seq / 30.0, 0.0, self.image)
            self.seq += 3
            ctx = DetectContext(
                lane=Lane.FAST,
                categories=self.cfg.categories,
                stride=3,
                allowlist=self.host.resources.allowlist,
            )
            (d,) = self.det.detect(frame, ctx)
        return d

    def close(self) -> None:
        self.host.close()


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


def _add(face_id: str, code: int, version: int) -> AllowlistUpdate:
    return AllowlistUpdate("add", face_id, "SPECIMEN", tuple(float(x) for x in _vec(code)), version)


def test_empty_allowlist_masks_everybody_until_an_add_arrives(rig: Rig) -> None:
    d = rig.run("alice")
    assert not d.allow and d.extra["reason"] == "face_no_allowlist"


def test_enrolled_face_becomes_allowed_and_a_stranger_stays_masked(rig: Rig) -> None:
    rig.publish(_add("alice", IDENT["alice"], 1))
    rig.wait_version(1)
    assert rig.run("alice").allow
    assert not rig.run("bob", n=15).allow


def test_remove_masks_the_face_again_and_snapshot_replaces_the_list(rig: Rig) -> None:
    rig.publish(_add("alice", IDENT["alice"], 1))
    rig.publish(_add("bob", IDENT["bob"], 2))
    rig.wait_version(2)
    assert rig.run("alice").allow
    v = rig.host.resources.allowlist.version
    rig.publish(AllowlistUpdate("remove", "alice", None, None, 3))
    rig.wait_version(v + 1)
    assert not rig.run("alice", n=15).allow  # votes were reset by the version change
    rig.seq = 0  # a new person steps in: pts restarts, so tracks and votes start over
    assert rig.run("bob", n=20).allow
    v = rig.host.resources.allowlist.version
    rig.publish(AllowlistUpdate("snapshot", None, None, None, 4))
    rig.wait_version(v + 1)
    assert len(rig.host.resources.allowlist) == 0
    assert not rig.run("bob", n=15).allow
    rig.publish(_add("alice", IDENT["alice"], 4))  # snapshot is followed by its adds
    rig.wait_version(v + 2)
    assert rig.run("alice").allow


def test_malformed_updates_are_ignored_and_never_clear_or_allow(rig: Rig, caplog) -> None:
    rig.publish(_add("alice", IDENT["alice"], 1))
    rig.wait_version(1)
    feed = rig.host._allow_feed
    v = rig.host.resources.allowlist.version
    bad_dim = AllowlistUpdate("add", "mallory", None, (0.5,) * 64, 2)  # wrong dimension
    zero = AllowlistUpdate("add", "zero", None, (0.0,) * EMBEDDING_DIM, 2)
    with caplog.at_level(logging.DEBUG, logger="vision.allowlist"):
        assert feed.apply(bad_dim) is False
        assert feed.apply(zero) is False
        assert feed.apply("not an update") is False
    assert rig.host.resources.allowlist.version == v and len(rig.host.resources.allowlist) == 1
    assert rig.run("alice").allow  # the existing entry survives
    assert "0.5" not in caplog.text  # no embedding values in the logs


def test_embeddings_are_stored_normalised_and_never_in_repr(rig: Rig) -> None:
    emb = tuple(float(x) * 3.0 for x in _vec(7))
    rig.publish(AllowlistUpdate("add", "alice", None, emb, 1))
    rig.wait_version(1)
    ((fid, vec),) = rig.host.resources.allowlist.embeddings()
    assert fid == "alice" and vec.dtype == np.float32 and abs(float(np.linalg.norm(vec)) - 1.0) < 1e-5
    assert "3.0" not in repr(rig.host.resources.allowlist) and "3.0" not in repr(_add("a", 1, 1))


def test_no_bus_means_no_feed() -> None:
    cfg = Config(profile="dev", _env_file=None)
    h = vhost.VisionHost(cfg, None, registry={}, resources=VisionResources(cfg, ocr=lambda f: None))  # type: ignore[arg-type]
    assert h._allow_feed is None


# --------------------------------------------------------------------------- review fixes: order, failure, overflow, cap
def _wait(cond, timeout: float = 3.0) -> None:
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.01)
    assert cond()


def test_stale_updates_are_dropped_and_the_applied_version_is_reported(rig: Rig) -> None:
    assert rig.host.applied_allowlist_version == 0
    rig.publish(_add("alice", IDENT["alice"], 1))
    rig.publish(_add("bob", IDENT["bob"], 2))
    rig.publish(AllowlistUpdate("remove", "alice", None, None, 3))
    _wait(lambda: rig.host.applied_allowlist_version == 3)
    feed = rig.host._allow_feed
    assert feed.apply(_add("alice", IDENT["alice"], 1)) is False  # a replayed old add must not bring her back
    assert (
        feed.apply(AllowlistUpdate("snapshot", None, None, None, 2)) is False
    )  # an old snapshot: no rollback
    assert {f for f, _ in rig.host.resources.allowlist.embeddings()} == {"bob"}
    assert rig.host.applied_allowlist_version == 3
    assert feed.apply(AllowlistUpdate("snapshot", None, None, None, 5)) is True  # newer: authoritative
    assert rig.host.applied_allowlist_version == 5 and len(rig.host.resources.allowlist) == 0
    assert feed.apply(_add("alice", IDENT["alice"], 5)) is True  # the snapshot's adds carry its version


def test_a_feed_that_cannot_subscribe_fails_closed() -> None:
    class NoSub(InProcessBus):
        def subscribe(self, topics):
            raise RuntimeError("no bus")

    r = Rig(NoSub())
    try:
        assert r.host.applied_allowlist_version == 0 and len(r.host.resources.allowlist) == 0
        d = r.run("alice")
        assert not d.allow and d.extra["reason"] == "face_no_allowlist"
    finally:
        r.close()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_dead_feed_thread_clears_the_allowlist(rig: Rig) -> None:
    rig.publish(_add("alice", IDENT["alice"], 1))
    rig.wait_version(1)
    assert rig.run("alice").allow
    feed = rig.host._allow_feed

    def boom():
        raise SystemExit  # not an Exception: escapes the per-event guard and ends the thread

    feed._next = boom
    _wait(lambda: len(rig.host.resources.allowlist) == 0)
    assert rig.host.applied_allowlist_version == 0
    assert not rig.run("alice", n=15).allow


def test_dropped_bus_events_clear_the_list_until_a_snapshot_arrives() -> None:
    r = Rig(InProcessBus(maxsize=2))
    try:
        r.publish(_add("alice", IDENT["alice"], 1))
        r.wait_version(1)
        for i in range(10):  # overflow the 2-slot subscription: oldest events are dropped
            r.publish(_add(f"x{i}", 10 + i, 2 + i))
        _wait(lambda: r.host.applied_allowlist_version == 0 and len(r.host.resources.allowlist) == 0)
        time.sleep(0.2)
        feed = r.host._allow_feed
        assert (
            feed.apply(_add("alice", IDENT["alice"], 20)) is False
        )  # a stale add after a drop is not trusted
        assert feed.apply(AllowlistUpdate("snapshot", None, None, None, 12)) is True
        assert feed.apply(_add("alice", IDENT["alice"], 12)) is True
        assert r.host.applied_allowlist_version == 12
    finally:
        r.close()


def test_the_list_is_capped_and_the_cap_is_logged_once(rig: Rig, caplog) -> None:
    from vision.allowlist_feed import MAX_ENTRIES

    feed = rig.host._allow_feed
    with caplog.at_level(logging.ERROR, logger="vision.allowlist"):
        for i in range(MAX_ENTRIES + 6):
            feed.apply(_add(f"f{i}", i % EMBEDDING_DIM, 1))
    assert len(rig.host.resources.allowlist) == MAX_ENTRIES
    assert caplog.text.count("allowlist full") == 1
    assert feed.apply(_add("f0", 5, 1)) is True  # updating an existing entry is still allowed
