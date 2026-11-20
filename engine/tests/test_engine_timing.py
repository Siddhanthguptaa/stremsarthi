"""StageTiming JSONL: category and time only."""

from __future__ import annotations

import json

from contracts import StageTiming, Topic
from engine.bus import LocalBus
from engine.timing import TimingLog


def test_disabled_without_a_path():
    log = TimingLog(None)
    assert not log.enabled
    log.write(StageTiming("compose", 1, 0.0, 1.0, 1.01))  # no-op
    log.close()
    assert log.written == 0


def test_unwritable_path_disables_the_log_instead_of_raising(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    log = TimingLog(blocker / "sub" / "t.jsonl")  # the parent is a file
    assert not log.enabled
    log.write(StageTiming("compose", 1, 0.0, 1.0, 1.01))


def test_lines_carry_only_stage_seq_pts_and_times(tmp_path):
    path = tmp_path / "a" / "t.jsonl"
    bus = LocalBus()
    log = TimingLog(path)
    log.attach(bus)
    bus.publish(Topic.TIMING, StageTiming("lane_fast", 7, 0.2333333333, 10.0, 10.0421))
    bus.publish(Topic.TIMING, StageTiming("ingest_decode", 8, None, 10.1, 10.1))  # from the relay
    log.close()
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert lines[0] == {
        "stage": "lane_fast",
        "seq": 7,
        "pts": 0.233333,
        "t_start": 10.0,
        "t_end": 10.0421,
        "ms": 42.1,
    }
    assert lines[1]["pts"] is None and lines[1]["ms"] == 0.0
    assert all(set(line) == {"stage", "seq", "pts", "t_start", "t_end", "ms"} for line in lines)
    bus.publish(Topic.TIMING, StageTiming("late", 9, None, 1.0, 1.0))  # detached after close
    assert len(path.read_text().splitlines()) == 2


def test_flush_makes_lines_visible_before_close(tmp_path):
    path = tmp_path / "t.jsonl"
    log = TimingLog(path, flush_interval_s=60.0)
    log.write(StageTiming("compose", 1, 0.0, 1.0, 1.002))
    log.flush()
    assert len(path.read_text().splitlines()) == 1
    log.close()
