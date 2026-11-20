"""Delay maths on a synthetic tap, StageTiming JSONL, engine-log alignment, card statistics (T1.4)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from eval.delay import (
    EngineFrame,
    TapFrame,
    align_to_engine,
    delay_stats,
    egress_delays,
    engine_frames,
    fps_stats,
    gap_stats,
    live_frames,
    read_engine_timing,
    write_tap_timing,
)
from eval.harness.timing import read_timing
from eval.phase1 import analyse_main
from eval.tap import card_stats, stamp_stats

FPS = 30.0
T0 = 1000.0  # monotonic read time of the first frame


def synth_tap(
    seconds: float = 20.0,
    delay: float = 4.3,
    jitter: float = 0.0,
    card_every: bool = True,
    fps: float = FPS,
) -> list[TapFrame]:
    """One live frame per 1/fps, captured `delay` before it is read (a constant-delay tap)."""
    frames = []
    n = int(seconds * fps)
    for i in range(n):
        t_read = T0 + delay + i / fps
        d = delay + (jitter if i % 20 == 0 else 0.0)
        tf = TapFrame(i, t_read, "live", d, i / fps, "card_static")
        if card_every:
            tf.card_visible = True
            tf.leaked = False
            tf.similarity = 0.02
        frames.append(tf)
    return frames


# ---------------------------------------------------------------- delay math


def test_delay_equals_injected_delay():
    frames = synth_tap(delay=4.3)
    d = delay_stats(frames)
    assert d["p50_s"] == pytest.approx(4.3, abs=1e-3)
    assert d["p95_s"] == pytest.approx(4.3, abs=1e-3)
    assert d["max_s"] == pytest.approx(4.3, abs=1e-3)
    assert d["n"] > 0


def test_delay_percentiles_with_spikes():
    # every 20th frame is 0.9 s later: 5 % of frames, so p95 sits at the spike boundary and max shows it
    frames = synth_tap(seconds=40, delay=4.0, jitter=0.9)
    d = delay_stats(frames)
    assert d["p50_s"] == pytest.approx(4.0, abs=1e-3)
    assert d["max_s"] == pytest.approx(4.9, abs=1e-3)
    assert 4.0 <= d["p95_s"] <= 4.9


def test_warmup_drops_the_first_seconds():
    frames = synth_tap(seconds=5)
    frames[0].delay_s = 9.0  # a startup outlier at the very first frame
    assert delay_stats(frames, warmup_s=0.0)["max_s"] == pytest.approx(9.0)
    assert delay_stats(frames, warmup_s=1.0)["max_s"] == pytest.approx(4.3, abs=1e-3)


def test_delay_stats_empty():
    assert delay_stats([])["p95_s"] is None
    only_slates = [TapFrame(None, T0 + i / FPS, "slate") for i in range(10)]
    assert delay_stats(only_slates)["n"] == 0
    assert live_frames(only_slates) == []


def test_delay_from_stamp_survives_modulus_wrap():
    """delay = (t_read_ms - stamp_ms) mod 2**31 (done by the tap): a wrapped stamp still gives a small delay."""
    from eval.sender.stamp import MS_MODULUS

    capture_ms = MS_MODULUS - 1000  # one second before the wrap
    t_read_ms = MS_MODULUS + 3300  # 4.3 s later, past the wrap
    assert ((t_read_ms) - capture_ms) % MS_MODULUS / 1000.0 == pytest.approx(4.3)


def test_fps_and_gaps():
    frames = synth_tap(seconds=30)
    fps = fps_stats(frames)
    assert fps["fps"] == pytest.approx(30.0, abs=0.1)
    assert gap_stats(frames)["n_over"] == 0
    stalled = [f for f in frames if not (400 <= f.counter < 440)]  # a 1.3 s hole
    g = gap_stats(stalled)
    assert g["n_over"] == 1 and g["max_gap_s"] == pytest.approx(41 / 30.0, abs=0.01)
    assert fps_stats(stalled)["fps"] < 29.0


def test_fps_ignores_trailing_end_of_stream_slates():
    frames = synth_tap(seconds=10)
    last = frames[-1].t_read
    frames += [TapFrame(None, last + (i + 1) / 5.0, "slate") for i in range(5)]  # sender stopped, slow slates
    assert fps_stats(frames)["fps"] == pytest.approx(30.0, abs=0.2)
    assert fps_stats([])["fps"] is None


# ---------------------------------------------------------------- StageTiming JSONL


def test_tap_timing_jsonl_is_stagetiming_compatible(tmp_path):
    frames = synth_tap(seconds=2, delay=4.2)
    frames.append(TapFrame(None, T0 + 10, "slate"))  # slates carry no capture time and are skipped
    path = tmp_path / "tap.jsonl"
    n = write_tap_timing(
        path,
        frames,
        "run1",
        "dev",
        "abc123",
        "2026-11-20T00:00:00+0000",
        {"cpu": "x"},
        1234,
        [("hold_sent", T0 + 1.0, None)],
    )
    assert n == 60
    log = read_timing(path)
    assert log.header["profile"] == "dev" and log.header["seed"] == 1234
    assert len(log.timings) == 60
    t = log.timings[10]
    assert t["stage"] == "egress" and t["seq"] == 10 and t["proc"] == "eval-tap"
    assert t["t_end"] - t["t_start"] == pytest.approx(4.2, abs=1e-6)  # capture to tap read
    assert [e["name"] for e in log.events] == ["hold_sent"]
    # no image, audio or text anywhere
    assert set(json.loads(path.read_text().splitlines()[1])) == {
        "type",
        "stage",
        "seq",
        "pts",
        "t_start",
        "t_end",
        "proc",
    }


# ---------------------------------------------------------------- engine timing log


def _engine_log(path, frames, ingest=0.15, emit_after=4.1, extra_lines=()):
    lines = []
    for f in frames:
        capture = f.t_read - f.delay_s
        arrival = capture + ingest
        emitted = arrival + emit_after
        lines.append(
            {
                "stage": "engine_delay",
                "seq": f.counter,
                "pts": f.pts_s,
                "t_start": arrival,
                "t_end": emitted,
                "ms": 4100.0,
            }
        )
    path.write_text("\n".join(json.dumps(x) for x in [*lines, *extra_lines]) + "\n", encoding="utf-8")


def test_engine_timing_is_read_leniently(tmp_path):
    p = tmp_path / "engine.jsonl"
    p.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "stage": "engine_delay",
                        "seq": 1,
                        "pts": 0.03,
                        "t_start": 1.0,
                        "t_end": 5.0,
                        "ms": 4000.0,
                    }
                ),
                json.dumps({"stage": "not_a_stage", "seq": 1, "t_start": 1.0, "t_end": 2.0}),
                json.dumps({"stage": "compose", "seq": 2}),  # missing times
                "not json",
                '{"stage": "egress", "seq": 3, "t_start"',  # half-written last line
            ]
        ),
        encoding="utf-8",
    )
    log = read_engine_timing(p)
    assert [t["stage"] for t in log.timings] == ["engine_delay"]
    assert engine_frames(log)[1] == EngineFrame(1, 1.0, 5.0)
    assert read_engine_timing(tmp_path / "missing.jsonl").timings == []


def test_alignment_through_pts_checks_the_mapping(tmp_path):
    frames = synth_tap(seconds=10, delay=4.4)
    p = tmp_path / "engine.jsonl"
    _engine_log(p, frames, ingest=0.15)
    log = read_engine_timing(p)
    info = align_to_engine(frames, engine_frames(log))
    assert info["valid"] and info["matched"] == len(frames)
    assert info["ingest_s_median"] == pytest.approx(0.15, abs=1e-3)
    assert frames[5].extra["engine_emitted"] == pytest.approx(
        frames[5].t_read - frames[5].delay_s + 0.15 + 4.1
    )
    # a shifted mapping (wrong seq base) is rejected because the implied ingest latency is not steady
    shifted = [TapFrame(f.counter, f.t_read, "live", f.delay_s, (f.pts_s or 0.0) + 2.0) for f in frames]
    bad = align_to_engine(shifted, engine_frames(log))
    assert not bad["valid"] or abs(bad["ingest_s_median"] - 0.15) > 0.5


def test_egress_socket_delay_from_the_relay_stage(tmp_path):
    frames = synth_tap(seconds=10, delay=4.4)
    lines = [
        {
            "stage": "egress_write",
            "seq": f.counter,
            "pts": f.pts_s,
            "t_start": f.t_read - 0.2,
            "t_end": f.t_read - 0.05,
            "ms": 150.0,
        }
        for f in frames
    ]
    p = tmp_path / "engine.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    # "egress_write" is not a harness stage yet: the reader skips it and the egress-socket list stays empty
    delays = egress_delays(frames, read_engine_timing(p))
    from eval.harness.timing import STAGES

    if "egress_write" in STAGES:
        assert delays and max(delays) == pytest.approx(4.35, abs=1e-3)
    else:
        assert delays == []


# ---------------------------------------------------------------- card statistics and the main-leg checks


def test_card_stats_recall_and_warmup():
    frames = synth_tap(seconds=10)
    for f in frames[:20]:  # a leak inside the 1.0 s warm-up is reported but not scored
        f.leaked = True
    stats = card_stats(frames, warmup_s=1.0)
    assert stats["leaked_frames"] == 0 and stats["unreadable_recall"] == 1.0
    assert stats["leaked_in_warmup"] == 20
    for f in frames[100:104]:
        f.leaked = True
    stats = card_stats(frames, warmup_s=1.0)
    assert stats["leaked_frames"] == 4
    assert stats["unreadable_recall"] == pytest.approx(1 - 4 / stats["visible_frames"])
    assert stats["leaked_counters"] == [100, 101, 102, 103]


def test_card_stats_counts_valid_ocr_reads():
    frames = synth_tap(seconds=10)
    frames[200].ocr = {"valid_read": False, "exact_read": False}
    frames[201].ocr = {"valid_read": True, "exact_read": True}
    frames[201].leaked = True
    stats = card_stats(frames)
    assert stats["ocr_checked"] == 2 and stats["ocr_valid_reads"] == 1 and stats["ocr_exact_reads"] == 1


def test_card_stats_without_card_frames():
    assert card_stats([])["unreadable_recall"] is None
    nocard = synth_tap(seconds=3, card_every=False)
    assert card_stats(nocard)["visible_frames"] == 0


def _fake_result(frames, tmp_path, engine_exit=None, unreadable=0, av=0.0):
    decoded = len(frames)
    reader = SimpleNamespace(
        video_decoded=decoded,
        stamp_unreadable=unreadable,
        slate_frames=sum(1 for f in frames if f.kind == "slate"),
        summary=lambda: {"av_offset_ms": {"max_abs_ms": av, "n": 20}, "fps": 30.0},
    )
    tap = SimpleNamespace(frames=frames, reader=reader, audio=[], errors=[])
    return SimpleNamespace(
        tap=tap,
        engine_timing_path=tmp_path / "none.jsonl",
        engine_exit=engine_exit,
        cpu={"mean_cores": 1.0},
        vram_mb={},
        spec=SimpleNamespace(delay_s=4.0, engine="ref", script="x", duration_s=0.0),
        events=[],
        sender={},
    )


def test_analyse_main_passes_a_good_run(tmp_path):
    frames = synth_tap(seconds=60, delay=4.4)
    for i, f in enumerate(frames):
        if i % 90 == 0:
            f.ocr = {"valid_read": False, "exact_read": False}
    checks, m = analyse_main(_fake_result(frames, tmp_path), "cpu")
    failed = [c.name for c in checks if not c.passed]
    assert failed == []
    assert stamp_stats(_fake_result(frames, tmp_path).tap)["stamp_unreadable_frac"] == 0.0


def test_analyse_main_fails_on_each_criterion(tmp_path):
    def failed(frames, **kw):
        checks, _ = analyse_main(_fake_result(frames, tmp_path, **kw), "cpu")
        return [c.name for c in checks if not c.passed]

    def ocr(frames):
        for i, f in enumerate(frames):
            if i % 90 == 0:
                f.ocr = {"valid_read": False, "exact_read": False}
        return frames

    assert any("delay p95" in n for n in failed(ocr(synth_tap(seconds=60, delay=5.3))))
    leaky = ocr(synth_tap(seconds=60, delay=4.4))
    for f in leaky[300:420]:
        f.leaked = True
    names = failed(leaky)
    assert any("card unreadable" in n for n in names)
    valid = ocr(synth_tap(seconds=60, delay=4.4))
    valid[500].ocr = {"valid_read": True, "exact_read": True}
    assert any("Verhoeff-valid OCR" in n for n in failed(valid))
    assert any("A/V offset" in n for n in failed(ocr(synth_tap(seconds=60)), av=55.0))
    assert any("engine alive" in n for n in failed(ocr(synth_tap(seconds=60)), engine_exit=-11))
    assert any("stamp unreadable" in n for n in failed(ocr(synth_tap(seconds=60)), unreadable=120))
    slow = synth_tap(seconds=60, fps=24.0)
    assert any("fps" in n for n in failed(ocr(slow)))


# ---------------------------------------------------------------- real relay: encoder seq -> engine pts


def test_alignment_through_the_relay_encoder_numbering(tmp_path):
    """The relay encodes from seq 0 while the engine's first covered frame is seq 17: bridge them with egress_write."""
    from eval.delay import encoder_pts_map

    offset = 17
    engine_lines, relay_lines, frames = [], [], []
    for i in range(300):
        capture = T0 + (i + offset) / FPS
        arrival = capture + 0.3
        emitted = arrival + 4.0
        engine_lines.append(
            {
                "stage": "engine_delay",
                "seq": i + offset,
                "pts": (i + offset) / FPS,
                "t_start": arrival,
                "t_end": emitted,
            }
        )
        relay_lines.append(
            {
                "stage": "egress_write",
                "seq": i,
                "pts": (i + offset) / FPS,
                "t_start": emitted,
                "t_end": emitted + 0.15,
            }
        )
        # tap PTS is the encoder's own count from 0
        frames.append(
            TapFrame(i + offset, emitted + 0.2, "live", emitted + 0.2 - capture, i / FPS, "card_static")
        )
    p = tmp_path / "engine.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in engine_lines + relay_lines) + "\n", encoding="utf-8")
    log = read_engine_timing(p)
    # without the bridge the PTS mapping is wrong (the old behaviour on a real relay)
    assert not align_to_engine(frames, engine_frames(log))["valid"]
    info = align_to_engine(frames, engine_frames(log), enc_map=encoder_pts_map(log))
    assert info["valid"] and info["matched"] == 300
    assert info["ingest_s_median"] == pytest.approx(0.3, abs=1e-3)
    egress = egress_delays(frames, log)
    assert egress and max(egress) == pytest.approx(4.45, abs=1e-3)  # capture to the egress write


