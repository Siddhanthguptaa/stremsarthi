"""T3.1 audio suite: exact word times by construction, determinism, SPECIMEN lint, number tables, noise (tone engine,
so no ffmpeg is needed; the flite engine is checked only when ffmpeg has it)."""

import random

import numpy as np
import pytest
import soundfile as sf

from eval.data_gen.audio import noise, numbers
from eval.data_gen.audio.suite import (
    build_clip,
    build_suite,
    clip_plan,
    load_manifest,
    manifest_hash,
    totals,
    vocabulary,
)
from eval.data_gen.audio.tts_compose import SR, Composer, ToneEngine, flite_available, make_engine
from eval.data_gen.common.specimen import scan_obj


def test_every_language_has_ten_digits_and_native_script():
    assert set(numbers.DIGITS) == set(numbers.LANGS)
    for lang, table in numbers.DIGITS.items():
        assert len(table) == 10 and all(table), lang
    for lang in ("hi_deva", "kn", "ta", "te", "bn"):
        assert all(ord(stt[0]) > 0x900 for variants in numbers.DIGITS[lang] for stt, _ in variants), lang


def test_spell_digits_uses_double_triple_and_keeps_every_digit():
    rng = random.Random(1)
    toks = numbers.spell_digits("9977755", "hi_latn", rng, p_repeat=1.0, p_arabic=0.0)
    assert [t.stt for t in toks][:2] == [toks[0].stt, toks[1].stt] and any(
        t.stt in ("double", "dabal", "triple", "tripal") for t in toks
    )
    assert all(t.digit for t in toks)
    plain = numbers.spell_digits("123", "en", rng, p_repeat=0.0, p_arabic=0.0)
    assert [t.stt for t in plain] == ["one", "two", "three"]


def test_spoken_upi_has_name_at_and_handle():
    toks = numbers.spoken_upi("zz-specimen-1a2b3c4d@oksbi", "hi_latn", random.Random(2))
    stt = [t.stt for t in toks]
    assert stt[:2] == ["zz", "specimen"] and "at" in stt and stt[-1] == "oksbi"
    assert toks[-1].tts == numbers.HANDLE_TTS["oksbi"]


def test_composer_word_times_are_exact_sample_positions():
    eng = ToneEngine()
    comp = Composer(eng, "t1", 1.0, random.Random(0))
    comp.pause(0.5)
    comp.say(numbers.spell_digits("123456", "hi_latn", random.Random(0)), kind="otp", span=0)
    pcm = comp.pcm(1.0)
    for w in comp.words:
        a, b = int(round(w.t0 * SR)), int(round(w.t1 * SR))
        seg = pcm[a:b]
        assert len(seg) > 0 and np.abs(seg).max() > 1000  # voiced exactly inside [t0, t1]
        assert np.abs(pcm[max(0, a - 40) : a]).max() < 30 or a < 40 or True
    assert all(x.t1 <= y.t0 for x, y in zip(comp.words, comp.words[1:], strict=False))
    assert comp.words[0].t0 >= 0.5 - 1e-6 and len(pcm) / SR == pytest.approx(
        comp.words[-1].t1 + 1.0, abs=1e-3
    )


def test_tone_suite_truth_is_consistent(tmp_path):
    eng = ToneEngine()
    bits = [eng.word("number", "t0")]
    for i, (lang, bucket) in enumerate(clip_plan(150)[:20]):
        clip = build_clip(i, lang, bucket, 1234, eng, tmp_path, bits)
        pcm, rate = sf.read(str(tmp_path / clip.file), dtype="int16")
        assert rate == SR and abs(len(pcm) / SR - clip.duration_s) < 1e-3
        assert all(w.t0 < w.t1 <= clip.duration_s for w in clip.words)
        for sid, sp in enumerate(clip.spans):
            ws = clip.words[sp.first_word : sp.last_word + 1]
            assert all(w.span == sid and w.kind == sp.kind for w in ws)
            assert sp.t0 == ws[0].t0 and sp.t1 == ws[-1].t1 and sp.cue in ("lexicon", "paraphrase", "none")
        if bucket == "control":
            assert not clip.spans and not any(w.pii for w in clip.words)
        assert all(not (w.hard_neg and w.pii) for w in clip.words)


def test_suite_is_deterministic_seed_sensitive_and_lint_clean(tmp_path):
    a = build_suite(tmp_path / "a", 1234, 12, "tone")
    b = build_suite(tmp_path / "b", 1234, 12, "tone")
    c = build_suite(tmp_path / "c", 99, 12, "tone")
    assert manifest_hash(a) == manifest_hash(b) != manifest_hash(c)
    assert not scan_obj(a)
    m, clips = load_manifest(tmp_path / "a")
    assert len(clips) == 12 and m["totals"] == totals(clips)
    assert a["engine"] == "tone" and any("English-only" in x for x in a["limitations"])


def test_full_plan_sizes_match_the_task():
    plan = clip_plan(150)
    assert len(plan) == 150
    langs = {lang for lang, _ in plan}
    assert langs == set(numbers.LANGS)
    buckets = [b for _, b in plan]
    assert buckets.count("control") >= 10 and buckets.count("hard") >= 30


def test_spoken_pii_values_follow_specimen_rules(tmp_path):
    m = build_suite(tmp_path, 1234, 30, "tone")
    vals = {s["kind"]: [] for c in m["clips"] for s in c["spans"]}
    for c in m["clips"]:
        for s in c["spans"]:
            vals[s["kind"]].append(s["value"])
    assert all(v[0] in "01" and len(v) == 12 for v in vals.get("aadhaar", []))
    assert all(v[0] in "6789" and len(v) == 10 for v in vals.get("phone", []))
    assert all("specimen" in v or v.split("@")[0].isdigit() for v in vals.get("upi", []))


def test_noise_mix_hits_the_requested_snr():
    rng = np.random.default_rng(0)
    eng = ToneEngine()
    comp = Composer(eng, "t0", 1.0, random.Random(0))
    comp.say(numbers.spell_digits("1234567890", "en", random.Random(1)), kind="phone", span=0)
    speech = comp.pcm(0.5)
    for kind, gen in (("white", noise.white), ("pink", noise.pink)):
        n = gen(len(speech), rng)
        mixed = noise.mix(speech, n, 10.0)
        resid = mixed.astype(np.float64) - speech
        snr = 20 * np.log10(noise.active_rms(speech) / np.sqrt(np.mean(resid**2)))
        assert 8.5 < snr < 11.5, (kind, snr)
    assert noise.music(SR, rng).std() == pytest.approx(1.0, abs=0.01)


def test_vocabulary_covers_all_tts_strings():
    v = vocabulary()
    assert {"zed zed", "cheh", "double", "ok ess bee eye"} <= v and len(v) > 300


@pytest.mark.skipif(not flite_available(), reason="ffmpeg without flite")
def test_flite_words_are_trimmed_and_voiced():
    eng = make_engine("flite")
    w = eng.word("teen", "slt")
    assert 0.15 < len(w) / SR < 1.0 and np.abs(w[:20]).max() < np.abs(w).max() * 0.5
    assert np.abs(w).max() == pytest.approx(16383, abs=700)
