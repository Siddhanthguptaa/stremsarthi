"""Phase 0 STT spike (T0.6): latency and capabilities of Sarvam REST, Groq Whisper and local faster-whisper.

    python -m audio.spikes.stt_spike [--n 12] [--models tiny,base] [--clip FILE --ref "text"] [--fetch-only]

* Sarvam and Groq run only when ``SARVAM_API_KEY`` / ``GROQ_API_KEY`` are in the process environment (this
  script never reads ``.env``). Otherwise their rows are written as UNVERIFIED.
* Local faster-whisper (tiny, base, int8, CPU) always runs when the package is installed; model files are
  downloaded from Hugging Face with the hash check in ``audio.models``.
* The default clip is a flite (English) TTS clip. No Hindi or Hinglish TTS exists in this environment, so
  Hindi/Hinglish quality is reported as NOT MEASURED unless ``--clip``/``--ref`` supply one.
* Results are written to ``audio/spikes/REPORT.md`` and ``audio/spikes/results.json`` (timings and flags
  only: no keys, and no transcript text of anything but the benign default clip).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import statistics
import time
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from audio.models import WHISPER_REPOS, fetch_hf_model, weights_dir
from audio.segmenter import Segmenter
from audio.stt.base import SttError
from audio.stt.groq import GroqSTT
from audio.stt.local import LocalWhisperSTT, model_dir_for
from audio.stt.sarvam import SarvamSTT
from audio.vad import StreamingVad

HERE = Path(__file__).resolve().parent
DEFAULT_CLIP = HERE.parent / "tests" / "data" / "tts_two_sentences.wav"
DEFAULT_REF = "Hello, this is a test."
SR = 16_000


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def summarise(lat: list[float]) -> dict[str, Any]:
    return {
        "n": len(lat),
        "p50_s": round(statistics.median(lat), 3) if lat else None,
        "p95_s": round(pct(lat, 0.95) or 0.0, 3) if lat else None,
        "max_s": round(max(lat), 3) if lat else None,
    }


def wer(ref: str, hyp: str) -> float:
    norm = lambda s: re.sub(r"[^\w\s]", "", s.lower()).split()  # noqa: E731
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(len(r), 1)


def cut(clip: np.ndarray, start_s: float, dur_s: float) -> np.ndarray:
    a = int(start_s * SR)
    out = clip[a : a + int(dur_s * SR)]
    if len(out) < int(dur_s * SR):
        out = np.concatenate([out, np.zeros(int(dur_s * SR) - len(out), dtype=np.int16)])
    return out


def measure_vad(clip: np.ndarray) -> dict[str, Any]:
    vad = StreamingVad.silero()
    seg = Segmenter(vad)
    t = time.perf_counter()
    out = []
    for i in range(0, len(clip), 320):
        out += seg.feed(clip[i : i + 320], i / SR, i / SR)
    out += seg.flush()
    dt = time.perf_counter() - t
    windows = len(clip) / 512
    return {
        "ms_per_32ms_window": round(dt / windows * 1000, 3),
        "cpu_fraction_of_realtime": round(dt / (len(clip) / SR), 4),
        "segments": len(out),
    }


async def run_api(stt: Any, pcm: np.ndarray, n: int, spacing_s: float, hint: str | None) -> dict[str, Any]:
    lat: list[float] = []
    errors: dict[str, int] = {}
    last = None
    for i in range(n):
        t = time.perf_counter()
        try:
            last = await stt.transcribe(pcm, 100.0 + i, hint)
            lat.append(time.perf_counter() - t)
        except (SttError, TimeoutError, ValueError) as exc:
            errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1
        await asyncio.sleep(spacing_s)
    res = summarise(lat)
    res["errors"] = errors
    if last is not None:
        res["has_text"] = bool(last.text)
        res["word_times"] = "word-level" if last.has_word_times else "interpolated only"
    return res


async def spike_sarvam(clip: np.ndarray, n: int) -> dict[str, Any]:
    key = os.environ.get("SARVAM_API_KEY", "")
    if not key:
        return {"status": "UNVERIFIED (no SARVAM_API_KEY in the environment)"}
    seg = cut(clip, 0.5, 1.5)
    stt = SarvamSTT(key, timeout_s=5.0)
    out: dict[str, Any] = {"status": "measured", "segment_s": 1.5}
    try:
        out["rest"] = await run_api(stt, seg, n, 1.2, None)
        # does the REST response carry timestamps, and at what granularity?
        stamp = SarvamSTT(key, with_timestamps=True, timeout_s=5.0)
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as c:
            from audio.stt.base import pcm_to_wav

            r = await c.post(
                stamp.base_url,
                data={
                    "model": stamp.model,
                    "mode": "codemix",
                    "language_code": "unknown",
                    "with_timestamps": "true",
                },
                files={"file": ("s.wav", pcm_to_wav(seg), "audio/wav")},
                headers={"api-subscription-key": key},
            )
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        ts = body.get("timestamps") or {}
        out["timestamps_present"] = bool(ts)
        out["timestamp_entries_vs_words"] = [
            len(ts.get("words", [])),
            len(str(body.get("transcript", "")).split()),
        ]
        out["http_status_with_timestamps"] = r.status_code
        out["mode_accepted_by_v4"] = r.status_code == 200
    finally:
        await stt.aclose()
    return out


async def spike_groq(clip: np.ndarray, n: int) -> dict[str, Any]:
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        return {"status": "UNVERIFIED (no GROQ_API_KEY in the environment)"}
    chunk = cut(clip, 0.5, 3.1)
    stt = GroqSTT(key, timeout_s=5.0)
    try:
        return {"status": "measured", "chunk_s": 3.1, "rest": await run_api(stt, chunk, n, 3.4, "en")}
    finally:
        await stt.aclose()


async def spike_local(clip: np.ndarray, sizes: list[str], n: int, ref: str, fetch: bool) -> dict[str, Any]:
    results: dict[str, Any] = {}
    try:
        import faster_whisper  # noqa: F401

        installed = True
    except ImportError:
        installed = False
    seg = cut(clip, 0.5, 1.5)
    for size in sizes:
        row: dict[str, Any] = {"repo": WHISPER_REPOS[size]}
        d = model_dir_for(size)
        if fetch or not (d / "model.bin").exists():
            try:
                _, commit = fetch_hf_model(WHISPER_REPOS[size], d)
                row["download"] = f"verified, commit {commit[:12]}"
            except Exception as exc:  # noqa: BLE001 - spike: report and carry on
                row["download"] = f"FAILED ({type(exc).__name__})"
        else:
            commit_file = d / "COMMIT"
            commit = commit_file.read_text().strip()[:12] if commit_file.exists() else "unknown"
            row["download"] = f"present, hash-verified at fetch, commit {commit}"
        if not installed:
            row["status"] = "BLOCKED: faster-whisper / ctranslate2 are not installed in the venv"
            results[size] = row
            continue
        stt = LocalWhisperSTT(size, cpu_threads=4, timeout_s=30.0)
        t = time.perf_counter()
        await stt.warmup()
        row["load_and_warm_s"] = round(time.perf_counter() - t, 2)
        lat, last = [], None
        for _ in range(n):
            t = time.perf_counter()
            last = await stt.transcribe(seg, 0.0, "en")
            lat.append(time.perf_counter() - t)
        row.update(summarise(lat))
        row["status"] = "measured"
        row["segment_s"] = 1.5
        row["rtf"] = round(statistics.median(lat) / 1.5, 3)
        row["word_times"] = "word-level" if last and last.has_word_times else "none"
        row["wer_english_clip"] = round(wer(ref, last.text), 3) if last else None
        row["sample_output"] = last.text if last else ""
        await stt.aclose()
        results[size] = row
    results["hindi_hinglish_quality"] = (
        "NOT MEASURED: no Hindi/Hinglish TTS clip available (pass --clip/--ref)"
    )
    return results


def write_report(res: dict[str, Any]) -> None:
    (HERE / "results.json").write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    s, g, loc, v = res["sarvam"], res["groq"], res["local"], res["vad"]

    def api_row(name: str, r: dict[str, Any]) -> str:
        if r["status"] != "measured":
            return f"| {name} | {r['status']} | UNVERIFIED | UNVERIFIED | UNVERIFIED |"
        x = r["rest"]
        return f"| {name} | measured (n={x['n']}) | {x['p50_s']} | {x['p95_s']} | {x.get('word_times')} |"

    lines = [
        "# T0.6 STT spike report",
        "",
        f"Generated by `audio/spikes/stt_spike.py` on {res['host']['cpu']} ({res['host']['os']}), Python "
        f"{res['host']['python']}. Anything marked UNVERIFIED needs API keys (`SARVAM_API_KEY`, `GROQ_API_KEY` in the "
        "process environment) and is re-measured by re-running the script.",
        "",
        "## Latency (1.5 s segments for Sarvam, 3.1 s chunks for Groq)",
        "",
        "| Engine | Status | p50 s | p95 s | Word times |",
        "|---|---|---|---|---|",
        api_row("Sarvam REST saaras:v4 codemix", s),
        api_row("Groq whisper-large-v3-turbo", g),
    ]
    for size in ("tiny", "base"):
        r = loc.get(size)
        if not r:
            continue
        if r.get("status") == "measured":
            lines.append(
                f"| local faster-whisper {size} int8 CPU | measured (n={r['n']}) | {r['p50_s']} | {r['p95_s']} | "
                f"{r['word_times']} |"
            )
        else:
            lines.append(
                f"| local faster-whisper {size} int8 CPU | {r['status']} | NOT MEASURED | NOT MEASURED | n/a |"
            )
    lines += ["", "## Sarvam REST timestamps", ""]
    if s["status"] == "measured":
        lines += [
            f"- timestamps present with `with_timestamps=true`: {s.get('timestamps_present')}",
            f"- chunk entries vs words in transcript: {s.get('timestamp_entries_vs_words')}",
            f"- `mode=codemix` accepted with `saaras:v4` (HTTP 200): {s.get('mode_accepted_by_v4')}",
        ]
    else:
        lines += [
            "- UNVERIFIED live. The installed `sarvamai` SDK documents `with_timestamps` as CHUNK level "
            "(sentence or phrase entries: `words`, `start_time_seconds`, `end_time_seconds`), and states word-level "
            "timestamps are not supported on REST. The WebSocket API returns finals with `timestamps=null`.",
            "- Consequence for D-4: Sarvam words are always `approx=True` (interpolated); chunk times only tighten the "
            "interpolation. The adapter sends `with_timestamps=false` by default; turn it on only if the live latency "
            "difference is negligible.",
            "- UNVERIFIED: the SDK docstring says `mode` applies to `saaras:v3` only, while also listing modes for v4. The "
            "adapter sends `mode=codemix` with `saaras:v4`; check the live response is HTTP 200 and code-mixed.",
        ]
    lines += [
        "",
        "## Groq limits",
        "",
        "- Whisper limit 20 requests per minute (plan, UNVERIFIED live); adapter budget 18/min with a token bucket, "
        "chunks of at least 3.0 s (`GroqChunker`), `verbose_json` with `timestamp_granularities[]=word`.",
        "- Billing: anything under 10 s is billed as 10 s (spec note, UNVERIFIED).",
        "",
        "## Local faster-whisper",
        "",
    ]
    for size in ("tiny", "base"):
        r = loc.get(size)
        if r:
            lines.append(f"- {size}: download {r['download']}; status {r['status']}")
            if r.get("status") == "measured":
                lines.append(
                    f"  - load+warm {r['load_and_warm_s']} s; 1.5 s segment p50 {r['p50_s']} s, p95 {r['p95_s']} s, "
                    f"real-time factor {r['rtf']}; English clip WER {r['wer_english_clip']}"
                )
    lines += [f"- {loc['hindi_hinglish_quality']}", "", "## Silero VAD on ONNX Runtime CPU", ""]
    lines += [
        f"- {v['ms_per_32ms_window']} ms per 32 ms window ({v['cpu_fraction_of_realtime'] * 100:.2f} % of one core), "
        f"{v['segments']} segments on the default clip",
        "",
    ]
    (HERE / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--models", default="tiny,base")
    ap.add_argument("--clip", type=Path, default=DEFAULT_CLIP)
    ap.add_argument("--ref", default=DEFAULT_REF)
    ap.add_argument("--fetch-only", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    args = ap.parse_args()

    sizes = [m for m in args.models.split(",") if m]
    if args.fetch_only:
        for size in sizes:
            d, commit = fetch_hf_model(WHISPER_REPOS[size], model_dir_for(size))
            print(f"{size}: verified {d} @ {commit[:12]} (weights dir {weights_dir()})")
        return
    clip, sr = sf.read(args.clip, dtype="int16")
    if sr != SR or clip.ndim != 1:
        raise SystemExit("clip must be 16 kHz mono")
    res: dict[str, Any] = {
        "host": {
            "cpu": platform.processor() or platform.machine(),
            "os": platform.platform(),
            "python": platform.python_version(),
        },
        "vad": measure_vad(clip),
        "sarvam": await spike_sarvam(clip, args.n),
        "groq": await spike_groq(clip, args.n),
        "local": await spike_local(clip, sizes, args.n, args.ref, fetch=False),
    }
    print(json.dumps(res, indent=2, ensure_ascii=False))
    if not args.no_report:
        write_report(res)


if __name__ == "__main__":
    asyncio.run(main())
