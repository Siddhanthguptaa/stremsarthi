"""T5.1 unit tests: music state machine, fingerprint, library, MusicGuard. CPU only, synthetic audio."""

from __future__ import annotations

import json

import numpy as np
import pytest

from contracts.enums import Category
from contracts.events import RiskSignal
from guard.music import fingerprint as fp
from guard.music.build_library import build_synthetic
from guard.music.detector import (
    HOP,
    SR,
    WINDOW,
    MusicDetector,
    MusicStateMachine,
    YamnetScorer,
    _class_index,
    resolve_model_path,
)
from guard.music.evaluate import add_noise, mix, mp3_128k, speech_clips, tempo_change
from guard.music.library import load_library, save_library
from guard.music.service import MusicGuard
from guard.music.spectral import SpectralScorer
from guard.music.synth import synth_song

HAVE_YAMNET = resolve_model_path().is_file()
needs_yamnet = pytest.mark.skipif(not HAVE_YAMNET, reason="YAMNet weights not prepared")
needs_chroma = pytest.mark.skipif(not fp.chromaprint_available(), reason="libchromaprint missing")


@pytest.fixture(scope="module")
def library() -> fp.Library:
    return load_library()


@pytest.fixture(scope="module")
def songs() -> dict[str, np.ndarray]:
    return {f"demo_{i:02d}": synth_song(100 + i, 120.0) for i in range(1, 6)}


def test_class_index_by_name() -> None:
    assert _class_index("Music") == 132 and _class_index("Speech") == 0


def test_state_machine_needs_three_windows_and_hysteresis() -> None:
    sm = MusicStateMachine(alpha=1.0)  # no smoothing, to test the counting alone
    states = [sm.update(p)[1] for p in (0.9, 0.9, 0.1, 0.9, 0.9, 0.9)]
    assert states == [False, False, False, False, False, True]  # flicker does not toggle
    assert [sm.update(p)[1] for p in (0.2, 0.2, 0.4, 0.2, 0.2, 0.2)] == [True, True, True, True, True, False]


@needs_yamnet
def test_yamnet_music_vs_speech() -> None:
    sc = YamnetScorer()
    assert np.median(sc.score(synth_song(900, 6.0))) > 0.8
    sp = speech_clips(3, 6.0)
    assert all(np.max(sc.score(s)) < 0.5 for s in sp)
    assert len(sc.score(np.zeros(WINDOW + HOP, np.float32))) == 2


@needs_yamnet
def test_music_under_speech_at_0db_turns_on() -> None:
    sc = YamnetScorer()
    s, m = speech_clips(2, 12.0), [synth_song(910, 12.0), synth_song(911, 12.0)]
    for sp, mu in zip(s, m, strict=True):
        d = MusicDetector(sc)
        assert any(r.on for r in d.feed(mix(sp, mu, 0.0)))


def test_spectral_fallback_separates_synthetic_music_and_speech() -> None:
    sc = SpectralScorer()
    assert np.median(sc.score(synth_song(901, 6.0))) > 0.8
    assert all(np.mean(sc.score(s) > 0.5) < 0.2 for s in speech_clips(3, 6.0))


def test_popcount_and_entropy_reject() -> None:
    assert list(fp.popcount32(np.array([0, 1, 0xFFFFFFFF, 0x80000001], dtype=np.uint32))) == [0, 1, 32, 2]
    assert not fp.usable_items(np.zeros(40, dtype=np.uint32))


def test_library_is_deterministic_and_has_no_audio(tmp_path) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    save_library(build_synthetic(2, 20.0), a)
    save_library(build_synthetic(2, 20.0), b)
    assert a.read_bytes() == b.read_bytes()
    d = json.loads(a.read_text())
    assert [t["id"] for t in d["tracks"]] == ["demo_01", "demo_02"]
    allowed = {"id", "duration_s", "chromaprint", "landmark"}
    assert all(set(t) <= allowed for t in d["tracks"])
    assert load_library(a).tracks[0].lm_hashes is not None
    # tiny: a fingerprint of 20 s is far smaller than 20 s of 16-bit audio
    assert a.stat().st_size < 2 * 20 * SR * 0.2


def test_shipped_library_roundtrip(library: fp.Library) -> None:
    assert [t.id for t in library.tracks] == [f"demo_0{i}" for i in range(1, 6)]


