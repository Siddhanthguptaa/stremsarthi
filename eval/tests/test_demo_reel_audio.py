"""The demo reel audio: structure, ground-truth timeline and the SPECIMEN PII moments (tone engine, no ffmpeg needed)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from eval.data_gen import demo_audio as da


@pytest.fixture(scope="module")
def built() -> tuple[np.ndarray, dict]:
    # the real song is 120 s; only seconds 20-23 are used, so a 24 s render keeps the test short
    mp = pytest.MonkeyPatch()
    mp.setattr(da, "MUSIC_LIBRARY_SECONDS", 24.0)
    try:
        return da.build("tone")
    finally:
        mp.undo()


def test_timeline_has_two_pii_moments_music_and_one_curse(built: tuple[np.ndarray, dict]) -> None:
    audio, tl = built
    ev = tl["events"]
    assert tl["sample_rate"] == 48000 and tl["duration_s"] == 90.0 and len(audio) == 90 * 48000
    assert audio.dtype == np.float32 and float(np.max(np.abs(audio))) <= 0.96
    assert [e["kind"] for e in ev].count("phone") == 1 and [e["kind"] for e in ev].count("upi") == 1
    assert [e["kind"] for e in ev].count("music") == 1 and [e["kind"] for e in ev].count("profanity") == 1
    phone = next(e for e in ev if e["kind"] == "phone")
    assert phone["value"].startswith("99999") and len(phone["value"]) == 10
    assert [w["w"] for w in phone["pii_words"]] == [da.DIGIT_WORDS[int(d)] for d in phone["value"]]
    assert all(w["digit"] for w in phone["pii_words"])
    upi = next(e for e in ev if e["kind"] == "upi")
    assert upi["value"] == "demo.streamer.zz@ybl" and upi["text"].endswith("demo dot streamer zz at ybl")
    assert [w["w"] for w in upi["pii_words"]] == "demo dot streamer zz at ybl".split()
    assert upi["pii_t0"] > upi["t0"] and phone["pii_t0"] > phone["t0"]  # the cue words come first
    music = next(e for e in ev if e["kind"] == "music")
    assert abs((music["t1"] - music["t0"]) - 3.0) < 0.01


def test_events_do_not_overlap_and_match_the_scene_schedule(built: tuple[np.ndarray, dict]) -> None:
    _, tl = built
    ev = tl["events"]
    for a, b in zip(ev, ev[1:], strict=False):
        assert a["t1"] <= b["t0"], (a["id"], b["id"])
    assert ev[-1]["t1"] <= 90.0
    scenes = {s["scene"]: s for s in tl["scenes"]}
    for e in ev:
        assert scenes[e["scene"]]["t0"] <= e["t0"] <= 90.0


def test_audio_is_loud_where_events_are_and_quiet_between(built: tuple[np.ndarray, dict]) -> None:
    audio, tl = built
    sr = tl["sample_rate"]
    for e in tl["events"]:
        seg = audio[int(e["t0"] * sr) : int(e["t1"] * sr)]
        assert float(np.sqrt(np.mean(seg**2))) > 0.01, e["id"]
    gap = audio[int(41.7 * sr) : int(46.3 * sr)]  # between "signboard" and "otp"
    assert float(np.max(np.abs(gap))) < 1e-4


def test_write_produces_wav_and_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(da, "MUSIC_LIBRARY_SECONDS", 24.0)
    wav, js = da.write(tmp_path, "tone")
    info = sf.info(str(wav))
    assert (info.samplerate, info.channels, round(info.duration)) == (48000, 1, 90)
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["engine"] == "tone" and "SPECIMEN" in data["note"] and len(data["events"]) == 14


def test_no_real_looking_phone_or_id_in_the_timeline(built: tuple[np.ndarray, dict]) -> None:
    from eval.data_gen.common import specimen  # noqa: PLC0415

    _, tl = built
    assert specimen.scan_obj(tl) == []
