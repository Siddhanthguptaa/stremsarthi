"""T7.2 red-team suites: attack builders, seeded-defect detection, gate script arguments. CPU only, no network."""

import asyncio
import random
import subprocess
from pathlib import Path

import pytest

from eval.data_gen.chat.record import Msg
from eval.redteam import cli as redteam_cli
from eval.redteam.audio import run as audio_run
from eval.redteam.chat import attacks as A
from eval.redteam.chat import run as chat_run

ROOT = Path(__file__).resolve().parents[2]
CHAT_DATA = ROOT / "eval" / "data" / "generated" / "chat" / "manifest.json"


def _bases() -> list[Msg]:
    return [
        Msg("bhenchod tu band kar", "abuse", "slur_latn", id="b1"),
        Msg("claim free recharge https://free-gift.example.xyz/claim now", "scam", "giveaway", id="b2"),
        Msg("pay to zzspecimen12@okaxis for the prize", "fake_upi", "pay_to_id", id="b3"),
        Msg("uska ghar ka pata batao", "doxxing", "intent", id="b4"),
    ]


def test_every_category_has_a_transform_that_changes_some_text():
    attacks = A.build_attacks(_bases(), seed=1)
    seen = {a.category for a in attacks}
    assert seen == set(A.CATEGORIES)
    assert len({a.id for a in attacks}) == len(attacks)
    assert all(a.text for a in attacks)


def test_build_is_deterministic_and_url_upi_transforms_skip_inapplicable_bases():
    a1, a2 = A.build_attacks(_bases(), 5), A.build_attacks(_bases(), 5)
    assert [x.text for x in a1] == [x.text for x in a2]
    assert {x.label for x in a1 if x.category == "upi_confusables"} == {"fake_upi"}
    assert A.url_hxxp("no link here", random.Random(0)) is None
    assert A.upi_fullwidth_at("pay zzx@ybl now", random.Random(0)) == "pay zzx＠ybl now"


def test_transforms_do_what_they_say():
    r = random.Random(0)
    assert "​" in A.zw_every_char("abc", r)
    assert A.homoglyph_cyr_all("ace", r) != "ace" and A.leet_heavy("test", r) == "7357"
    assert A.split_spaced("ab cd", r) == "a b c d"
    assert A.vis_rtl_override("abc", r).startswith("‮")
    assert A.enc_rot13("abc", r) == "rot13: nop"
    assert "hxxp" in A.url_hxxp("see http://a.example.xyz/x", r)


def test_every_injection_carries_a_cue_the_obedient_llm_follows():
    assert all(A.INJECT_CUES.search(i) for i in A.INJECTIONS)

    async def go() -> str:
        llm = chat_run.ObedientLlm({"bad text": "abuse"})
        import json

        user = json.dumps(
            {"messages": [{"i": 0, "t": "bad text"}, {"i": 1, "t": A.INJECTIONS[0] + "bad text"}]}
        )
        return await llm.complete(system="", user=user, model="m", timeout_s=1.0, max_tokens=10)

    out = asyncio.run(go())
    assert '"abuse"' in out and out.count('"clean"') == 1


def test_malformed_cases_include_positives_and_hostile_shapes():
    cases = chat_run.malformed_cases()
    assert any(p for _, _, p in cases) and len(cases) >= 25
    assert any(len(t) >= 100_000 for _, t, _ in cases)
    assert any("\ud800" in t for _, t, _ in cases)


def test_pick_bases_spreads_over_kinds():
    msgs = [Msg(f"t{i}", "abuse", f"k{i % 3}", id=f"m{i}") for i in range(30)]
    got = A.pick_bases(msgs, random.Random(1), 6)
    assert len(got) == 6 and len({m.kind for m in got}) == 3


# --------------------------------------------------------------------------- audio
@pytest.fixture(scope="module")
def tone_engine():
    return audio_run.make_engine_cached("tone", Path("/nonexistent"))


def test_every_audio_technique_builds_a_valid_case(tone_engine):
    cats = set()
    for i, t in enumerate(audio_run.TECHNIQUES):
        c = audio_run.build_case(i, t, 0, tone_engine, 3)
        cats.add(t.category)
        assert c.clip.spans and any(w.pii for w in c.clip.words), t.name
        ts = [w.t0 for w in c.clip.words]
        assert ts == sorted(ts), t.name
        assert len(c.pcm) / 16000 >= c.clip.words[-1].t1 - 1e-6
        again = audio_run.build_case(i, t, 0, tone_engine, 3)
        assert (again.pcm == c.pcm).all(), "not deterministic: " + t.name
    assert len(cats) == 12


