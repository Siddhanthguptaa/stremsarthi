"""Gate 4 harness: scoring on toy data, reference classifier validation, defects, log scan, criteria."""

import logging

import pytest

from eval import phase4, phase4_report
from eval.data_gen.chat.record import Msg
from eval.data_gen.chat.suite import build_suite, load


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    d = tmp_path_factory.mktemp("c4")
    build_suite(d, 1234)
    return d


def test_log_scan_counts_leaks_without_printing():
    cap = phase4.install_capture()
    try:
        logging.getLogger("x").warning("saw %s", "secret-text-here")
        logging.getLogger("x").info("fine")
    finally:
        logging.getLogger().removeHandler(cap)
    assert phase4.scan_logs(cap, ["secret-text-here", "abc"])["leaks"] == 1


def test_reference_classify_passes_every_criterion(data):
    res = phase4.leg_classify(data, False, "", True)
    assert all(c.passed for c in phase4_report.evaluate(res))
    assert (
        res["abuse"]["recall"] == 1.0
        and res["clean"]["false_positive"] == 0.0
        and res["staff"]["hidden"] == 0
    )
    assert res["staff_dox_flagged"]["flagged"] == res["staff_dox_flagged"]["n"]


@pytest.mark.parametrize("defect", phase4.DEFECTS)
def test_seeded_defects_fail_the_reference(data, defect):
    res = phase4.leg_classify(data, False, defect, True)
    assert any(not c.passed for c in phase4_report.evaluate(res)), defect


def test_real_classifier_run_is_measured_not_assumed(data):
    res = phase4.leg_classify(data, False, "", False)
    assert res["missing"] == 0 and res["abuse"]["n"] >= 500 and res["clean"]["n"] >= 600
    assert res["log_scan"]["leaks"] == 0 and res["staff"]["hidden"] == 0


def test_data_leg_and_problems(data, tmp_path):
    res, rc = phase4.leg_data(data)
    assert rc == phase4.EXIT_OK, res["problems"]
    _, rc = phase4.leg_data(tmp_path / "nope")
    assert rc == phase4.EXIT_ENV
    _, msgs = load(data)
    assert isinstance(msgs[0], Msg)


def test_evaluate_connector_checks_and_missing_checks():
    ok = phase4_report.evaluate(
        {"leg": "youtube", "checks": [{"name": "a", "value": 1.0, "op": ">=", "target": 1.0}]}
    )
    assert ok[0].passed
    empty = phase4_report.evaluate({"leg": "twitch", "checks": []})
    assert not empty[0].passed
