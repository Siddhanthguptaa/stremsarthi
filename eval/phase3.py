"""Gate 3 (T3.1): spoken-PII mute coverage through the audio pipeline and the engine's mute book.

Legs (``python -m eval.phase3 --leg ...``, each writes ``<out>/phase3-<leg>.json``; exit 0 ok, 1 criterion failed,
2 environment/setup error with nothing measured, 3 required leg not runnable):

* ``data``       dataset present, sizes, sample hashes, SPECIMEN lint, engine is real TTS (not the tone fallback).
* ``main``       APIs up. Every clip goes through ``audio.AudioPipeline`` (Silero VAD, STT race, PII rules) in
                 virtual time at real-time pacing; the resulting ``MuteSpan`` s are applied by
                 ``engine.audio_mute.MuteBook`` to the 48 kHz clip exactly as the delay buffer would (a span that
                 is ready after its samples left is late and leaks). Mute coverage, over-mute and latency are
                 read from that OUTPUT audio. Mouth blur: ``engine.mouth.resolve_mouth_quads`` on muted frames.
* ``api_down``   every provider fails: voiced speech with no verdict must be 100 % muted (fail-safe).
* ``local_only`` the API provider fails, the local recogniser (modelled lossy) answers: PII coverage >= 70 %.

THE RECOGNISER IS AN ORACLE: the offline TTS cannot be recognised by a real STT (no native Indic phonetics), so
``OracleStt`` answers from the ground-truth words with seeded errors (``SttModel``). The numbers therefore
measure the audio pipeline's timing, VAD segmentation, PII rules, span merging, fail-safe and the engine's mute
application, NOT recognition accuracy. A real-recogniser leg needs real recorded or native-TTS audio.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .data_gen.audio.suite import DEFAULT_N, DEFAULT_SEED, build_suite, load_manifest
from .data_gen.audio.truth import ClipTruth, WordTruth
from .data_gen.common.specimen import scan_obj
from .harness import metrics

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = "eval/data/generated/audio"
DELAY_S = 4.0
HOP_S = 0.01
OUT_RATE = 48_000
FRAME_FPS = 30.0
EXIT_OK, EXIT_FAIL, EXIT_ENV, EXIT_SKIP = 0, 1, 2, 3
TARGETS = {
    "pii_coverage": 0.90,
    "digit_coverage": 0.95,
    "over_mute": 0.15,
    "audio_p95_s": 3.3,
    "mouth_blur": 0.95,
    "api_down_pii": 1.0,
    "api_down_speech": 0.99,
    "local_only_pii": 0.70,
}


# --------------------------------------------------------------------------- the oracle recogniser
@dataclass(frozen=True)
class SttModel:
    """Error model of a recogniser fed the truth words: per-word fate is fixed per word (overlapping
    segments agree), word times are jittered, latency is uniform per request."""

    drop: float = 0.0  # word missing from the transcript
    mishear: float = 0.0  # word replaced by an unrelated non-number word
    latency: tuple[float, float] = (0.4, 1.0)
    jitter_s: float = 0.03


PERFECT = SttModel(0.0, 0.0, (0.4, 1.0), 0.0)
API_STT = SttModel(0.03, 0.02, (0.4, 1.0))  # assumed: a good cloud recogniser on clean speech
LOCAL_STT = SttModel(0.10, 0.05, (0.8, 1.4))  # assumed: faster-whisper base on Hinglish, ~15 % words lost


class OracleStt:
    name: ClassVar[str] = "oracle"

    def __init__(
        self,
        words: list[WordTruth],
        provider: str,
        model: SttModel,
        seed: int,
        fail: BaseException | None = None,
    ) -> None:
        from contracts.speech import Word  # noqa: PLC0415 (imported lazily: contracts is only needed to run legs)

        self._word_cls = Word
        self.provider = provider
        self.model = model
        self.fail = fail
        self.rng = random.Random(seed)
        fate = random.Random(seed ^ 0x5EED)
        self.words: list[tuple[WordTruth, str, float, float]] = []
        for w in words:
            r = fate.random()
            text = w.text
            if r < model.drop:
                continue
            if r < model.drop + model.mishear:
                text = "kya"
            j0, j1 = fate.gauss(0, model.jitter_s), fate.gauss(0, model.jitter_s)
            t0 = max(0.0, w.t0 + j0)
            t1 = max(t0, w.t1 + j1)
            self.words.append((w, text, t0, t1))
        self.calls = 0

    async def transcribe(self, pcm16k: Any, pts_start: float, lang_hint: str | None) -> Any:
        from contracts.speech import Transcript  # noqa: PLC0415

        self.calls += 1
        lat = self.rng.uniform(*self.model.latency)
        await asyncio.sleep(lat)
        if self.fail is not None:
            raise self.fail
        end = pts_start + len(pcm16k) / 16_000
        got = [(t, a, b) for w, t, a, b in self.words if pts_start <= (w.t0 + w.t1) / 2 < end]
        words = tuple(self._word_cls(t, a, b, False, 0) for t, a, b in got)
        return Transcript(pts_start, end, " ".join(t for t, _, _ in got), words, "hi", self.provider, lat)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- one clip through the pipeline
@dataclass
class ClipRun:
    spans: list[dict[str, Any]]
    masks: list[dict[str, Any]]
    timings: dict[str, list[float]]
    latencies: list[float]
    stats: dict[str, int]
    leaked_s: float
    compute_s: float
    vad: str


DEFECTS = (
    "late_spans",
    "no_failsafe",
    "no_mouth",
)  # seeded into the reference build: the gate must FAIL with each


def reference_run(clip: ClipTruth, mode: str, defect: str) -> ClipRun:
    """A stand-in audio module that is right by construction (used to validate the harness, like eval/refengine.py):
    PII words muted at once (``api``/``local_only``) or every voiced word muted by the fail-safe (``api_down``).
    Seeded defects: ``late_spans`` (spans ready after the samples left), ``no_failsafe`` (nothing muted when the
    API is down), ``no_mouth`` (no mouth-blur requests)."""
    spans: list[dict[str, Any]] = []
    masks: list[dict[str, Any]] = []
    if mode == "api_down" and defect == "no_failsafe":
        return ClipRun([], [], {}, [], {}, 0.0, 0.0, "reference")
    items = [
        (w.t0, w.t1) for w in clip.words if mode == "api_down" or w.pii
    ]  # word by word: ready right after each word
    for t0, t1 in items:
        ready = t1 + (DELAY_S + 1.0 if defect == "late_spans" else 0.5)
        gran = "failsafe" if mode == "api_down" else "word"
        spans.append(
            {
                "id": f"ref-{len(spans)}",
                "t0": max(0.0, t0 - 0.2),
                "t1": t1 + 0.2,
                "ready": ready,
                "gran": gran,
                "cat": "spoken_phone",
            }
        )
        if defect != "no_mouth":
            masks.append(
                {"t0": max(0.0, t0 - 0.2), "t1": t1 + 0.2, "ready": ready, "kind": "mouth_of_speaker"}
            )
    lat = [sp["ready"] - sp["t0"] for sp in spans]
    return ClipRun(spans, masks, {"stt": [0.7]}, lat or [0.0], {"spans": len(spans)}, 0.0, 0.0, "reference")


def run_clip(
    clip: ClipTruth, pcm: np.ndarray, mode: str, seed: int, defect: str = "", reference: bool = False
) -> ClipRun:
    if reference:
        return reference_run(clip, mode, defect)
    from audio.orchestrator.pipeline import AudioPipeline  # noqa: PLC0415
    from audio.orchestrator.race import SttRace  # noqa: PLC0415
    from audio.pii.detector import SpokenPiiDetector  # noqa: PLC0415
    from audio.pii.rules import SpokenRules  # noqa: PLC0415
    from audio.segmenter import Segmenter  # noqa: PLC0415
    from audio.stt.base import SttProviderError  # noqa: PLC0415
    from audio.testing import feed_realtime, run_virtual  # noqa: PLC0415
    from audio.vad import EnergyFrameModel  # noqa: PLC0415
    from contracts.config import Config  # noqa: PLC0415
    from contracts.enums import Topic  # noqa: PLC0415
    from contracts.fakes import InProcessBus  # noqa: PLC0415

    down = SttProviderError("down", 500)
    api, loc = API_STT, LOCAL_STT
    plan = {
        "api": (api, None, loc, None),
        "perfect": (PERFECT, None, PERFECT, None),
        "api_down": (api, down, loc, down),
        "local_only": (api, down, loc, None),
    }[mode]
    cfg = Config(delay_seconds=DELAY_S)  # constructed directly: no .env, no keys, offline
    vad_name = "silero"
    try:
        seg = Segmenter.from_config(cfg)
    except Exception:  # missing Silero weights: the product falls back to the energy detector too
        seg = Segmenter.from_config(cfg, EnergyFrameModel())
        vad_name = "energy"
    state: dict[str, Any] = {}

    async def main(loop: Any) -> None:
        bus = InProcessBus()
        sar = OracleStt(clip.words, "sarvam", plan[0], seed, plan[1])
        loc = OracleStt(clip.words, "local", plan[2], seed + 1, plan[3])
        race = SttRace(
            {"sarvam": sar, "local": loc},  # type: ignore[dict-item]
            order=("sarvam", "local"),
            hedge_s=cfg.audio.race_hedge_s,
            clock=loop.time,
        )
        det = SpokenPiiDetector(
            SpokenRules(watch_window_s=cfg.audio.watch_window_s, address_mute=cfg.audio.address_mute)
        )
        pipe = AudioPipeline(cfg, bus, seg, race, det, clock=loop.time)
        t0 = loop.time()
        await feed_realtime(pipe, pcm, pts0=0.0)
        await asyncio.sleep(DELAY_S + 1.0)
        await pipe.drain()
        state.update(t0=t0, bus=bus, pipe=pipe)

    c0 = time.process_time()
    run_virtual(main)
    compute = time.process_time() - c0
    t0, bus, pipe = state["t0"], state["bus"], state["pipe"]
    spans = [
        {
            "id": e.span_id,
            "t0": e.pts_start,
            "t1": e.pts_end,
            "ready": e.completed_at - t0,
            "gran": e.granularity,
            "cat": e.category.value,
        }
        for tp, e in bus.history
        if tp is Topic.MUTE_SPAN
    ]
    masks = [
        {"t0": e.pts_start, "t1": e.pts_end, "ready": e.completed_at - t0, "kind": e.kind.value}
        for tp, e in bus.history
        if tp is Topic.MASK_REQUEST
    ]
    timings: dict[str, list[float]] = defaultdict(list)
    for tp, e in bus.history:
        if tp is Topic.TIMING and e.stage in ("stt", "pii_rules", "pii_llm"):
            timings[e.stage].append(e.t_end - e.t_start)
    return ClipRun(
        spans, masks, dict(timings), list(pipe.latencies), dict(pipe.stats), pipe.leaked_s, compute, vad_name
    )


# --------------------------------------------------------------------------- scoring one clip
def output_audio(pcm: np.ndarray, spans: list[dict[str, Any]]) -> tuple[np.ndarray, int]:
    """The 48 kHz audio that leaves the delay buffer: mutes applied by the engine's MuteBook, a span being added
    only once it is ready. Returns (float signal, number of spans that were late or partly late)."""
    from contracts.detection import MuteSpan  # noqa: PLC0415
    from contracts.enums import Category  # noqa: PLC0415
    from engine.audio_mute import MuteBook  # noqa: PLC0415

    up = np.clip(np.rint(resample_poly(pcm.astype(np.float64), OUT_RATE // 16_000, 1)), -32768, 32767).astype(
        np.int16
    )
    out = up.copy()
    book = MuteBook()
    order = sorted(spans, key=lambda s: s["ready"])
    ptr = late = 0
    step = OUT_RATE // 30
    for k in range(0, len(out), step):
        t_emit = k / OUT_RATE + DELAY_S
        while ptr < len(order) and order[ptr]["ready"] <= t_emit:
            s = order[ptr]
            ptr += 1
            outcome = book.add(
                MuteSpan(
                    s["id"], s["t0"], s["t1"], Category(s["cat"]), s["gran"], 0.9, "eval", 0.0, "silence"
                )
            )
            late += int(outcome.late or outcome.leaked_samples > 0)
        book.apply(out[k : k + step], k)
    # spans that were ready only after the last frame left count as late too
    late += len(order) - ptr
    return out.astype(np.float64) / 32768.0, late


def mouth_blur(clip: ClipTruth, run: ClipRun) -> tuple[int, int]:
    """(frames with a resolved mouth quad, frames muted while an allowlisted face is visible), 30 fps frames.

    Vision is modelled by a perfect face detector on every 3rd frame; the audio side supplies the request windows
    and the engine's resolver picks the quads, so this checks the audio->engine hand-off, not pixels."""
    from contracts import Category, Detection, Lane, LaneResult, Quad  # noqa: PLC0415
    from engine.mouth import resolve_mouth_quads  # noqa: PLC0415

    n_frames = int(clip.duration_s * FRAME_FPS)
    visible = lambda p: any(a <= p < b for a, b in clip.face_ranges)  # noqa: E731
    results: dict[int, LaneResult] = {}
    for f in range(0, n_frames, 3):
        p = f / FRAME_FPS
        if visible(p):
            det = Detection(
                "eval", Category.FACE, Quad.from_xyxy(500, 200, 780, 520), 0.9, f, p, p + 0.05,
                allow=True, aux_quads={"mouth": Quad.from_xyxy(590, 400, 690, 460)},
            )  # fmt: skip
            results[f] = LaneResult(Lane.FAST, f, p, p + 0.05, True, aux_detections=(det,))
    max_age = {Lane.FAST: 6, Lane.ACCURATE: 12, Lane.SLOW: 60}
    ident = lambda flat, src, dst, dl: flat  # noqa: E731
    total = got = 0
    mouth = [m for m in run.masks if m["kind"] == "mouth_of_speaker"]
    for f in range(n_frames):
        p = f / FRAME_FPS
        if not visible(p) or not any(s["t0"] <= p <= s["t1"] for s in run.spans):
            continue
        total += 1
        reqs = [m for m in mouth if m["t0"] <= p <= m["t1"] and m["ready"] <= p + DELAY_S]
        if not reqs:
            continue
        near = [results[g] for g in range(max(0, f - 12), f + 13) if g in results]
        if resolve_mouth_quads(near, f, p + DELAY_S, (0, n_frames), max_age, ident):
            got += 1
    return got, total


