"""Metrics on toy data, timing JSONL, report writer (spec 9 tests 5 and 6)."""

import json
import math

import numpy as np
import pytest

from eval.harness import metrics, report, timing


def test_wilson_interval_known_values():
    lo, hi = metrics.wilson_interval(98, 100)
    assert lo == pytest.approx(0.9300, abs=1e-3) and hi == pytest.approx(0.9945, abs=1e-3)
    assert metrics.wilson_interval(0, 0) == (0.0, 1.0)
    lo, hi = metrics.wilson_interval(100, 100)
    assert hi == pytest.approx(1.0) and lo == pytest.approx(0.9630, abs=1e-3)
    lo, hi = metrics.wilson_interval(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=1e-3)


def test_frame_recall_excludes_warmup_and_invisible():
    frames = (
        [(1.0, True, True)] * 10
        + [(6.0, False, True)] * 10
        + [(7.0, True, False)] * 95
        + [(8.0, True, True)] * 5
    )
    r = metrics.frame_recall(frames, warmup_s=5.0)
    assert (r.n, r.k, r.value) == (100, 95, 0.95)
    assert r.ci_lo < 0.95 < r.ci_hi
    assert metrics.frame_recall([]).value is None


def test_precision_recall_helpers():
    assert metrics.precision(9, 1) == 0.9
    assert metrics.recall(9, 1) == 0.9
    assert metrics.precision(0, 0) is None
    masks = [(0, 0, 10, 10), (100, 100, 110, 110), (50, 50, 60, 60)]
    truths = [(0, 0, 10, 10), (51, 51, 61, 61)]
    p = metrics.mask_precision(masks, truths)
    assert (p.k, p.n) == (2, 3)
    assert metrics.iou_xyxy((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(1 / 3)


def test_visible_target_rule():
    assert metrics.is_visible_target([(10, 10), (110, 10), (110, 60), (10, 60)])
    assert not metrics.is_visible_target([(-90, 10), (10, 10), (10, 60), (-90, 60)])  # 10 % inside
    assert metrics.is_visible_target([(-30, 10), (70, 10), (70, 60), (-30, 60)])  # 70 % inside
    assert not metrics.is_visible_target([(10, 10), (30, 10), (30, 60), (10, 60)])  # 20 px side
    assert metrics.quad_inside_fraction([(0, 0), (1280, 0), (1280, 720), (0, 720)]) == pytest.approx(1.0)
    rot = [(100, 0), (200, 100), (100, 200), (0, 100)]
    assert metrics.polygon_area(rot) == pytest.approx(20000)


def test_covered_fraction():
    assert metrics.covered_fraction((0, 0, 10, 10), [(0, 0, 10, 5)]) == 0.5
    assert metrics.covered_fraction((0, 0, 10, 10), [(0, 0, 10, 5), (0, 4, 10, 10)]) == 1.0
    assert metrics.covered_fraction((0, 0, 10, 10), []) == 0.0


def test_flicker_fraction():
    steady = [True] * 90
    assert metrics.flicker_fraction(steady, [True] * 90) == (3, 0, 0.0)
    flick = [True] * 10 + [False] * 5 + [True] * 15 + [True] * 60
    w, f, frac = metrics.flicker_fraction(flick, [True] * 90)
    assert (w, f) == (3, 1) and frac == pytest.approx(1 / 3)
    # windows where the target is not visible do not count
    vis = [True] * 30 + [False] * 30 + [True] * 30
    assert metrics.flicker_fraction(flick, vis)[0] == 2
    # off at the start of a window (mask not yet on) is not a flicker
    assert metrics.flicker_fraction([False] * 5 + [True] * 25, [True] * 30)[1] == 0


def test_mute_coverage_and_over_mute_exact():
    hop = 0.01
    env = np.full(200, -20.0)  # 2 s of audible audio
    env[50:100] = -80.0  # 0.5 s .. 1.0 s fully muted
    env[150:160] = -80.0  # 1.5 s .. 1.6 s muted
    words = [(0.5, 1.0), (1.5, 1.7), (0.0, 0.3)]  # fully muted, half muted, never muted
    mc = metrics.mute_coverage(words, env, hop)
    assert mc.n_words == 3 and mc.n_covered == 1
    assert mc.coverage == pytest.approx(0.5 / 1.0)  # covered 0.5 s of 1.0 s total
    # a word muted for 90 % of its interval counts as covered
    env2 = np.full(100, -80.0)
    env2[0:10] = -20.0
    assert metrics.mute_coverage([(0.0, 1.0)], env2, hop).n_covered == 1
    env2[0:11] = -20.0
    assert metrics.mute_coverage([(0.0, 1.0)], env2, hop).n_covered == 0
    # over-mute: 0.1 s muted inside 1.0 s of non-PII speech
    assert metrics.over_mute([(1.5, 2.0), (0.0, 0.5)], env, hop) == pytest.approx(0.1 / 1.0)
    assert metrics.over_mute([], env, hop) is None


def test_rms_envelope_levels():
    sr = 16000
    t = np.arange(sr) / sr
    tone = 0.5 * np.sin(2 * math.pi * 440 * t)
    env = metrics.rms_dbfs_envelope(tone, sr, 0.01)
    assert len(env) == 100 and abs(env.mean() - (20 * math.log10(0.5 / math.sqrt(2)))) < 0.1
    assert metrics.rms_dbfs_envelope(np.zeros(sr), sr)[0] == pytest.approx(-120.0)


def test_delay_percentiles_and_continuity():
    d = metrics.delay_percentiles([1, 2, 3, 4, 5])
    assert d == {"n": 5, "p50_s": 3.0, "p95_s": 4.8, "max_s": 5.0}
    assert metrics.delay_percentiles([])["p50_s"] is None
    c = metrics.counter_continuity([10, 11, 12, 14, 14, 15])
    assert (c["expected"], c["received"], c["missing"]) == (6, 5, 1)
    g = metrics.capture_gaps([0, 33, 66, 200, 233], 1 << 31)
    assert g == {"max_gap_ms": 134.0, "n_over_100ms": 1}
    assert metrics.capture_gaps([(1 << 31) - 10, 23], 1 << 31)["max_gap_ms"] == 33.0  # wraps


def test_av_offsets_and_checks():
    o = metrics.av_offsets_ms([1.002, 2.001, 3.5], [1.0, 2.0, 3.0])
    assert o["n"] == 3 and o["max_abs_ms"] == pytest.approx(500.0)
    o = metrics.av_offsets_ms([1.002, 2.0005], [1.0, 2.0])
    assert o["max_abs_ms"] == pytest.approx(2.0, abs=0.01)
    assert (
        metrics.check(5.0, "<=", 5.0)
        and not metrics.check(5.1, "<=", 5.0)
        and not metrics.check(None, ">=", 0)
    )
    assert metrics.regression(5.3, 5.0, 0.2) and not metrics.regression(5.1, 5.0, 0.2)


def test_timing_delay_equals_injected(tmp_path):
    p = tmp_path / "timing.jsonl"
    with timing.TimingWriter(p) as w:
        w.header("r1", "dev", "abc", "2026-11-20T00:00:00Z", {"cpu": "x"}, 1234)
        for seq in range(20):
            t = 50.0 + seq / 30
            w.timing("ingest_decode", seq, seq / 30, t, t + 0.003, "engine")
            w.timing("compose", seq, seq / 30, t + 4.0, t + 4.01, "engine")
            w.timing("egress", seq, seq / 30, t + 4.9, t + 5.0, "engine")
        w.event("hold", 51.0)
    log = timing.read_timing(p)
    assert log.header["run_id"] == "r1" and len(log.events) == 1
    delays = timing.end_to_end_delays(log)
    assert len(delays) == 20 and all(d == pytest.approx(5.0) for d in delays)
    lat = timing.stage_latencies(log)
    assert lat["compose"]["p50_ms"] == pytest.approx(10.0, abs=0.01) and lat["egress"]["n"] == 20


def test_timing_rejects_unknown_stage_and_extra_keys(tmp_path):
    p = tmp_path / "t.jsonl"
    with timing.TimingWriter(p) as w:
        with pytest.raises(timing.TimingFormatError):
            w.timing("nope", 1, None, 0.0, 1.0, "x")
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        json.dumps(
            {
                "type": "timing",
                "stage": "stt",
                "seq": 1,
                "pts": None,
                "t_start": 0,
                "t_end": 1,
                "proc": "a",
                "text": "hello",
            }
        )
        + "\n"
    )
    with pytest.raises(timing.TimingFormatError):
        timing.read_timing(bad)


def test_stage_timing_contract_object(tmp_path):
    from contracts import StageTiming

    p = tmp_path / "c.jsonl"
    with timing.TimingWriter(p) as w:
        w.stage_timing(StageTiming(stage="egress", seq=3, pts=0.1, t_start=1.0, t_end=1.5), "engine")
    assert timing.read_timing(p).timings[0]["seq"] == 3


def _make_report():
    r = report.Report(
        "phaseX", "2026-11-20", {"seed": 1234, "git_rev": "abc", "hardware": {"cpu": "c", "gpu": "g"}}
    )
    r.checks += [report.Check("delay p95", 4.2, "<=", 5.0, "s"), report.Check("recall", 0.97, ">=", 0.98)]
    r.tables.append(report.Table("Latency", ["stage", "p50 ms"], [["egress", 1.5], ["a|b", None]]))
    r.failing_ids = ["case_1"]
    r.baseline = {"delay_p95": 4.0}
    return r


def test_report_writer_md_json_stable(tmp_path):
    r = _make_report()
    md, js = report.write_report(r, tmp_path)
    assert md.name == "2026-11-20-phaseX.md" and js.name == "2026-11-20-phaseX.json"
    first = (md.read_bytes(), js.read_bytes())
    report.write_report(_make_report(), tmp_path)
    assert (md.read_bytes(), js.read_bytes()) == first
    data = json.loads(js.read_text())
    assert data["passed"] is False and data["failing_ids"] == ["case_1"]
    assert [c["passed"] for c in data["checks"]] == [True, False]
    text = md.read_text()
    assert "FAIL" in text and "case_1" in text and "a\\|b" in text
    assert list(json.loads(js.read_text()).keys()) == sorted(data.keys())


def test_report_name_must_be_safe(tmp_path):
    with pytest.raises(ValueError):
        report.write_report(report.Report("../x", "2026-01-01"), tmp_path)


def test_git_rev_reads_head_without_git(tmp_path):
    (tmp_path / ".git" / "refs" / "heads").mkdir(parents=True)
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / ".git" / "refs" / "heads" / "main").write_text("0123456789abcdef0123\n")
    assert report.git_rev(tmp_path) == "0123456789ab"
    assert report.git_rev(tmp_path / "nowhere") == "unknown"