def test_an_impossible_stamp_is_counted_as_unreadable_not_as_a_delay():
    from eval.sender.stamp import Stamp
    from eval.tap import MAX_PLAUSIBLE_DELAY_S, Tap

    tap = Tap("rtmp://x/shielded", "card_absent:10", ocr=False)
    t_read = 400.0
    good = Stamp(counter=5, capture_ms=int((t_read - 4.3) * 1000), beep=False)
    bad = Stamp(counter=0, capture_ms=0, beep=False)  # decodes with a valid CRC but is 400 s old
    frame = SimpleNamespace(pts=1, time_base=1 / 30)
    tap._video_hook(frame, good, t_read)
    tap._video_hook(frame, bad, t_read + 0.033)
    assert tap.implausible == 1 and 400.0 > MAX_PLAUSIBLE_DELAY_S
    d = delay_stats(tap.frames, warmup_s=0.0)
    assert d["n"] == 1 and d["max_s"] == pytest.approx(4.3, abs=1e-2)
    res = SimpleNamespace(
        reader=SimpleNamespace(video_decoded=2, stamp_unreadable=0, slate_frames=0),
        implausible=tap.implausible,
    )
    assert stamp_stats(res)["stamp_unreadable_frac"] == 0.5


def test_card_stats_with_no_frames_has_every_key_the_gate_reads():
    # a run where nothing reached the tap must fail its criteria, not crash the analysis with a KeyError
    from eval.tap import card_stats

    s = card_stats([])
    for key in ("visible_frames", "unreadable_recall", "ocr_valid_reads", "ocr_checked", "leaked_frames"):
        assert key in s
    assert s["visible_frames"] == 0 and s["unreadable_recall"] is None