def pts_cover(spans: list[dict[str, Any]], a: float, b: float) -> float:
    """Fraction of [a, b] inside the union of the spans (timeline coverage, ignoring when the span became ready)."""
    pieces = sorted((max(s["t0"], a), min(s["t1"], b)) for s in spans if s["t1"] > a and s["t0"] < b)
    total, cur = 0.0, a
    for lo, hi in pieces:
        lo = max(lo, cur)
        if hi > lo:
            total += hi - lo
            cur = hi
    return total / max(1e-9, b - a)


@dataclass
class Tally:
    n: int = 0
    covered: int = 0
    dur: float = 0.0
    cov_dur: float = 0.0

    def add(self, d: float, ok: bool) -> None:
        self.n += 1
        self.dur += d
        if ok:
            self.covered += 1
            self.cov_dur += d

    def asdict(self) -> dict[str, Any]:
        r = metrics.ratio(self.covered, self.n)
        return {
            "n": self.n,
            "covered": self.covered,
            "word_recall": r.value,
            "ci": [round(r.ci_lo, 4), round(r.ci_hi, 4)],
            "dur_coverage": (self.cov_dur / self.dur) if self.dur else None,
        }


def score_clip(clip: ClipTruth, env: np.ndarray) -> dict[str, Any]:
    """Per-word coverage flags and the over-mute numerator/denominator for one clip's output envelope."""
    rows = []
    for w in clip.words:
        frac = metrics.muted_fraction(env, HOP_S, w.t0, w.t1)
        rows.append((w, frac >= metrics.MUTE_MIN_FRACTION))
    non_pii = [(w.t0, w.t1) for w in clip.words if not w.pii]
    sec_total = sec_muted = 0.0
    for a, b in non_pii:
        seg = env[max(0, int(round(a / HOP_S))) : min(len(env), int(round(b / HOP_S)))]
        sec_total += seg.size * HOP_S
        sec_muted += float((seg <= metrics.MUTE_THRESHOLD_DBFS).sum()) * HOP_S
    hn = [(w.t0, w.t1) for w in clip.words if w.hard_neg]
    hn_total = hn_muted = 0.0
    for a, b in hn:
        seg = env[max(0, int(round(a / HOP_S))) : min(len(env), int(round(b / HOP_S)))]
        hn_total += seg.size * HOP_S
        hn_muted += float((seg <= metrics.MUTE_THRESHOLD_DBFS).sum()) * HOP_S
    return {
        "rows": rows,
        "sec_total": sec_total,
        "sec_muted": sec_muted,
        "hn_total": hn_total,
        "hn_muted": hn_muted,
    }