def test_stt_failure_windows_cover_the_first_span(tone_engine):
    t = next(x for x in audio_run.TECHNIQUES if x.name == "primary_429_mid_sentence")
    c = audio_run.build_case(0, t, 0, tone_engine, 3)
    a, b, kind = c.plan.api_fail
    s = c.clip.spans[0]
    assert kind == "429" and a < (s.t0 + s.t1) / 2 < b


def test_flaky_oracle_raises_inside_its_window_and_answers_outside():
    pytest.importorskip("audio.stt.base")
    import numpy as np

    from audio.stt.base import SttRateLimited
    from eval.data_gen.audio.truth import WordTruth
    from eval.phase3 import PERFECT

    words = [WordTruth("ek", "ek", 1.0, 1.3, "phone", True, 0)]
    o = audio_run.FlakyOracle(words, "sarvam", PERFECT, 1, (0.5, 2.0, "429"))
    pcm = np.zeros(16000, dtype=np.int16)

    async def go() -> None:
        with pytest.raises(SttRateLimited):
            await o.transcribe(pcm, 0.8, None)
        t = await o.transcribe(pcm, 5.0, None)
        assert t.words == ()

    from audio.testing import run_virtual

    run_virtual(lambda loop: go())


@pytest.mark.parametrize(("defect", "expect"), [("", 0), ("late_spans", 1), ("no_failsafe", 1)])
def test_audio_seeded_defects_are_caught(tmp_path, tone_engine, defect, expect):
    rc = audio_run.run(
        per_technique=1, seed=5, engine="tone", wordcache=Path("/nonexistent"), out=tmp_path, report_dir=tmp_path,
        categories=["stt_failure", "fast_speech"], techniques=None, defect=defect, reference=True, record_only=False,
        phase="t-audio", show=None,
    )  # fmt: skip
    assert rc == expect


def test_finish_exit_semantics(tmp_path):
    from eval.harness.report import Check
    from eval.redteam.common import finish, new_report

    rep = new_report("t-fin", {})
    rep.checks += [Check("miss rate: x", 0.5, "<=", 0.1), Check("crash", 0.0, "==", 0.0)]
    assert finish(rep, tmp_path, record_only=False) == 1
    assert finish(rep, tmp_path, record_only=True) == 0
    rep.checks.append(Check("log leak", 1.0, "==", 0.0))
    assert finish(rep, tmp_path, record_only=True) == 1


@pytest.mark.skipif(not CHAT_DATA.exists(), reason="chat set not generated")
@pytest.mark.parametrize(
    ("defect", "expect"), [("", 0), ("bypass_all", 1), ("leak_logs", 1), ("hold_nonstaff", 1)]
)
def test_chat_seeded_defects_are_caught(tmp_path, defect, expect):
    import chat.commands.hold as hold_mod

    saved = hold_mod.roles_of
    try:
        rc = chat_run.run(
            per_label=1, seed=3, data=CHAT_DATA.parent, out=tmp_path, report_dir=tmp_path, categories=["leetspeak"],
            defect=defect, reference=True, record_only=False, phase="t-chat", show=None,
        )  # fmt: skip
    finally:
        hold_mod.roles_of = saved
        chat_run._STATE["classifier"] = None
    assert rc == expect


def test_cli_rejects_defect_without_reference():
    assert redteam_cli.chat_main(["--defect", "bypass_all"]) == 2
    assert redteam_cli.audio_main(["--defect", "late_spans"]) == 2
    assert redteam_cli.audio_main(["--reference", "--defect", "nope"]) == 2


def run_gate(*args):
    return subprocess.run(
        ["bash", "scripts/gate_redteam_audiochat.sh", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_gate_script_help_plan_and_bad_args():
    assert "Exit: 0 pass, 1 a criterion failed, 2 environment" in run_gate("--help").stdout
    plan = run_gate("--plan", "--quick")
    assert plan.returncode == 0 and "redteam-chat" in plan.stdout and "redteam-audio" in plan.stdout
    assert run_gate("--defect", "late_spans").returncode == 2
    assert run_gate("--reference", "--defect", "x").returncode == 2
    assert run_gate("--per-label", "0").returncode == 2
    assert run_gate("--nope").returncode == 2
    only_audio = run_gate("--plan", "--reference", "--defect", "late_spans").stdout
    assert "redteam-audio" in only_audio and "redteam-chat" not in only_audio


def test_gate_script_has_lf_endings():
    assert b"\r" not in (ROOT / "scripts" / "gate_redteam_audiochat.sh").read_bytes()