@needs_chroma
def test_fingerprint_matches_noisy_mp3_snippets(library: fp.Library, songs: dict[str, np.ndarray]) -> None:
    rng = np.random.default_rng(3)
    m = fp.FingerprintMatcher(library)
    ok = tot = 0
    for tid, s in songs.items():
        for _ in range(4):
            a = int(rng.integers(0, len(s) - 5 * SR))
            r = m.match(mp3_128k(add_noise(s[a : a + 5 * SR], 10.0, rng)))
            tot += 1
            ok += r is not None and r.track_id == tid
    assert ok / tot >= 0.9


@needs_chroma
def test_no_false_match_and_tempo_limit(library: fp.Library, songs: dict[str, np.ndarray]) -> None:
    m = fp.FingerprintMatcher(library)
    rng = np.random.default_rng(4)
    for i in range(12):
        assert m.match(add_noise(synth_song(800 + i, 6.0)[: 5 * SR], 10.0, rng)) is None
    for s in speech_clips(6, 5.0):
        assert m.match(s) is None
    q = tempo_change(songs["demo_02"][20 * SR : 20 * SR + 6 * SR], 1.15)[: 5 * SR]
    assert m.match(q) is None  # documented limit
    assert m.match(np.zeros(5 * SR, np.float32)) is None  # silence


def test_landmark_backend_matches_clean_snippet(library: fp.Library, songs: dict[str, np.ndarray]) -> None:
    m = fp.FingerprintMatcher(library, backends=("landmark",))
    r = m.match(songs["demo_04"][40 * SR : 48 * SR])
    assert r is not None and r.track_id == "demo_04" and r.backend == "landmark"
    assert m.match(synth_song(850, 8.0)) is None


class _FixedScorer:
    backend = "fixed"

    def __init__(self, p: float) -> None:
        self.p = p

    def score(self, samples: np.ndarray) -> np.ndarray:
        n = len(samples)
        return np.full(0 if n < WINDOW else (n - WINDOW) // HOP + 1, self.p, dtype=np.float32)


def test_guard_emits_contract_signals_and_never_blocks_push(library: fp.Library) -> None:
    g = MusicGuard(library=library, scorer=_FixedScorer(0.9), wall_clock=lambda: "2026-11-21T00:00:00+00:00")
    audio = synth_song(103, 14.0)
    sigs: list[RiskSignal] = []
    for k in range(0, len(audio), SR):
        g.push(audio[k : k + SR])
        sigs += g.poll()
    music = [s for s in sigs if s.source == "music"]
    assert music and all(
        s.category is Category.MUSIC and s.ttl_s == 10.0 and s.label == "music" for s in music
    )
    assert 0.0 < max(s.score for s in music) <= 1.0
    if fp.chromaprint_available():
        f = [s for s in sigs if s.source == "fingerprint"]
        assert len(f) == 1 and f[0].label == "fp:demo_03" and f[0].score == 0.95 and f[0].ttl_s == 60.0
        assert f[0].category is Category.COPYRIGHT_MATCH
    assert all(s.pts is not None for s in sigs)


def test_guard_short_music_gives_no_fingerprint_and_speech_gives_no_signal(library: fp.Library) -> None:
    g = MusicGuard(library=library, scorer=_FixedScorer(0.9), wall_clock=lambda: "t")
    a = synth_song(103, 4.0)
    g.push(a)
    assert not [s for s in g.poll() if s.source == "fingerprint"]
    g2 = MusicGuard(library=library, scorer=_FixedScorer(0.0), wall_clock=lambda: "t")
    g2.push(synth_song(103, 10.0))
    assert g2.poll() == []


def test_push_is_bounded_and_fast(library: fp.Library) -> None:
    g = MusicGuard(library=library, scorer=_FixedScorer(0.0))
    for _ in range(40):
        g.push(np.zeros(SR, np.float32))
    assert g.dropped_s >= 9.0 and g._queued <= 30 * SR


def test_privacy_signal_labels_are_codes(library: fp.Library) -> None:
    g = MusicGuard(library=library, scorer=_FixedScorer(0.9))
    g.push(synth_song(103, 12.0))
    for s in g.poll():
        assert s.label in {"music", "fp:demo_03"} and isinstance(s, RiskSignal)
