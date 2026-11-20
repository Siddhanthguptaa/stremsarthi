"""SS_FAULT protocol: parser, injector, log records and the hook-presence xfails (T1.4).

The hooks live in other owners' modules (engine, vision, relay). Until they honour ``SS_FAULT`` the two
``test_*_honours_ss_fault`` tests xfail with the exact reason; the day a hook lands they start to pass
(``strict=False``) and the xfail marker can be dropped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval import fault
from eval.fault import (
    FaultInjector,
    FaultSpecError,
    activation_time,
    faults_from_env,
    format_env,
    hooks_present,
    parse_faults,
    parse_spec,
    parse_time,
    read_fault_log,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("20", 20.0), ("20s", 20.0), ("1.5s", 1.5), ("500ms", 0.5), (" 3 ", 3.0), ("0", 0.0)],
)
def test_parse_time(text, seconds):
    assert parse_time(text) == seconds


@pytest.mark.parametrize("text", ["", "s", "-1s", "1m", "1.2.3", "abc", "5 s"])
def test_parse_time_rejects(text):
    with pytest.raises(FaultSpecError):
        parse_time(text)


def test_the_three_documented_specs():
    a = parse_spec("lane:accurate:hang@t=20s")
    assert (a.kind, a.mode, a.lane, a.at_s, a.for_s) == ("lane", "hang", "accurate", 20.0, None)
    b = parse_spec("lane:fast:crash@t=5")
    assert (b.kind, b.mode, b.lane, b.at_s) == ("lane", "crash", "fast", 5.0)
    c = parse_spec("sink:stall@t=40s,for=3s")
    assert (c.kind, c.mode, c.lane, c.at_s, c.for_s) == ("sink", "stall", None, 40.0, 3.0)
    assert c.end_s == 43.0 and a.end_s is None


def test_canonical_round_trip():
    for text in (
        "lane:accurate:hang@t=20s",
        "lane:fast:crash@t=15s,for=5s",
        "sink:stall@t=40s,for=3s",
        "sink:crash@t=1.5s",
        "lane:slow:hang@t=0s",
    ):
        spec = parse_spec(text)
        assert spec.canonical() == text
        assert parse_spec(spec.canonical()).canonical() == text


def test_active_window():
    s = parse_spec("sink:stall@t=10s,for=2s")
    assert not s.active(9.99) and s.active(10.0) and s.active(11.99) and not s.active(12.0)
    forever = parse_spec("lane:accurate:hang@t=10s")
    assert forever.active(1e6) and not forever.active(9.0)


@pytest.mark.parametrize(
    "text",
    [
        "lane:accurate:hang",  # no @t
        "lane:accurate:hang@",
        "lane:accurate:hang@for=2s",  # t required
        "lane:medium:hang@t=1s",  # unknown lane
        "lane:accurate:freeze@t=1s",  # unknown mode
        "lane:accurate@t=1s",  # lane needs a mode
        "sink:hang@t=1s",  # lane mode on a sink
        "sink:stall:extra@t=1s",
        "disk:stall@t=1s",  # unknown target
        "lane:fast:crash@t=1s,for=0s",  # for must be positive
        "lane:fast:crash@t=1s,x=2",  # unknown parameter
        "lane:fast:crash@t",  # not key=value
        "lane:fast:crash@t=soon",
    ],
)
def test_parse_spec_rejects(text):
    with pytest.raises(FaultSpecError):
        parse_spec(text)


def test_multiple_specs_and_empty_values():
    specs = parse_faults("lane:fast:crash@t=15s,for=5s; sink:stall@t=40s ;;")
    assert [s.kind for s in specs] == ["lane", "sink"]
    assert parse_faults(None) == [] and parse_faults("") == [] and parse_faults("  ") == []
    assert format_env(specs) == "lane:fast:crash@t=15s,for=5s;sink:stall@t=40s"
    assert format_env(["lane:accurate:hang@t=20s"]) == "lane:accurate:hang@t=20s"
    assert faults_from_env({"SS_FAULT": "sink:stall@t=1s"})[0].mode == "stall"
    assert faults_from_env({}) == []


class FakeClock:
    def __init__(self, t: float = 100.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_injector_is_a_no_op_without_faults():
    inj = FaultInjector.from_env("vision", {})
    assert not inj.enabled
    inj.start()
    assert inj.check("lane", "accurate") is None
    assert inj.check("sink") is None


def test_injector_timeline_and_log(tmp_path):
    log = tmp_path / "fault.jsonl"
    clock = FakeClock(100.0)
    inj = FaultInjector(
        parse_faults("lane:accurate:hang@t=20s;sink:stall@t=5s,for=2s"), "vision", str(log), clock
    )
    assert inj.check("lane", "accurate") is None  # t=0 not set yet: nothing fires before the first frame
    inj.start(first_frame_arrival=100.0)
    inj.start(first_frame_arrival=999.0)  # ignored
    clock.t = 119.0
    assert inj.check("lane", "accurate") is None
    assert inj.check("lane", "fast") is None  # other lane unaffected
    clock.t = 120.5
    assert inj.check("lane", "accurate") == "hang"
    assert inj.check("lane", "accurate") == "hang"  # still active, logged once
    assert inj.check("lane", "fast") is None
    clock.t = 105.5
    assert inj.check("sink") == "stall"
    clock.t = 108.0
    assert inj.check("sink") is None  # for=2 s ended: cleared
    records = read_fault_log(log)
    events = [(r["event"], r["spec"]) for r in records]
    assert events.count(("fault_active", "lane:accurate:hang@t=20s")) == 1
    assert ("fault_active", "sink:stall@t=5s,for=2s") in events
    assert ("fault_cleared", "sink:stall@t=5s,for=2s") in events
    # the recorded instant is the defined edge on the shared clock, not the moment a hook noticed it
    assert activation_time(records, "lane:accurate:hang@t=20s") == pytest.approx(120.0)
    assert activation_time(records, parse_spec("sink:stall@t=5s,for=2s")) == pytest.approx(105.0)
    assert activation_time(records, "sink:crash@t=1s") is None
    assert all(r["proc"] == "vision" for r in records)


def test_log_survives_garbage_and_missing_file(tmp_path):
    assert read_fault_log(tmp_path / "none.jsonl") == []
    p = tmp_path / "f.jsonl"
    p.write_text(
        "\n".join(
            [
                "not json",
                json.dumps({"event": "other"}),
                json.dumps({"event": "fault_active", "spec": "x", "t_monotonic": 3.0}),
            ]
        ),
        encoding="utf-8",
    )
    assert [r["event"] for r in read_fault_log(p)] == ["fault_active"]
    assert (
        activation_time(
            [
                {"event": "fault_active", "spec": "a", "t_monotonic": 9.0},
                {"event": "fault_active", "spec": "a", "t_monotonic": 7.0},
            ],
            "a",
        )
        == 7.0
    )


def test_hooks_present_finds_ss_fault_in_non_test_files(tmp_path):
    (tmp_path / "engine" / "tests").mkdir(parents=True)
    (tmp_path / "vision").mkdir()
    (tmp_path / "relay").mkdir()
    (tmp_path / "engine" / "tests" / "t.py").write_text("SS_FAULT", encoding="utf-8")  # tests do not count
    (tmp_path / "vision" / "lanes.py").write_text("os.environ.get('SS_FAULT')", encoding="utf-8")
    found = hooks_present(tmp_path)
    assert found == {"engine": False, "vision": True, "relay": False}
    assert hooks_present(tmp_path, ("missing",)) == {"missing": False}


def test_module_is_stdlib_only():
    """The owners import or copy this file: it must not pull in anything but the standard library."""
    src = Path(fault.__file__).read_text(encoding="utf-8")
    imports = {
        line.split()[1].split(".")[0] for line in src.splitlines() if line.startswith(("import ", "from "))
    }
    assert imports <= {
        "__future__",
        "json",
        "os",
        "re",
        "threading",
        "time",
        "collections",
        "dataclasses",
        "pathlib",
    }


# ---------------------------------------------------------------- hooks the other owners must honour

_HOOK_REASON = (
    "{mod}: no SS_FAULT hook yet (protocol in eval/fault.py). Needed: {need}. "
    "The failsafe gate leg exits 2 (environment) until it exists."
)


@pytest.mark.xfail(
    not hooks_present(ROOT, ("vision",))["vision"],
    reason=_HOOK_REASON.format(
        mod="vision/lanes.py",
        need="lane:<fast|accurate>:hang blocks the worker before the detector, :crash raises per run",
    ),
    strict=False,
)
def test_vision_honours_ss_fault():
    assert hooks_present(ROOT, ("vision",))["vision"]


@pytest.mark.xfail(
    not hooks_present(ROOT, ("relay",))["relay"],
    reason=_HOOK_REASON.format(
        mod="relay (egress sink, T1.2)",
        need="sink:stall stops draining and FrameSink.healthy=False, sink:crash kills the encoder once",
    ),
    strict=False,
)
def test_relay_honours_ss_fault():
    assert hooks_present(ROOT, ("relay",))["relay"]
