"""SS_FAULT sink hooks: parsing, stall window, one-shot crash, log format, dormant when unset."""

from __future__ import annotations

import json
from types import SimpleNamespace

from relay.sink import EgressFrameSink
from relay.sinkfault import SinkFault, parse_sink_faults


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_parser_keeps_only_sink_specs_and_ignores_bad_ones():
    specs = parse_sink_faults(
        "lane:fast:hang@t=5s;sink:stall@t=40s,for=3s;sink:crash@t=500ms;sink:nope@t=1;sink:stall"
    )
    assert [s.canonical() for s in specs] == ["sink:stall@t=40s,for=3s", "sink:crash@t=0.5s"]


def test_dormant_without_env():
    assert SinkFault.from_env(lambda: None, {}) is None
    assert SinkFault.from_env(lambda: None, {"SS_FAULT": "lane:fast:hang@t=1"}) is None


def test_stall_window_logs_active_and_cleared(tmp_path):
    log, clock, killed = tmp_path / "f.jsonl", Clock(), []
    fault = SinkFault(parse_sink_faults("sink:stall@t=2s,for=3s"), str(log), lambda: killed.append(1), clock)
    assert not fault.stalled()  # not started
    fault.start()
    for offset, expected in ((1.0, False), (2.1, True), (4.9, True), (5.2, False)):
        clock.now = 1000.0 + offset
        assert fault.stalled() is expected
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert [x["event"] for x in lines] == ["fault_active", "fault_cleared"]
    assert lines[0]["spec"] == "sink:stall@t=2s,for=3s" and lines[0]["proc"] == "relay"
    assert lines[0]["t_monotonic"] == 1002.0 and lines[1]["t_monotonic"] == 1005.0 and not killed


def test_crash_fires_once(tmp_path):
    log, clock, killed = tmp_path / "f.jsonl", Clock(), []
    fault = SinkFault(parse_sink_faults("sink:crash@t=1s"), str(log), lambda: killed.append(1), clock)
    fault.start()
    for offset in (0.5, 1.0, 1.5, 9.0):
        clock.now = 1000.0 + offset
        fault.stalled()
    assert killed == [1] and len(log.read_text().splitlines()) == 1


def test_sink_stall_discards_frames_and_turns_unhealthy(monkeypatch):
    monkeypatch.setenv("SS_FAULT", "sink:stall@t=0s,for=60s")
    monkeypatch.delenv("SS_FAULT_LOG", raising=False)
    submitted = []
    writer = SimpleNamespace(write=lambda *a: submitted.append(a), alive=True, submitted=0)
    sink = EgressFrameSink.__new__(EgressFrameSink)
    sink.egress = SimpleNamespace(healthy=True, encoder=SimpleNamespace(kill_worker=lambda: None))
    sink.writer, sink._closed = writer, False
    sink._fault = SinkFault.from_env(lambda: None)
    sink.write(0.0, None, None)
    assert submitted == [] and sink.healthy is False


def test_sink_without_fault_passes_frames_through():
    submitted = []
    sink = EgressFrameSink.__new__(EgressFrameSink)
    sink.egress = SimpleNamespace(healthy=True)
    sink.writer, sink._closed, sink._fault = (
        SimpleNamespace(write=lambda *a: submitted.append(a), alive=True),
        False,
        None,
    )
    sink.write(0.0, "img", "pcm")
    assert len(submitted) == 1 and sink.healthy is True