# --------------------------------------------------------------------------- legs
def _pct(xs: list[float], q: float) -> float | None:
    return round(float(np.percentile(np.asarray(xs), q)), 4) if xs else None


def _select(clips: list[ClipTruth], limit: int | None, stride: int) -> list[ClipTruth]:
    sel = clips[::stride] if stride > 1 else list(clips)
    return sel[:limit] if limit else sel


def _clip_pcm(data: Path, clip: ClipTruth) -> np.ndarray:
    pcm, rate = sf.read(str(data / clip.file), dtype="int16")
    if rate != 16_000:
        raise RuntimeError("dataset clip is not 16 kHz")
    return np.asarray(pcm, dtype=np.int16)


def run_leg(
    leg: str, data: Path, limit: int | None, stride: int, seed: int, defect: str = "", reference: bool = False
) -> dict[str, Any]:
    mode = {"main": "api", "api_down": "api_down", "local_only": "local_only", "perfect": "perfect"}[leg]
    manifest, clips = load_manifest(data)
    clips = _select(clips, limit, stride)
    by: dict[str, dict[str, Tally]] = {
        k: defaultdict(Tally) for k in ("lang", "kind", "cue", "bucket", "noise", "speed")
    }
    tot_pii, tot_digit, tot_all = Tally(), Tally(), Tally()
    seqs = [0, 0]  # complete digit sequences, total digit sequences
    over_num = over_den = hn_num = hn_den = 0.0
    lat: list[float] = []
    stage: dict[str, list[float]] = defaultdict(list)
    stats: dict[str, int] = defaultdict(int)
    mouth = [0, 0]
    leaked = compute = audio_s = 0.0
    late_spans = 0
    worst: list[tuple[float, str]] = []
    reasons: dict[tuple[str, str, str], int] = defaultdict(int)
    vad_used = set()
    t_wall = time.time()
    for clip in clips:
        pcm = _clip_pcm(data, clip)
        run = run_clip(clip, pcm, mode, seed + int(clip.clip_id[1:]), defect, reference)
        vad_used.add(run.vad)
        out, late = output_audio(pcm, run.spans)
        late_spans += late
        env = metrics.rms_dbfs_envelope(out, OUT_RATE, HOP_S)
        sc = score_clip(clip, env)
        missed = 0.0
        flags: dict[int, list[bool]] = defaultdict(list)
        for w, ok in sc["rows"]:
            d = w.t1 - w.t0
            tot_all.add(d, ok)
            if w.pii:
                tot_pii.add(d, ok)
                if w.digit:
                    tot_digit.add(d, ok)
                if w.span is not None:
                    flags[w.span].append(ok)
                    sp = clip.spans[w.span]
                    for key, val in (
                        ("lang", clip.lang),
                        ("kind", w.kind),
                        ("cue", sp.cue),
                        ("bucket", clip.bucket),
                        ("noise", clip.noise),
                        ("speed", f"{clip.speed}"),
                    ):
                        by[key][str(val)].add(d, ok)
                if not ok:
                    missed += d
                    cov = pts_cover(run.spans, w.t0, w.t1)
                    why = (
                        "no_span"
                        if cov < 0.05
                        else ("partial_span" if cov < metrics.MUTE_MIN_FRACTION else "late_span")
                    )
                    reasons[(why, w.kind or "", clip.spans[w.span].cue if w.span is not None else "")] += 1
        for sid, fl in flags.items():
            if clip.spans[sid].kind in ("phone", "otp", "aadhaar"):
                seqs[1] += 1
                seqs[0] += int(all(fl))
        over_num += sc["sec_muted"]
        over_den += sc["sec_total"]
        hn_num += sc["hn_muted"]
        hn_den += sc["hn_total"]
        lat += run.latencies
        for k, v in run.timings.items():
            stage[k] += v
        for k, v in run.stats.items():
            stats[k] += v
        leaked += run.leaked_s
        compute += run.compute_s
        audio_s += clip.duration_s
        if mode == "api":
            g, tot = mouth_blur(clip, run)
            mouth[0] += g
            mouth[1] += tot
        if missed > 0:
            worst.append((missed, clip.clip_id))
    worst.sort(reverse=True)
    result: dict[str, Any] = {
        "leg": leg,
        "mode": mode,
        "defect": defect,
        "reference": reference,
        "n_clips": len(clips),
        "audio_s": round(audio_s, 1),
        "vad": sorted(vad_used),
        "stt_model": "oracle: "
        + json.dumps({"api": API_STT.__dict__, "local": LOCAL_STT.__dict__}, default=list),
        "pii": tot_pii.asdict(),
        "digits": tot_digit.asdict(),
        "all_speech": tot_all.asdict(),
        "digit_sequences": {
            "complete": seqs[0],
            "total": seqs[1],
            "rate": (seqs[0] / seqs[1]) if seqs[1] else None,
        },
        "over_mute": (over_num / over_den) if over_den else None,
        "over_mute_seconds": [round(over_num, 2), round(over_den, 2)],
        "hard_negative_mute": (hn_num / hn_den) if hn_den else None,
        "audio_latency_s": {
            "n": len(lat),
            "p50": _pct(lat, 50),
            "p95": _pct(lat, 95),
            "max": round(max(lat), 4) if lat else None,
        },
        "stage_s": {
            k: {"n": len(v), "p50": _pct(v, 50), "p95": _pct(v, 95), "max": round(max(v), 4)}
            for k, v in sorted(stage.items())
        },
        "pipeline_stats": dict(stats),
        "late_or_partly_late_spans": late_spans,
        "leaked_seconds_in_pipeline": round(leaked, 3),
        "compute_s_per_audio_s": round(compute / audio_s, 4) if audio_s else None,
        "wall_s": round(time.time() - t_wall, 1),
        "mouth_blur": {
            "covered": mouth[0],
            "frames": mouth[1],
            "rate": (mouth[0] / mouth[1]) if mouth[1] else None,
        },
        "by": {k: {kk: v.asdict() for kk, v in sorted(d.items())} for k, d in by.items()},
        "worst_clips": [{"clip": c, "missed_pii_s": round(m, 2)} for m, c in worst[:40]],
        "clips_with_misses": len(worst),
        "miss_reasons": {"/".join(k): v for k, v in sorted(reasons.items())},
    }
    return result


