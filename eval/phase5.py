"""Gate 5 (T5.5): the strike guard and live captions against the guard eval set (``eval/data_gen/guard``).

Legs (``python -m eval.phase5 --leg ...``; each writes ``<out>/phase5-<leg>.json``; exit 0 ok, 1 criterion failed,
2 environment or setup error or a component that does not exist yet: NOT RUN, never a pass):

* ``data``             the set exists and is complete, the manifest is clean (SPECIMEN lint).
* ``music``            ``guard.music.detector.MusicDetector`` over music-only, music-under-speech and speech-only clips.
* ``fingerprint``      library built by ``guard.music.build_library.build_from_dir`` from the eval's own wavs, then
                       ``FingerprintMatcher`` on 5 s snippets (10 dB pink noise, half through MP3 128 kbps).
* ``profanity``        written Hinglish, English and regional utterances through ``guard.risk.ProfanityDetector``.
* ``meter``            signals injected into ``guard.risk.RiskService`` on a virtual clock: time to the first level change.
* ``captions_render``  ``engine.captions.CaptionRenderer`` in five scripts against an independent raqm reference.
* ``captions_timing``  ``audio.captions.service.CaptionService`` on a spoken timeline: every published caption must
                       arrive before its display time (capture + delay), and nothing muted may appear in a caption.
* ``report``           builds ``eval/reports/<date>-phase5.md`` from the leg files.

``--reference`` swaps every component for a stand-in that is right by construction (``eval.phase5_ref``); ``--defect``
seeds one defect into it. Nothing here reads transcripts or audio into a log.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from . import phase5_ref as ref
from .data_gen.common.specimen import scan_obj
from .data_gen.guard import clips as cl
from .data_gen.guard.suite import DEFAULT_SEED, build_suite, load, load_captions, load_utterances
from .harness import metrics

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = "eval/data/generated/guard"
EXIT_OK, EXIT_FAIL, EXIT_ENV = 0, 1, 2
LEGS = ("data", "music", "fingerprint", "profanity", "meter", "captions_render", "captions_timing")
MIN_COUNTS = {
    "music_only": 20,
    "music_under_speech": 10,
    "speech_only": 20,
    "fp_positive": 40,
    "fp_negative": 10,
}


class NotRun(Exception):
    """A component the leg needs does not exist (yet) or cannot load: the leg is NOT RUN, never a pass."""


def pct(xs: list[float], q: float) -> float | None:
    return round(float(np.percentile(np.asarray(xs), q)), 3) if xs else None


def rat(k: int, n: int) -> dict[str, Any]:
    r = metrics.ratio(k, n)
    return {
        "k": k,
        "n": n,
        "value": None if r.value is None else round(r.value, 4),
        "ci": [round(r.ci_lo, 4), round(r.ci_hi, 4)],
    }


# ================================================================================================ data
def leg_data(data: Path) -> tuple[dict[str, Any], int]:
    problems: list[str] = []
    try:
        m = load(data)
    except (OSError, ValueError):
        return {"leg": "data", "problems": [f"no manifest at {data}"], "totals": {}}, EXIT_ENV
    counts: dict[str, int] = defaultdict(int)
    for c in m["clips"] + m["snippets"]:
        counts[c["kind"]] += 1
        if not (data / c["file"]).is_file():
            problems.append(f"missing file {c['file']}")
    for kind, need in MIN_COUNTS.items():
        if counts[kind] < need:
            problems.append(f"{kind}: {counts[kind]} clips, need {need}")
    if len(m["library"]) < 5:
        problems.append(f"library has {len(m['library'])} tracks, need 5")
    for t in m["library"]:
        if not (data / t["file"]).is_file():
            problems.append(f"missing library file {t['file']}")
    utts = load_utterances(data)
    groups: dict[str, int] = defaultdict(int)
    for u in utts:
        groups[u.group] += 1
    for g, need in (
        ("hinglish_latn", 60),
        ("hinglish_deva", 30),
        ("english", 20),
        ("clean", 20),
        ("clean_trap", 15),
    ):
        if groups[g] < need:
            problems.append(f"profanity group {g}: {groups[g]}, need {need}")
    if len({u.text for u in utts}) != len(utts):
        problems.append("duplicate utterance texts")
    if scan_obj(m):
        problems.append("forbidden pattern in the manifest")
    cap = load_captions(data)
    if set(cap["shaping"]) != {"hi", "kn", "ta", "te", "bn"}:
        problems.append("shaping texts are not in the five scripts")
    res = {
        "leg": "data",
        "problems": problems,
        "totals": m["totals"],
        "seed": m["seed"],
        "engine": m["tts_engine"],
        "mp3": m["mp3_available"],
    }
    return res, EXIT_OK


# ================================================================================================ music
def _music_clips(data: Path) -> list[dict[str, Any]]:
    return [c for c in load(data)["clips"]]


def leg_music(data: Path, reference: bool, defect: str) -> dict[str, Any]:
    clips = _music_clips(data)
    detector_cls: Any = None
    backend = "reference"
    scorer: Any = None
    if not reference:
        try:
            from guard.music.detector import MusicDetector, make_scorer  # noqa: PLC0415
        except ImportError as exc:
            raise NotRun(f"guard.music.detector is not importable ({exc.name})") from exc
        scorer = make_scorer()
        detector_cls = MusicDetector
        backend = str(getattr(scorer, "backend", type(scorer).__name__))
    rows: list[dict[str, Any]] = []
    t_wall = time.perf_counter()
    for c in clips:
        if reference:
            on, lat = ref.ref_music(c, defect)
            rows.append({"id": c["id"], "kind": c["kind"], "music": c["music"], "on": on, "latency_s": lat})
            continue
        det = detector_cls(scorer)
        res = det.feed(cl.read_wav(data / c["file"]))
        t_on = next((r.t_end for r in res if r.on), None)
        lat = None if t_on is None or c["music_start_s"] is None else round(t_on - c["music_start_s"], 3)
        rows.append(
            {"id": c["id"], "kind": c["kind"], "music": c["music"], "on": t_on is not None, "latency_s": lat}
        )
    secs = sum(c["seconds"] for c in clips)
    music = [r for r in rows if r["music"]]
    speech = [r for r in rows if r["kind"] == "speech_only"]
    under = [r for r in rows if r["kind"] == "music_under_speech"]
    lats = [r["latency_s"] for r in music if r["latency_s"] is not None]
    return {
        "leg": "music", "backend": backend, "reference": reference, "defect": defect,
        "recall": rat(sum(r["on"] for r in music), len(music)),
        "recall_under_speech": rat(sum(r["on"] for r in under), len(under)),
        "recall_music_only": rat(sum(r["on"] for r in music if r["kind"] == "music_only"), len([r for r in music if r["kind"] == "music_only"])),
        "false_positive": rat(sum(r["on"] for r in speech), len(speech)),
        "fp_by_noise": {k: rat(sum(r["on"] for r in speech if _noise(clips, r["id"]) == k), len([r for r in speech if _noise(clips, r["id"]) == k])) for k in ("clean", "pink", "white")},
        "on_latency_s": {"n": len(lats), "p50": pct(lats, 50), "p95": pct(lats, 95), "max": round(max(lats), 3) if lats else None},
        "missed_ids": [r["id"] for r in music if not r["on"]], "fp_ids": [r["id"] for r in speech if r["on"]],
        "compute_s_per_audio_s": None if reference else round((time.perf_counter() - t_wall) / max(secs, 1e-9), 4),
    }  # fmt: skip


def _noise(clips: list[dict[str, Any]], cid: str) -> str:
    return next(c.get("noise", "") for c in clips if c["id"] == cid)


# ================================================================================================ fingerprint
def leg_fingerprint(data: Path, reference: bool, defect: str) -> dict[str, Any]:
    m = load(data)
    snips = m["snippets"]
    backend = "reference"
    matcher: Any = None
    t_build = 0.0
    if not reference:
        try:
            from contracts.config import GuardConfig  # noqa: PLC0415
            from guard.music.build_library import build_from_dir  # noqa: PLC0415
            from guard.music.fingerprint import FingerprintMatcher, chromaprint_available  # noqa: PLC0415
        except ImportError as exc:
            raise NotRun(f"guard.music fingerprint modules are not importable ({exc.name})") from exc
        t0 = time.perf_counter()
        lib = build_from_dir(data / "music" / "library")
        t_build = time.perf_counter() - t0
        ids = [t.id for t in lib.tracks]
        want = [t["id"] for t in m["library"]]
        if ids != want:
            raise NotRun(f"library ids {ids} differ from the manifest {want}")
        cfg = GuardConfig()
        matcher = FingerprintMatcher(lib, max_ber=cfg.fp_max_ber, margin=cfg.fp_margin)
        backend = "chromaprint" if chromaprint_available() else "landmark"
    rows = []
    for s in snips:
        t0 = time.perf_counter()
        if reference:
            got = ref.ref_fingerprint(s, defect)
        else:
            mt = matcher.match(cl.read_wav(data / s["file"]))
            got = None if mt is None else mt.track_id
        rows.append({**s, "got": got, "ms": (time.perf_counter() - t0) * 1000.0})
    pos = [r for r in rows if r["kind"] == "fp_positive"]
    neg = [r for r in rows if r["kind"] == "fp_negative"]
    tempo = [r for r in rows if r["kind"] == "fp_tempo"]
    return {
        "leg": "fingerprint", "backend": backend, "reference": reference, "defect": defect,
        "match_correct": rat(sum(r["got"] == r["track"] for r in pos), len(pos)),
        "match_wrong_id": rat(sum(r["got"] not in (None, r["track"]) for r in pos), len(pos)),
        "false_match": rat(sum(r["got"] is not None for r in neg), len(neg)),
        "by_codec": {k: rat(sum(r["got"] == r["track"] for r in pos if r["codec"] == k), len([r for r in pos if r["codec"] == k])) for k in ("none", "mp3_128k")},
        "tempo_15pct_matched": rat(sum(r["got"] is not None for r in tempo), len(tempo)),
        "match_ms": {"p50": pct([r["ms"] for r in rows], 50), "p95": pct([r["ms"] for r in rows], 95)},
        "library_build_s": round(t_build, 2), "missed_ids": [r["id"] for r in pos if r["got"] != r["track"]],
    }  # fmt: skip


# ================================================================================================ profanity
def leg_profanity(data: Path, reference: bool, defect: str) -> dict[str, Any]:
    utts = load_utterances(data)
    det: Any = None
    if not reference:
        try:
            from contracts.speech import Word  # noqa: PLC0415
            from guard.risk import ProfanityDetector  # noqa: PLC0415
        except ImportError as exc:
            raise NotRun(f"guard.risk is not importable ({exc.name})") from exc
        det = ProfanityDetector()
    rows = []
    for u in utts:
        t0 = time.perf_counter()
        if reference:
            times = ref.ref_profanity(asdict_utt(u), defect)
        else:
            words = [Word(w, t0_ + u.t_offset, t1_ + u.t_offset) for w, t0_, t1_ in u.words]
            times = [s.pts for s in det.feed_words(words) if s.pts is not None]
        ms = (time.perf_counter() - t0) * 1000.0
        first = u.t_offset + u.words[u.hit_words[0]][1] if u.hit_words else None
        rows.append({"id": u.id, "group": u.group, "level": u.level, "profane": u.profane, "fired": bool(times), "ms": ms,
                     "on_time": bool(times) and first is not None and min(abs(t - first) for t in times) <= 0.6})  # fmt: skip

    def recall(pred: Any) -> dict[str, Any]:
        sel = [r for r in rows if r["profane"] and pred(r)]
        return rat(sum(r["fired"] for r in sel), len(sel))

    clean = [r for r in rows if not r["profane"]]
    traps = [r for r in rows if r["group"] == "clean_trap"]
    fired = [r for r in rows if r["profane"] and r["fired"]]
    return {
        "leg": "profanity", "reference": reference, "defect": defect,
        "hinglish_recall": recall(lambda r: r["group"] in ("hinglish_latn", "hinglish_deva")),
        "by_group": {g: recall(lambda r, g=g: r["group"] == g) for g in ("hinglish_latn", "hinglish_deva", "english", "variants", "regional")},
        "by_level": {str(lv): recall(lambda r, lv=lv: r["level"] == lv and r["group"] in ("hinglish_latn", "hinglish_deva")) for lv in (1, 2)},
        "false_positive": rat(sum(r["fired"] for r in clean), len(clean)),
        "trap_false_positive": rat(sum(r["fired"] for r in traps), len(traps)),
        "signal_time_ok": rat(sum(r["on_time"] for r in fired), len(fired)),
        "process_ms": {"p50": pct([r["ms"] for r in rows], 50), "p95": pct([r["ms"] for r in rows], 95), "max": round(max(r["ms"] for r in rows), 3)},
        "missed_ids": [r["id"] for r in rows if r["profane"] and not r["fired"] and r["group"].startswith("hinglish")],
        "fp_ids": [r["id"] for r in clean if r["fired"]],
    }  # fmt: skip


def asdict_utt(u: Any) -> dict[str, Any]:
    from dataclasses import asdict  # noqa: PLC0415

    return asdict(u)


# ================================================================================================ meter
SCENARIOS = (  # name, expected peak level
    ("music_p0.8", "yellow"),
    ("fingerprint_match", "red"),
    ("profanity_L2_transcript", "yellow"),
    ("risky_text", "yellow"),
)


class _RealRisk:
    def __init__(self, utt: Any) -> None:
        from contracts.config import GuardConfig  # noqa: PLC0415
        from guard.risk import RiskService  # noqa: PLC0415

        self.svc = RiskService(GuardConfig(), None)
        self.utt = utt
        self.warn = 0

    def _collect(self, out: list[Any]) -> list[tuple[str, float]]:
        from contracts.enums import Topic  # noqa: PLC0415

        self.warn += sum(1 for tp, _ in out if tp is Topic.SAFETY_EVENT)
        return [(ev.level, ev.score) for tp, ev in out if tp is Topic.RISK_METER]

    def inject(self, name: str, t: float) -> list[tuple[str, float]]:
        from contracts.enums import Category, Topic  # noqa: PLC0415
        from contracts.events import RiskSignal  # noqa: PLC0415
        from contracts.speech import Transcript, Word  # noqa: PLC0415

        def sig(src: str, cat: Any, score: float, ttl: float, label: str) -> Any:
            return RiskSignal("2026-01-01T00:00:00+00:00", t, src, cat, score, ttl, label)

        if name == "music_p0.8":
            return self._collect(
                self.svc.handle(Topic.RISK, sig("music", Category.MUSIC, 0.8, 10.0, "music"), now=t)
            )
        if name == "fingerprint_match":
            return self._collect(
                self.svc.handle(
                    Topic.RISK, sig("fingerprint", Category.COPYRIGHT_MATCH, 0.95, 60.0, "fp:demo_01"), now=t
                )
            )
        if name == "risky_text":
            return self._collect(
                self.svc.handle(
                    Topic.RISK, sig("risky_text", Category.RISKY_TEXT, 0.5, 10.0, "risky_text"), now=t
                )
            )
        u = self.utt
        words = tuple(Word(w, t + a, t + b) for w, a, b in u.words)
        tr = Transcript(t, t + words[-1].t1 - t, u.text, words, "hi", "local", 1.0)
        return self._collect(self.svc.handle(Topic.TRANSCRIPT, tr, now=t + words[u.hit_words[0]].t1 - t))

    def tick(self, t: float) -> list[tuple[str, float]]:
        return self._collect(self.svc.tick(now=t))


class _RefRisk:
    def __init__(self, defect: str) -> None:
        self.r = ref.RefRisk(defect)
        self.last = -1e9

    def _pub(self, t: float, force: bool) -> list[tuple[str, float]]:
        if force or t - self.last >= 0.5 - 1e-9:
            self.last = t
            return [self.r.meter(t)]
        return []

    def inject(self, name: str, t: float) -> list[tuple[str, float]]:
        spec = {"music_p0.8": ("music", 0.8, 10.0), "fingerprint_match": ("fingerprint", 0.95, 60.0),
                "profanity_L2_transcript": ("profanity", 0.6, 30.0), "risky_text": ("risky_text", 0.5, 10.0)}[name]  # fmt: skip
        self.r.add(*spec, t)
        return self._pub(t, True)

    def tick(self, t: float) -> list[tuple[str, float]]:
        return self._pub(t, False)


def leg_meter(data: Path, reference: bool, defect: str) -> dict[str, Any]:
    utts = load_utterances(data)
    # a canonical strong word (the profanity leg measures lexicon coverage; this leg measures the meter, not the lexicon)
    strong = [u for u in utts if u.group == "hinglish_latn" and u.level == 2 and u.hit_words]
    utt = next((u for u in strong if u.words[u.hit_words[0]][0] == "chutiya"), strong[0])
    if not reference:
        try:
            _RealRisk(utt)
        except ImportError as exc:
            raise NotRun(f"guard.risk is not importable ({exc.name})") from exc
    rows = []
    rate_n, rate_span = 0, 0.0
    for name, expect in SCENARIOS:
        drv = _RefRisk(defect) if reference else _RealRisk(utt)
        t_inj, step = 10.0, 0.1
        series: list[tuple[float, str, float]] = [(t_inj, lv, sc) for lv, sc in drv.inject(name, t_inj)]
        t = t_inj
        while t < t_inj + 30.0:
            t = round(t + step, 6)
            series += [(t, lv, sc) for lv, sc in drv.tick(t)]
        first = next((x for x in series if x[1] != "green"), None)
        peak = max((ref.LEVELS.index(x[1]) for x in series), default=0)
        peak_t = next((x[0] for x in series if ref.LEVELS.index(x[1]) == peak), t_inj)
        back = next((x[0] - t_inj for x in series if x[0] > peak_t and x[1] == "green"), None)
        rows.append({"scenario": name, "expect": expect, "peak": ref.LEVELS[peak], "reaction_s": None if first is None else round(first[0] - t_inj, 3),
                     "back_to_green_s": None if back is None else round(back, 3), "warnings": getattr(drv, "warn", None)})  # fmt: skip
        if name == "music_p0.8":  # 2 Hz publication while a signal is live (t_inj+1 .. t_inj+9)
            rate_n = sum(1 for x in series if t_inj + 1.0 <= x[0] < t_inj + 9.0)
            rate_span = 8.0
    reacts = [r["reaction_s"] for r in rows]
    return {
        "leg": "meter", "reference": reference, "defect": defect, "scenarios": rows,
        "reaction_s": {"max": None if None in reacts else max(reacts), "all": reacts},
        "level_ok": rat(sum(r["peak"] == r["expect"] for r in rows), len(rows)),
        "publish_hz": round(rate_n / rate_span, 3) if rate_span else None,
    }  # fmt: skip


# ================================================================================================ captions
def leg_captions_render(data: Path, reference: bool, defect: str) -> dict[str, Any]:
    from . import phase5_shaping as sh  # noqa: PLC0415

    cap = load_captions(data)
    renderer: Any = None
    if not reference:
        try:
            from engine.captions import CaptionRenderer  # noqa: PLC0415
        except ImportError as exc:
            raise NotRun(f"engine.captions is not importable ({exc.name})") from exc
        renderer = CaptionRenderer(None, 1280, 720, use_raqm=True, enabled=True)
        raqm = bool(renderer.raqm)
    else:
        raqm = True
    rows: list[dict[str, Any]] = []
    for lang, texts in cap["shaping"].items():
        for i, text in enumerate(texts):
            if reference:
                mask = sh.ref_mask(text, lang, raqm=defect != "basic_shaping")
            else:
                mask = renderer.text_mask(text, lang)
            cmp = sh.compare(mask, text, lang)
            row: dict[str, Any] = {"lang": lang, "i": i, **cmp}
            row.update(_frame_checks(renderer, lang, text) if renderer is not None else _frame_checks_ref())
            rows.append(row)
    by_lang = {}
    for lang in cap["shaping"]:
        sel = [r for r in rows if r["lang"] == lang]
        by_lang[lang] = {"script": cap["script_names"][lang], "shaping_ok": rat(sum(bool(r["ok"]) for r in sel), len(sel)),
                         "frame_ok": rat(sum(bool(r.get("frame_ok")) for r in sel), len(sel))}  # fmt: skip
    ok = [r for r in rows if r["ok"]]
    return {
        "leg": "captions_render", "reference": reference, "defect": defect, "raqm": raqm, "by_lang": by_lang,
        "scripts_ok": sum(1 for v in by_lang.values() if v["shaping_ok"]["k"] == v["shaping_ok"]["n"] and v["frame_ok"]["k"] == v["frame_ok"]["n"]),
        "scripts_total": len(by_lang),
        "worst_corr": min((r.get("corr_raqm", 0.0) for r in rows), default=None),
        "max_width_err": max((r.get("width_err", 1.0) for r in rows), default=None), "n_ok": len(ok), "n": len(rows),
        "bad": [f"{r['lang']}#{r['i']}" for r in rows if not r["ok"] or not r.get("frame_ok", True)],
    }  # fmt: skip


def _frame_checks_ref() -> dict[str, Any]:
    return {"frame_ok": True}


def _frame_checks(renderer: Any, lang: str, text: str) -> dict[str, Any]:
    """Burn one caption into a flat grey frame: drawn inside its window only, in the bottom third and the side
    margins only, and not at all when switched off."""
    from contracts.events import CaptionEvent  # noqa: PLC0415

    renderer.clear()
    renderer.add(CaptionEvent(10.0, 13.0, lang, text, True))
    h, w = renderer.height, renderer.width
    base = np.full((h, w, 3), 90, np.uint8)
    inside, before, after = base.copy(), base.copy(), base.copy()
    d_in, d_before, d_after = (
        renderer.render(inside, 11.0),
        renderer.render(before, 9.5),
        renderer.render(after, 14.5),
    )
    diff = np.any(inside != base, axis=2)
    ys, xs = np.nonzero(diff)
    placed = (
        bool(len(ys)) and ys.min() >= 2 * h // 3 and xs.min() >= int(0.04 * w) and xs.max() <= int(0.96 * w)
    )
    bright = bool(len(ys)) and int(inside[diff].max()) >= 200
    renderer.set_enabled(False)
    off = base.copy()
    d_off = renderer.render(off, 11.0)
    renderer.set_enabled(True)
    ok = (
        d_in
        and not d_before
        and not d_after
        and placed
        and bright
        and not d_off
        and bool((off == base).all())
        and bool((before == base).all())
    )
    return {
        "frame_ok": bool(ok),
        "frame": {
            "drawn": d_in,
            "not_before": not d_before,
            "not_after": not d_after,
            "placed": placed,
            "bright": bright,
            "off_untouched": not d_off,
        },
    }


class _ListBus:
    def __init__(self, now: Any) -> None:
        self.now = now
        self.events: list[tuple[Any, Any, float]] = []

    def publish(self, topic: Any, ev: Any) -> None:
        self.events.append((topic, ev, self.now()))


async def _timing_real(tl: dict[str, Any]) -> dict[str, Any]:
    try:
        from audio.captions.service import CaptionService  # noqa: PLC0415
        from audio.captions.settings import CaptionSettings  # noqa: PLC0415
        from audio.captions.translate import ResilientTranslator  # noqa: PLC0415
        from contracts.detection import MuteSpan  # noqa: PLC0415
        from contracts.enums import Category, Topic  # noqa: PLC0415
        from contracts.speech import Transcript, Word  # noqa: PLC0415
    except ImportError as exc:
        raise NotRun(f"audio.captions.service is not importable ({exc.name})") from exc
    anchor = {"v": 0.0, "m": time.monotonic()}

    def vnow() -> float:
        return anchor["v"] + (time.monotonic() - anchor["m"])

    cur: dict[str, Any] = {"lat": 0.3}

    class FakeProvider:  # a stand-in for Sarvam with the timeline's latency, no network
        name, paid = "sarvam", False
        available = True

        async def translate(self, text: str, src: str, tgt: str) -> str:
            await asyncio.sleep(cur["lat"])
            return text

        transliterate = translate

    delay = float(tl["delay_s"])
    bus = _ListBus(vnow)
    svc = CaptionService(None, bus, ResilientTranslator([FakeProvider()], clock=vnow),
                         CaptionSettings(enabled=True, target_lang="hi", delay_s=delay, local_rules=False, on_late="skip"), clock=vnow)  # fmt: skip
    for seg in tl["segments"]:
        anchor["v"], anchor["m"] = seg["pts_end"] + seg["stt_latency_s"], time.monotonic()
        cur["lat"] = seg["translate_latency_s"]
        words = tuple(Word(w, a, b) for w, a, b in seg["words"])
        svc.handle(
            Topic.TRANSCRIPT,
            Transcript(
                seg["pts_start"],
                seg["pts_end"],
                " ".join(w for w, _, _ in seg["words"]),
                words,
                seg["lang"],
                "local",
                seg["stt_latency_s"],
            ),
        )
        if seg["mute"]:
            lo, hi = seg["mute"]
            svc.handle(
                Topic.MUTE_SPAN, MuteSpan("m1", lo, hi, Category.SPOKEN_PHONE, "word", 0.9, "digits", vnow())
            )
        await svc.drain()
    out = []
    for topic, ev, v in bus.events:
        if topic is Topic.CAPTION:
            out.append((ev.pts_start, ev.pts_end, ev.text, v))
    return {"published": out, "stats": dict(svc.stats)}


def leg_captions_timing(data: Path, reference: bool, defect: str) -> dict[str, Any]:
    tl = load_captions(data)["timeline"]
    delay = float(tl["delay_s"])
    segs = tl["segments"]
    if reference:
        pub = []
        for s in segs:
            t = ref.ref_caption_publish_time(s, defect, delay)
            if t is not None:
                text = " ".join("###" if w in s["secret_words"] else w for w, _, _ in s["words"])
                pub.append((s["pts_start"], s["pts_end"], text, t))
        stats: dict[str, Any] = {}
    else:
        r = asyncio.run(_timing_real(tl))
        pub, stats = r["published"], r["stats"]
    margins = [round(p[0] + delay - p[3], 3) for p in pub]
    leaks = 0
    for s in (
        segs
    ):  # a muted segment's captions must not show the muted words (checked on that segment's captions only)
        if s["secret_words"]:
            for lo, _hi, text, _ in pub:
                if s["pts_start"] - 0.01 <= lo <= s["pts_end"] + 0.01:
                    leaks += sum(1 for w in s["secret_words"] if w in text.split())
    covered = sum(1 for s in segs if any(abs(p[0] - s["pts_start"]) < 1e-3 for p in pub))
    return {
        "leg": "captions_timing", "reference": reference, "defect": defect, "n_segments": len(segs), "n_published": len(pub),
        "late_published": sum(1 for m in margins if m < 0.0), "margin_s": {"min": min(margins, default=None), "p5": pct(margins, 5), "p50": pct(margins, 50)},
        "covered": rat(covered, len(segs)), "secret_word_leaks": leaks, "service_stats": stats,
    }  # fmt: skip


# ================================================================================================ main
def run_leg(leg: str, data: Path, reference: bool, defect: str) -> tuple[dict[str, Any], int]:
    if leg == "data":
        return leg_data(data)
    if not (data / "manifest.json").exists():
        print(
            f"ENV ERROR: no dataset at {data} (python -m eval.data_gen.guard --out {data})", file=sys.stderr
        )
        return {"leg": leg, "not_run": "no dataset"}, EXIT_ENV
    fn = {"music": leg_music, "fingerprint": leg_fingerprint, "profanity": leg_profanity, "meter": leg_meter,
          "captions_render": leg_captions_render, "captions_timing": leg_captions_timing}[leg]  # fmt: skip
    try:
        return fn(data, reference, defect), EXIT_OK
    except NotRun as exc:
        print(f"NOT RUN ({leg}): {exc}", file=sys.stderr)
        return {"leg": leg, "not_run": str(exc)}, EXIT_ENV


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.phase5", description=__doc__.split("\n")[0])
    p.add_argument("--leg", required=True, choices=(*LEGS, "report"))
    p.add_argument("--data", default=DEFAULT_DATA)
    p.add_argument("--out", default="")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--generate", action="store_true", help="build the set when missing")
    p.add_argument("--reference", action="store_true")
    p.add_argument("--defect", default="")
    p.add_argument("--results", default="")
    p.add_argument("--report-dir", default="")
    p.add_argument("--phase-name", default="phase5")
    a = p.parse_args(argv)
    data = Path(a.data) if Path(a.data).is_absolute() else ROOT / a.data
    out = Path(a.out) if a.out else ROOT / ".tmp" / "phase5"
    out.mkdir(parents=True, exist_ok=True)
    if a.defect and not a.reference:
        print("ERROR: --defect needs --reference (defects are seeded into the stand-ins)", file=sys.stderr)
        return EXIT_ENV
    if a.defect and a.defect not in ref.DEFECTS:
        print(f"ERROR: --defect must be one of {', '.join(ref.DEFECTS)}", file=sys.stderr)
        return EXIT_ENV
    if a.leg == "report":
        from .phase5_report import build_report  # noqa: PLC0415

        return build_report(
            out,
            Path(a.results) if a.results else None,
            a.phase_name,
            Path(a.report_dir) if a.report_dir else None,
        )
    if a.generate and not (data / "manifest.json").exists():
        build_suite(data, a.seed)
    res, rc = run_leg(a.leg, data, a.reference, a.defect)
    (out / f"phase5-{a.leg}.json").write_text(
        json.dumps(res, indent=1, sort_keys=True, default=str, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    brief = {k: v for k, v in res.items() if not k.endswith("_ids") and k not in ("bad", "scenarios")}
    print(json.dumps(brief, indent=1, default=str, ensure_ascii=False))
    if rc == EXIT_OK:
        from .phase5_report import evaluate  # noqa: PLC0415

        failed = [c for c in evaluate(res) if not c.passed]
        for c in failed:
            print(f"FAIL {c.name}: {c.value} {c.op} {c.target}", file=sys.stderr)
        rc = EXIT_FAIL if failed else EXIT_OK
    return rc


if __name__ == "__main__":
    sys.exit(main())
