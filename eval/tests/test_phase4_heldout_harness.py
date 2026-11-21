"""Gate 4 held-out leg: scoring, the hold-versus-hard split, the CLI wiring and the no-message-text rule.

Uses a tiny TOY set built here. These tests never read the private set (eval/data/private/chat_heldout_v2).
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from eval import phase4, phase4_heldout, phase4_report
from eval import run as eval_run
from eval.data_gen.chat.record import Msg

TOY = [
    Msg("toy abuse alpha one", "abuse", "k1", tags=["d:easy"], id="t01"),
    Msg("toy abuse bravo two", "abuse", "k1", tags=["d:hard"], id="t02"),
    Msg("toy abuse charlie three", "abuse", "k2", tags=["d:medium"], id="t03"),
    Msg("toy scam delta four", "scam", "k3", tags=["d:medium"], id="t04"),
    Msg("toy upi echo five", "fake_upi", "k4", tags=["d:hard"], id="t05"),
    Msg("toy dox foxtrot six", "doxxing", "k5", tags=["d:hard"], id="t06"),
    Msg("toy clean golf seven", "clean", "k6", tags=["d:easy"], id="t07"),
    Msg("toy clean hotel eight", "clean", "k6", tags=["d:hard"], id="t08"),
    Msg("toy clean india nine", "clean", "k7", tags=["d:easy"], id="t09", gate=False),
    Msg("toy staff juliet ten", "clean", "own", staff="broadcaster", id="t10"),
    Msg("toy modquote kilo eleven", "doxxing_staff", "mq", staff="moderator", id="t11"),
]
PRIVATE = {"terms": ["toy private term"], "phones": ["6000000000"]}


@pytest.fixture
def toy(tmp_path):
    for i, m in enumerate(TOY):
        m.author = f"a{i}"
    d = tmp_path / "toy_heldout"
    d.mkdir()
    manifest = {
        "generator_version": "toy",
        "seed": 1,
        "streamer_private": PRIVATE,
        "totals": {"messages": len(TOY)},
        "messages": [m.to_dict() for m in TOY],
    }
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def _verdict(action, *cats):
    return SimpleNamespace(action=action, categories=tuple(cats), latency_ms=1.0)


def test_profile_separates_holds_from_hard_flags():
    from contracts import Category, ChatAction

    final = {
        "t01": _verdict(ChatAction.HIDE_FROM_STREAMER, Category.CHAT_ABUSE),  # hard, right category
        "t02": _verdict(ChatAction.HOLD_FOR_MOD, Category.CHAT_ABUSE),  # held, right category
        "t03": _verdict(ChatAction.ALLOW),  # missed
        "t04": _verdict(ChatAction.HOLD_FOR_MOD),  # held, no category
        "t05": _verdict(ChatAction.DELETE, Category.CHAT_FAKE_UPI),
        "t06": _verdict(ChatAction.DELETE, Category.CHAT_ABUSE),  # hard flag, wrong category
        "t07": _verdict(ChatAction.HOLD_FOR_MOD),  # clean held
        "t08": _verdict(ChatAction.ALLOW),
        "t09": _verdict(ChatAction.DELETE),  # not gating, hard
        "t10": _verdict(ChatAction.ALLOW),
        "t11": _verdict(ChatAction.ALLOW, Category.CHAT_DOXXING),  # flagged, kept visible
    }
    run = {
        "final": final,
        "first": final,
        "e2e_ms": [2.0] * len(final),
        "missing": [],
        "allowed": {"t03", "t08", "t10", "t11"},
    }
    res = phase4_heldout.profile(TOY, run)
    ab = res["abuse"]
    assert (ab["recall"]["k"], ab["recall"]["n"]) == (
        2,
        3,
    )  # a hold with the right category counts as flagged
    assert (ab["recall_hard"]["k"], ab["recall_hard"]["n"]) == (1, 3)  # but not as a hard flag
    assert ab["held_with_right_category"] == 1 and ab["allowed_missed"] == 1
    assert res["scam"]["recall"]["k"] == 0 and res["scam"]["held_without_right_category"] == 1
    assert res["scam"]["withheld_any"] == 1  # held without the category: still withheld from the overlay
    assert res["doxxing"]["hard_flag_wrong_category"] == 1 and res["doxxing"]["recall"]["k"] == 0
    assert res["fake_upi"]["recall_hard"]["k"] == 1
    assert (res["clean"]["false_positive"]["k"], res["clean"]["n"]) == (1, 2)
    assert res["clean"]["false_positive_hard"]["k"] == 0 and res["clean"]["held"]["k"] == 1
    assert res["not_gating"] == {"n": 1, "flagged_hard": 1, "held": 0}
    assert res["staff"]["hidden"] == 0 and res["staff"]["moderator_dox_flagged"] == 1
    assert res["holds"]["total"] == 3 and res["holds"]["hard_flags_total"] == 4
    assert res["by_difficulty"]["pos/hard"]["n"] == 3
    assert res["by_kind"]["abuse/k1"]["recall_hard"] == 0.5
    ids = {k: v for k, v in phase4_heldout.failure_ids(TOY, {"final": final}).items()}  # ids only, never text
    assert ids["missed/abuse"] == ["t03"] and "t04" in ids["missed/scam"] and ids["clean_flagged"] == ["t07"]
    assert "toy" not in json.dumps(ids)


def test_reference_run_scores_perfectly_and_writes_no_message_text(toy, tmp_path):
    res = phase4_heldout.leg_heldout(toy, reference=True)
    for g in ("abuse", "scam", "fake_upi", "doxxing"):
        assert res[g]["recall"]["rate"] == 1.0, g
    assert res["clean"]["false_positive"]["k"] == 0 and res["staff"]["hidden"] == 0
    assert res["log_scan"]["leaks"] == 0 and res["unclassified"] == 0
    assert res["informational"] is True
    blob = json.dumps(res, default=str, ensure_ascii=False)
    assert all(m.text not in blob for m in TOY)
    phase4_heldout.write_private_failures(toy, res)
    assert "_failure_ids" not in res
    assert (toy / "last_run_failure_ids.json").exists()


def test_seeded_defect_is_visible_in_the_heldout_rows(toy):
    """A classifier that lets scams through must show up as missed scam and fake-UPI messages."""
    manifest, msgs = phase4_heldout.load(toy)
    run = asyncio.run(
        phase4.run_pipeline(msgs, manifest["streamer_private"], llm=None, defect="no_scam", reference=True)
    )
    res = phase4_heldout.profile(msgs, run)
    assert res["scam"]["recall"]["rate"] == 0.0 and res["fake_upi"]["recall"]["rate"] == 0.0
    assert res["abuse"]["recall"]["rate"] == 1.0


def test_cli_heldout_flag_and_run_forwarder(toy, tmp_path, capsys):
    out = tmp_path / "out"
    rc = phase4.main(["--heldout", "--heldout-data", str(toy), "--out", str(out), "--reference"])
    assert rc == phase4.EXIT_OK
    written = (out / "phase4-heldout.json").read_text(encoding="utf-8")
    assert all(m.text not in written for m in TOY) and "last_run_failure_ids" not in written
    assert all(m.text not in capsys.readouterr().out for m in TOY)
    out2 = tmp_path / "out2"
    assert (
        eval_run.main(["phase4", "--heldout", "--heldout-data", str(toy), "--out", str(out2), "--reference"])
        == 0
    )
    assert (out2 / "phase4-heldout.json").exists()


def test_cli_missing_private_set_is_an_env_error_not_a_crash(tmp_path):
    rc = phase4.main(["--heldout", "--heldout-data", str(tmp_path / "absent"), "--out", str(tmp_path / "o")])
    assert rc == phase4.EXIT_ENV


def test_report_shows_heldout_rows_as_informational_aggregates(toy, tmp_path):
    out = tmp_path / "out"
    assert phase4.main(["--heldout", "--heldout-data", str(toy), "--out", str(out), "--reference"]) == 0
    rc = phase4_report.build_report(out, None, "phase4-toy", tmp_path / "reports")
    assert rc == 0
    md = next((tmp_path / "reports").glob("*phase4-toy.md")).read_text(encoding="utf-8")
    assert "Held-out set v2, informational, NOT gating" in md and "HOLD_FOR_MOD apart from hard flags" in md
    assert all(m.text not in md for m in TOY)
    # the held-out rows are tables, not checks: they cannot turn a gate red or green
    assert "held-out" not in " ".join(
        c.name for c in phase4_report.evaluate(json.loads((out / "phase4-heldout.json").read_text()))
    )


def test_banter_split_counts_profanity_apart_from_mild_banter():
    from types import SimpleNamespace as NS

    from contracts import ChatAction

    msgs = [
        Msg("abe bsdk kahan tha", "clean", "ambiguous", gate=False, id="b1"),
        Msg("bc kya khela yaar", "clean", "banter_friends", id="b2"),
        Msg("abe pagal hai kya", "clean", "banter_friends", id="b3"),
        Msg("tu noob hai bhai", "clean", "sarcasm", id="b4"),
        Msg("bsdk abusive", "abuse", "plain_latin", id="b5"),
    ]
    acts = {
        "b1": ChatAction.HOLD_FOR_MOD,
        "b2": ChatAction.HIDE_FROM_STREAMER,
        "b3": ChatAction.ALLOW,
        "b4": ChatAction.HOLD_FOR_MOD,
    }
    oc = {
        m.id: phase4_heldout._Outcome(
            NS(action=acts[m.id], categories=()) if m.id in acts else None,
            ChatAction.ALLOW,
            ChatAction.HOLD_FOR_MOD,
            lambda v: set(),
        )
        for m in msgs
    }
    r = phase4_heldout.banter_split(msgs, oc)
    assert r["strong_profanity"] == {"n": 2, "flagged_any": 2, "hard": 1, "held": 1}
    assert r["mild"] == {"n": 2, "flagged_any": 1, "hard": 0, "held": 1}
