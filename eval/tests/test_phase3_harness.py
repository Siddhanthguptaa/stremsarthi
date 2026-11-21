"""Gate 3 harness: oracle recogniser, output-audio mute application, scoring, criteria and the report (tone data)."""

import asyncio
import json

import numpy as np
import pytest

from eval import phase3, phase3_report
from eval.data_gen.audio.suite import build_suite, load_manifest
from eval.data_gen.audio.tts_compose import SR


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    d = tmp_path_factory.mktemp("a3")
    build_suite(d, 1234, 150, "tone")
    return d


def test_pts_cover_unions_spans():
    spans = [{"t0": 0.0, "t1": 1.0}, {"t0": 0.8, "t1": 2.0}]
    assert phase3.pts_cover(spans, 0.5, 1.5) == pytest.approx(1.0)
    assert phase3.pts_cover(spans, 2.0, 3.0) == 0.0
    assert phase3.pts_cover(spans, 1.5, 2.5) == pytest.approx(0.5)


def test_oracle_fate_is_fixed_per_word_and_failures_raise(data):
    _, clips = load_manifest(data)
    words = clips[1].words
    a = phase3.OracleStt(words, "sarvam", phase3.SttModel(0.3, 0.2, (0.0, 0.0)), 5)
    b = phase3.OracleStt(words, "sarvam", phase3.SttModel(0.3, 0.2, (0.0, 0.0)), 5)
    assert [(t, x, y) for _, t, x, y in a.words] == [(t, x, y) for _, t, x, y in b.words]
    assert len(a.words) < len(words) and any(t == "kya" for _, t, _, _ in a.words)
    pcm = np.zeros(SR * 5, dtype=np.int16)
    tr = asyncio.run(phase3.OracleStt(words, "local", phase3.PERFECT, 1).transcribe(pcm, 0.0, None))
    assert len(tr.words) == len([w for w in words if (w.t0 + w.t1) / 2 < 5.0])
    with pytest.raises(RuntimeError):
        asyncio.run(
            phase3.OracleStt(words, "sarvam", phase3.PERFECT, 1, RuntimeError("x")).transcribe(pcm, 0.0, None)
        )


def test_output_audio_mutes_on_time_spans_and_leaks_late_ones():
    pcm = (np.sin(np.arange(SR * 4) * 0.3) * 8000).astype(np.int16)
    span = {"id": "s", "t0": 1.0, "t1": 2.0, "gran": "word", "cat": "spoken_phone"}
    out, late = phase3.output_audio(pcm, [{**span, "ready": 2.0}])
    env = phase3.metrics.rms_dbfs_envelope(out, phase3.OUT_RATE, 0.01)
    assert late == 0 and phase3.metrics.muted_fraction(env, 0.01, 1.05, 1.95) == 1.0
    assert phase3.metrics.muted_fraction(env, 0.01, 0.0, 0.9) == 0.0
    out, late = phase3.output_audio(pcm, [{**span, "ready": 1.0 + phase3.DELAY_S + 2.0}])
    env = phase3.metrics.rms_dbfs_envelope(out, phase3.OUT_RATE, 0.01)
    assert late == 1 and phase3.metrics.muted_fraction(env, 0.01, 1.05, 1.95) == 0.0


def test_reference_legs_pass_and_defects_fail(data):
    for leg in ("main", "api_down", "local_only"):
        res = phase3.run_leg(leg, data, 12, 1, 1234, "", True)
        assert all(c.passed for c in phase3_report.evaluate(res)), (leg, res["pii"])
    assert phase3.run_leg("main", data, 12, 1, 1234, "", True)["digit_sequences"]["rate"] == 1.0
    for leg, defect in (("main", "late_spans"), ("main", "no_mouth"), ("api_down", "no_failsafe")):
        res = phase3.run_leg(leg, data, 12, 1, 1234, defect, True)
        assert any(not c.passed for c in phase3_report.evaluate(res)), (leg, defect)


def test_data_leg_flags_tone_engine_and_missing_set(tmp_path, data):
    res, rc = phase3.leg_data(data, 150, 1234, False, 5)
    assert rc == phase3.EXIT_FAIL and any("not real TTS" in p for p in res["problems"])
    res, rc = phase3.leg_data(data, 150, 1234, True, 5)
    assert rc == phase3.EXIT_OK, res["problems"]
    _, rc = phase3.leg_data(tmp_path / "none", 150, 1234, True, 5)
    assert rc == phase3.EXIT_ENV


def test_report_marks_missing_legs_as_failures_not_passes(tmp_path):
    (tmp_path / "phase3-main.json").write_text(
        json.dumps(
            {
                "leg": "main",
                "pii": {"dur_coverage": 1.0},
                "digits": {"dur_coverage": 1.0},
                "over_mute": 0.0,
                "audio_latency_s": {"p95": 1.0, "n": 1, "p50": 1, "max": 1},
                "mouth_blur": {"rate": 1.0},
                "by": {k: {} for k in ("lang", "kind", "cue", "bucket", "noise")},
                "stage_s": {},
                "digit_sequences": {"complete": 1, "total": 1},
                "hard_negative_mute": 0.0,
                "late_or_partly_late_spans": 0,
                "leaked_seconds_in_pipeline": 0,
                "compute_s_per_audio_s": 0.01,
                "pipeline_stats": {},
                "worst_clips": [],
                "n_clips": 1,
                "audio_s": 1,
                "vad": ["silero"],
            }
        )
    )
    assert phase3_report.build_report(tmp_path, None, "phase3-test", tmp_path) == 0
    js = json.loads(next(tmp_path.glob("*-phase3-test.json")).read_text())
    assert js["passed"] is False
    assert any("not run" in c["note"] for c in js["checks"])
