"""T4.1 chat eval set: determinism, label counts, uniqueness, SPECIMEN lint, staff messages, obfuscators."""

import json
import random
import re

from eval.data_gen.chat import abuse, doxxing, record, scams
from eval.data_gen.chat.suite import build_messages, build_suite, load, manifest_hash
from eval.data_gen.common.specimen import scan_obj


def test_set_is_deterministic_and_seed_sensitive():
    a, pa = build_messages(1234)
    b, pb = build_messages(1234)
    assert [m.to_dict() for m in a] == [m.to_dict() for m in b] and pa == pb
    c, _ = build_messages(99)
    assert [m.text for m in a] != [m.text for m in c]


def test_sizes_labels_and_uniqueness():
    msgs, private = build_messages(1234)
    assert 1950 <= len(msgs) <= 2050
    by = {}
    for m in msgs:
        by[m.label] = by.get(m.label, 0) + 1
    assert by["abuse"] >= 500 and by["scam"] >= 230 and by["fake_upi"] >= 190 and by["doxxing"] >= 250
    assert by["clean"] >= 700 and by["doxxing_staff"] >= 15
    assert len({m.text.strip().lower() for m in msgs}) == len(msgs)
    assert len({m.id for m in msgs}) == len(msgs)
    assert len(private["terms"]) >= 3 and len(private["phones"]) == 1


def test_staff_messages_and_nongating_flags():
    msgs, _ = build_messages(1234)
    staff = [m for m in msgs if m.staff]
    assert len(staff) >= 55 and {m.staff for m in staff} == {"broadcaster", "moderator"}
    assert all(m.label in ("clean", "doxxing_staff") for m in staff)
    ng = [m for m in msgs if not m.gate]
    assert {"aadhaar", "pan", "qr_lure", "ambiguous_mild", "staff_dox"} <= {m.kind for m in ng}


def test_specimen_lint_and_phone_rules():
    msgs, private = build_messages(1234)
    assert not scan_obj([m.to_dict() for m in msgs])
    assert re.fullmatch(r"[6-9][0-9]{9}", private["phones"][0])
    upi = [m for m in msgs if m.label == "fake_upi"]
    assert all("specimen" in m.text.lower() for m in upi if "upi://" in m.text or "zz-specimen" in m.text)
    assert not any(m.text.endswith("@specimen") for m in upi)  # real handles only


def test_every_label_has_hard_negatives_and_obfuscation():
    msgs, _ = build_messages(1234)
    assert sum("hard_negative" in m.tags for m in msgs) >= 350
    assert sum("obfuscated" in m.tags for m in msgs) >= 100
    kinds = {m.kind for m in msgs}
    assert {"hard_neg_abuse", "hard_neg_scam", "hard_neg_dox"} <= kinds


def test_languages_and_scripts_covered():
    msgs, _ = build_messages(1234)
    langs = {m.lang for m in msgs}
    assert {"hinglish", "deva", "en", "kn", "ta", "te", "bn"} <= langs
    assert any(re.search("[ಀ-೿]", m.text) for m in msgs)  # Kannada script
    assert any(re.search("[஀-௿]", m.text) for m in msgs)  # Tamil
    assert any(re.search("[ఀ-౿]", m.text) for m in msgs)  # Telugu
    assert any(re.search("[ঀ-৿]", m.text) for m in msgs)  # Bengali


def test_obfuscators_change_text_and_are_seeded():
    for how in record.OBFUSCATORS:
        out, used = record.obfuscate("madarchod", random.Random(3), how)
        assert used == how and out != "madarchod"
    assert (
        record.leet("chutiya", random.Random(1), 1.0)
        == record.leet("chutiya", random.Random(1), 1.0)
        != "chutiya"
    )


def test_disguised_links_hide_the_scheme_or_dots():
    outs = {scams.disguise("https://x-specimen.xyz/a", random.Random(i))[1] for i in range(40)}
    assert len(outs) >= 4


def test_phone_forms_all_carry_the_ten_digits():
    for i in range(40):
        rng = random.Random(i)
        text, form = doxxing.phone_forms("9876543210", rng)
        digits = re.sub(r"\D", "", text.translate(str.maketrans("०१२३४५६७८९", "0123456789")))
        assert digits.endswith("9876543210"), (form, text)


def test_manifest_roundtrip_and_hash(tmp_path):
    m = build_suite(tmp_path, 1234)
    m2, msgs = load(tmp_path)
    assert manifest_hash(m) == manifest_hash(m2) and len(msgs) == m["totals"]["messages"]
    assert (
        json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["generator_version"] == "chat-1"
    )


def test_abuse_vocabulary_is_not_in_clean_plain():
    msgs, _ = build_messages(1234)
    plain = [m.text.lower() for m in msgs if m.kind == "plain"]
    assert not any(w in t for t in plain for w in ("chutiya", "madarchod", "bhosdike"))
    assert abuse.ABUSE_COUNTS["slur_latn"] > 0