def leg_data(data: Path, n: int, seed: int, allow_tone: bool, sample: int) -> tuple[dict[str, Any], int]:
    try:
        manifest, clips = load_manifest(data)
    except (OSError, ValueError, KeyError):
        return {
            "leg": "data",
            "error": f"no dataset at {data}; run: python -m eval.data_gen.audio --out {data}",
        }, EXIT_ENV
    problems: list[str] = []
    t = manifest["totals"]
    if manifest.get("engine") != "flite" and not allow_tone:
        problems.append(f"engine is '{manifest.get('engine')}', not real TTS (flite)")
    if t["clips"] < n:
        problems.append(f"{t['clips']} clips < {n}")
    if t["pii_spans"] < 450:
        problems.append(f"{t['pii_spans']} PII spans < 450")
    forbidden = scan_obj(manifest)
    if forbidden:
        problems.append(f"{len(forbidden)} forbidden (possibly real) patterns")
    bad_hash = 0
    for clip in clips[:: max(1, len(clips) // max(1, sample))][:sample]:
        pcm = _clip_pcm(data, clip)
        if hashlib.sha256(pcm.tobytes()).hexdigest() != clip.pcm_sha256:
            bad_hash += 1
        if clip.words and clip.words[-1].t1 > clip.duration_s + 1e-6:
            problems.append(f"{clip.clip_id}: word beyond the end")
    if bad_hash:
        problems.append(f"{bad_hash} sampled clips do not match their manifest hash")
    out = {
        "leg": "data",
        "seed": manifest["seed"],
        "engine": manifest["engine"],
        "totals": t,
        "problems": problems,
    }
    return out, (EXIT_FAIL if problems else EXIT_OK)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.phase3", description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--leg", choices=("data", "main", "api_down", "local_only", "perfect", "report"), required=True
    )
    p.add_argument("--data", default=DEFAULT_DATA)
    p.add_argument("--out", default="")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--n", type=int, default=DEFAULT_N)
    p.add_argument("--limit", type=int, default=0, help="only the first N selected clips (smoke runs)")
    p.add_argument(
        "--stride", type=int, default=1, help="every k-th clip (api_down and local_only use a subset)"
    )
    p.add_argument("--generate", action="store_true", help="build the dataset first when it is missing")
    p.add_argument(
        "--allow-tone", action="store_true", help="accept the tone fallback engine (unit tests only)"
    )
    p.add_argument(
        "--reference", action="store_true", help="run the stand-in audio module that is right by construction"
    )
    p.add_argument(
        "--defect",
        choices=DEFECTS,
        default="",
        help="with --reference: seed a defect to prove the gate fails",
    )
    p.add_argument("--results", default="")
    p.add_argument("--report-dir", default="")
    p.add_argument("--phase-name", default="phase3")
    a = p.parse_args(argv)
    data = Path(a.data) if Path(a.data).is_absolute() else ROOT / a.data
    out = Path(a.out) if a.out else ROOT / ".tmp" / "phase3"
    out.mkdir(parents=True, exist_ok=True)
    if a.leg == "report":
        from .phase3_report import build_report  # noqa: PLC0415

        return build_report(
            out,
            Path(a.results) if a.results else None,
            a.phase_name,
            Path(a.report_dir) if a.report_dir else None,
        )
    if a.generate and not (data / "manifest.json").exists():
        try:
            build_suite(data, a.seed, a.n, "tone" if a.allow_tone else "flite")
        except (RuntimeError, OSError) as exc:
            print(f"ENV ERROR: cannot generate the dataset: {exc}", file=sys.stderr)
            return EXIT_ENV
    if a.leg == "data":
        res, rc = leg_data(data, a.n, a.seed, a.allow_tone, 10)
    else:
        if not (data / "manifest.json").exists():
            print(
                f"ENV ERROR: no dataset at {data} (python -m eval.data_gen.audio --out {data})",
                file=sys.stderr,
            )
            return EXIT_ENV
        try:
            if a.defect and not a.reference:
                print(
                    "ERROR: --defect needs --reference (defects are seeded into the stand-in build)",
                    file=sys.stderr,
                )
                return EXIT_ENV
            res = run_leg(a.leg, data, a.limit or None, a.stride, a.seed, a.defect, a.reference)
        except ImportError as exc:
            print(f"ENV ERROR: cannot import the audio/engine modules ({exc.name})", file=sys.stderr)
            return EXIT_ENV
        rc = EXIT_OK
    (out / f"phase3-{a.leg}.json").write_text(
        json.dumps(res, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    keys = (
        "leg",
        "n_clips",
        "pii",
        "digits",
        "all_speech",
        "over_mute",
        "audio_latency_s",
        "mouth_blur",
        "problems",
        "error",
    )
    print(json.dumps({k: res[k] for k in keys if k in res}, indent=1, default=str))
    if rc == EXIT_OK and a.leg != "data":
        from .phase3_report import evaluate  # noqa: PLC0415

        failed = [c for c in evaluate(res) if not c.passed]
        for c in failed:
            print(f"FAIL {c.name}: {c.value} {c.op} {c.target}", file=sys.stderr)
        rc = EXIT_FAIL if failed else EXIT_OK
    return rc


if __name__ == "__main__":
    sys.exit(main())
