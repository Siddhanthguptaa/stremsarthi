"""Caption translation behind one interface (docs/specs/audio.md 5.9, decision D-7).

* ``SarvamTranslator``: Sarvam ``/translate`` (Mayura for the 11 code-mixed languages, sarvam-translate for the
  rest) and ``/transliterate`` (romanised Hindi to Devanagari). Async, per-call timeout, token bucket.
* ``IndicTrans2Translator``: local distilled IndicTrans2 through CTranslate2, demo profile only. It never
  downloads anything: the model is gated on Hugging Face and needs a token the user has to supply. Without the
  converted weights, ``sentencepiece`` and ``ctranslate2`` it reports ``available = False`` and is skipped.
* ``PassthroughTranslator``: the dev-profile fallback, shows the original-language text, no model, no network.
* ``ResilientTranslator``: ordered chain with a circuit breaker per provider, a request and character budget for
  the paid API, and the passthrough as the last link. It never raises: the worst case is source-language text.

Only text that is already redacted (muted spans removed by ``redact.py``) ever reaches ``translate``. Errors
carry no text, logs carry no text. Live behaviour of both Sarvam endpoints and every part of IndicTrans2 is
UNVERIFIED against the real services (no key, no weights); the request shapes follow docs.sarvam.ai.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol

import httpx

from audio.captions.langs import IT2_CODES, MAYURA_LANGS, SARVAM_CODES, Plan
from audio.stt.base import TokenBucket

log = logging.getLogger("audio.captions")

SARVAM_BASE = "https://api.sarvam.ai"
MAYURA_MAX_CHARS = 1000
SARVAM_TRANSLATE_MAX_CHARS = 2000


class TranslateError(RuntimeError):
    """Base class. Messages never contain text, bodies or keys."""


class TranslateUnavailable(TranslateError):
    """No key, no weights, no library, or the pair is not supported."""


class TranslateRateLimited(TranslateError):
    """Local bucket empty or provider answered 429."""


class TranslateProviderError(TranslateError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class TranslateBusy(TranslateError):
    """The local model is still busy with an earlier caption: shed load instead of queueing."""


class Translator(Protocol):
    name: str
    paid: bool

    @property
    def available(self) -> bool: ...

    async def translate(self, text: str, src: str, tgt: str) -> str: ...

    async def transliterate(self, text: str, src: str, tgt: str) -> str: ...


# --------------------------------------------------------------------------- Sarvam
def pick_sarvam_model(src: str, tgt: str) -> str:
    """Mayura (code-mixed, 11 languages, ``auto`` source allowed) when both ends are in its set."""
    if tgt in MAYURA_LANGS and (src == "auto" or src in MAYURA_LANGS):
        return "mayura:v1"
    if src == "auto":
        raise TranslateUnavailable("auto source needs a Mayura language pair")
    return "sarvam-translate:v1"


class SarvamTranslator:
    name: ClassVar[str] = "sarvam"
    paid: ClassVar[bool] = True

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout_s: float = 0.8,
        rpm: float = 45.0,
        burst: int = 3,
        mode: str = "modern-colloquial",
        base_url: str = SARVAM_BASE,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._key = api_key if api_key is not None else os.environ.get("SARVAM_API_KEY", "")
        self.timeout_s = timeout_s
        self.mode = mode
        self.base_url = base_url.rstrip("/")
        self._client = client
        self._owns_client = client is None
        self.bucket = TokenBucket(rpm, burst, clock)

    @classmethod
    def from_config(cls, config: Any, **kwargs: Any) -> SarvamTranslator:
        key = config.sarvam_api_key.get_secret_value() if getattr(config, "sarvam_api_key", None) else ""
        return cls(key, **kwargs)

    @property
    def available(self) -> bool:
        return bool(self._key)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_s),
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _post(self, path: str, payload: dict[str, Any], field: str) -> str:
        if not self._key:
            raise TranslateUnavailable("SARVAM_API_KEY is not set")
        if not self.bucket.try_acquire():
            raise TranslateRateLimited("local token bucket empty")
        try:
            resp = await asyncio.wait_for(
                self._http().post(
                    f"{self.base_url}/{path}",
                    json=payload,
                    headers={"api-subscription-key": self._key},
                    timeout=self.timeout_s,
                ),
                timeout=self.timeout_s,
            )
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise TimeoutError("sarvam caption call timed out") from exc
        except httpx.HTTPError as exc:
            raise TranslateProviderError(f"sarvam transport error ({type(exc).__name__})") from None
        if resp.status_code == 429:
            raise TranslateRateLimited("provider rate limit (429)")
        if not 200 <= resp.status_code < 300:
            raise TranslateProviderError(f"provider error (HTTP {resp.status_code})", resp.status_code)
        try:
            out = str(resp.json()[field]).strip()
        except (ValueError, KeyError, TypeError, AttributeError):
            raise TranslateProviderError("sarvam returned an unusable body", resp.status_code) from None
        if not out:
            raise TranslateProviderError("sarvam returned an empty result", resp.status_code)
        return out

    @staticmethod
    def _code(lang: str) -> str:
        if lang == "auto":
            return "auto"
        try:
            return SARVAM_CODES[lang]
        except KeyError:
            raise TranslateUnavailable("language not supported by sarvam") from None

    async def translate(self, text: str, src: str, tgt: str) -> str:
        model = pick_sarvam_model(src, tgt)
        limit = MAYURA_MAX_CHARS if model == "mayura:v1" else SARVAM_TRANSLATE_MAX_CHARS
        if len(text) > limit:
            raise TranslateProviderError("input longer than the model limit")
        payload: dict[str, Any] = {
            "input": text,
            "source_language_code": self._code(src),
            "target_language_code": self._code(tgt),
            "model": model,
            "mode": self.mode if model == "mayura:v1" else "formal",
            "numerals_format": "international",
        }
        return await self._post("translate", payload, "translated_text")

    async def transliterate(self, text: str, src: str, tgt: str) -> str:
        payload = {
            "input": text,
            "source_language_code": self._code(src),
            "target_language_code": self._code(tgt),
            "numerals_format": "international",
        }
        return await self._post("transliterate", payload, "transliterated_text")


# --------------------------------------------------------------------------- IndicTrans2 (local, demo only)
def captions_weights_dir() -> Path:
    """Where the converted IndicTrans2 models live (gitignored). Override with ``SS_CAPTION_WEIGHTS``."""
    override = os.environ.get("SS_CAPTION_WEIGHTS")
    return (
        Path(override)
        if override
        else Path(__file__).resolve().parents[2] / "models" / "weights" / "captions"
    )


# one converted CTranslate2 directory per direction (the model family has three distilled checkpoints)
IT2_DIRS = {
    "en-indic": "indictrans2-en-indic-dist-200M-ct2",
    "indic-en": "indictrans2-indic-en-dist-200M-ct2",
    "indic-indic": "indictrans2-indic-indic-dist-320M-ct2",
}


def it2_direction(src: str, tgt: str) -> str:
    if src == "en":
        return "en-indic"
    if tgt == "en":
        return "indic-en"
    return "indic-indic"


class It2Backend(Protocol):
    """Synchronous, blocking: always called from the worker thread."""

    def available(self) -> bool: ...

    def translate(self, text: str, src_code: str, tgt_code: str, direction: str) -> str: ...


class Ct2Backend:
    """CTranslate2 + SentencePiece runtime for converted IndicTrans2 checkpoints. UNVERIFIED against real weights.

    Expected layout under ``root / IT2_DIRS[direction]``: CTranslate2 ``model.bin`` + ``config.json`` + vocab,
    and the SentencePiece models ``model.SRC`` and ``model.TGT`` from the Hugging Face repository (gated: the
    user must accept the terms and supply ``HF_TOKEN`` to obtain them; this code never downloads).
    """

    def __init__(self, root: Path | None = None, device: str = "cpu", compute_type: str = "int8") -> None:
        self.root = root or captions_weights_dir()
        self.device = device
        self.compute_type = compute_type
        self._models: dict[str, tuple[Any, Any, Any]] = {}

    @staticmethod
    def _libs() -> bool:
        return all(importlib.util.find_spec(m) is not None for m in ("ctranslate2", "sentencepiece"))

    def _dir(self, direction: str) -> Path:
        return self.root / IT2_DIRS[direction]

    def missing(self) -> list[str]:
        """Human-readable list of what is absent (codes only, safe to log)."""
        out: list[str] = []
        for m in ("ctranslate2", "sentencepiece"):
            if importlib.util.find_spec(m) is None:
                out.append(f"python:{m}")
        for name in IT2_DIRS.values():
            if not (self.root / name / "model.bin").exists():
                out.append(f"weights:{name}")
        return out

    def available(self) -> bool:
        return self._libs() and any((self.root / n / "model.bin").exists() for n in IT2_DIRS.values())

    def _load(self, direction: str) -> tuple[Any, Any, Any]:
        if direction in self._models:
            return self._models[direction]
        d = self._dir(direction)
        if not (d / "model.bin").exists() or not self._libs():
            raise TranslateUnavailable("indictrans2 weights or libraries missing")
        import ctranslate2  # noqa: PLC0415
        import sentencepiece as spm  # noqa: PLC0415

        tr = ctranslate2.Translator(str(d), device=self.device, compute_type=self.compute_type)
        src_sp = spm.SentencePieceProcessor(model_file=str(d / "model.SRC"))
        tgt_sp = spm.SentencePieceProcessor(model_file=str(d / "model.TGT"))
        self._models[direction] = (tr, src_sp, tgt_sp)
        return self._models[direction]

    def translate(self, text: str, src_code: str, tgt_code: str, direction: str) -> str:
        tr, src_sp, tgt_sp = self._load(direction)
        tokens = [src_code, tgt_code, *src_sp.encode(text, out_type=str), "</s>"]
        res = tr.translate_batch([tokens], beam_size=2, max_decoding_length=160)
        hyp = list(res[0].hypotheses[0])
        while hyp and hyp[0] in (src_code, tgt_code):
            hyp.pop(0)
        out = str(tgt_sp.decode(hyp)).strip()
        if not out:
            raise TranslateProviderError("indictrans2 returned an empty result")
        return out


class IndicTrans2Translator:
    name: ClassVar[str] = "indictrans2"
    paid: ClassVar[bool] = False

    def __init__(self, backend: It2Backend | None = None, *, device: str = "cpu") -> None:
        self.backend: It2Backend = backend or Ct2Backend(device=device)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="it2")
        self._busy = threading.Lock()

    @property
    def available(self) -> bool:
        try:
            return bool(self.backend.available())
        except Exception:
            return False

    async def translate(self, text: str, src: str, tgt: str) -> str:
        try:
            a, b = IT2_CODES[src], IT2_CODES[tgt]
        except KeyError:
            raise TranslateUnavailable("language not supported by indictrans2") from None
        if not self._busy.acquire(blocking=False):
            raise TranslateBusy("indictrans2 is still busy")
        direction = it2_direction(src, tgt)
        loop = asyncio.get_running_loop()

        def job() -> str:
            try:
                return self.backend.translate(text, a, b, direction)
            finally:
                self._busy.release()

        try:
            fut = loop.run_in_executor(self._pool, job)
        except Exception:
            self._busy.release()
            raise
        return await fut

    async def transliterate(self, text: str, src: str, tgt: str) -> str:
        raise TranslateUnavailable("indictrans2 does not transliterate")

    async def aclose(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


# --------------------------------------------------------------------------- passthrough
class PassthroughTranslator:
    """Dev-profile fallback: original-language captions. Stateless, always available."""

    name: ClassVar[str] = "passthrough"
    paid: ClassVar[bool] = False
    available = True

    async def translate(self, text: str, src: str, tgt: str) -> str:
        return text

    async def transliterate(self, text: str, src: str, tgt: str) -> str:
        return text


# --------------------------------------------------------------------------- budget, breaker, chain
class CharBudget:
    """Characters per minute and per session for the paid API (2 INR per 1,000 characters)."""

    def __init__(self, per_min: int, session_cap: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_min = per_min
        self.session_cap = session_cap
        self._clock = clock
        self._spent: deque[tuple[float, int]] = deque()
        self.total = 0

    def try_spend(self, n: int) -> bool:
        now = self._clock()
        while self._spent and now - self._spent[0][0] > 60.0:
            self._spent.popleft()
        in_min = sum(c for _, c in self._spent)
        if self.total + n > self.session_cap or in_min + n > self.per_min:
            return False
        self._spent.append((now, n))
        self.total += n
        return True


class Breaker:
    def __init__(self, failures: int, open_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.failures, self.open_s, self._clock = failures, open_s, clock
        self._n = 0
        self._until = 0.0

    def allow(self) -> bool:
        return self._clock() >= self._until

    def ok(self) -> None:
        self._n = 0

    def fail(self) -> None:
        self._n += 1
        if self._n >= self.failures:
            self._until = self._clock() + self.open_s
            self._n = 0


@dataclass(frozen=True, slots=True)
class Converted:
    text: str
    lang: str  # language the text is in (the source language when ``provider`` is ``passthrough``)
    provider: str
    degraded: bool  # True: the caption is the original-language text because nothing else worked


class ResilientTranslator:
    def __init__(
        self,
        chain: Sequence[Translator],
        *,
        budget: CharBudget | None = None,
        failures: int = 3,
        open_s: float = 20.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.chain = [t for t in chain if t.name != "passthrough"]
        self.budget = budget
        self._clock = clock
        self.breakers = {t.name: Breaker(failures, open_s, clock) for t in self.chain}
        self.stats: dict[str, int] = dict.fromkeys(
            ("calls", "ok", "timeouts", "errors", "rate_limited", "budget_denied", "degraded"), 0
        )

    async def convert(self, text: str, plan: Plan, timeout_s: float) -> Converted:
        """Convert ``text`` as ``plan`` says within ``timeout_s`` overall; never raises."""
        deadline = self._clock() + timeout_s
        for t in self.chain:
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            br = self.breakers[t.name]
            if not t.available or not br.allow():
                continue
            if plan.action == "transliterate" and t.name != "sarvam":
                continue
            if t.paid and self.budget is not None and not self.budget.try_spend(len(text)):
                self.stats["budget_denied"] += 1
                continue
            self.stats["calls"] += 1
            try:
                if plan.action == "transliterate":
                    coro = t.transliterate(text, plan.src, plan.tgt)
                else:
                    coro = t.translate(text, plan.src, plan.tgt)
                out = await asyncio.wait_for(coro, remaining)
            except (TimeoutError, httpx.TimeoutException):
                self.stats["timeouts"] += 1
                br.fail()
                continue
            except TranslateRateLimited:
                self.stats["rate_limited"] += 1
                continue  # our own bucket or a 429: not the provider being down
            except TranslateUnavailable:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # any other failure degrades, never breaks the caption path
                self.stats["errors"] += 1
                br.fail()
                log.info("caption provider %s failed (%s)", t.name, type(exc).__name__)
                continue
            out = out.strip()
            if not out:
                self.stats["errors"] += 1
                br.fail()
                continue
            br.ok()
            self.stats["ok"] += 1
            return Converted(out, plan.out_lang, t.name, False)
        self.stats["degraded"] += 1
        return Converted(text, plan.src if plan.src != "auto" else "unknown", "passthrough", True)

    async def aclose(self) -> None:
        for t in self.chain:
            closer = getattr(t, "aclose", None)
            if closer is not None:
                try:
                    await closer()
                except Exception as exc:
                    log.debug("closing a caption translator failed: %s", type(exc).__name__)


def build_translator(
    config: Any, settings: Any, *, clock: Callable[[], float] = time.monotonic
) -> ResilientTranslator:
    """Dev: Sarvam when a key exists, else passthrough. Demo: IndicTrans2 joins the chain when its weights
    exist (the order follows ``captions.provider``). No key and no weights: passthrough only, no error."""
    chain: list[Translator] = []
    sarvam = SarvamTranslator.from_config(
        config, timeout_s=settings.translate_timeout_s, rpm=settings.rpm, burst=settings.burst, clock=clock
    )
    it2: IndicTrans2Translator | None = None
    if settings.profile == "demo":
        device = "cuda" if getattr(getattr(config, "gpu", None), "enabled", False) else "cpu"
        it2 = IndicTrans2Translator(device=device)
        if not it2.available:
            log.info("IndicTrans2 not available (weights or libraries missing): captions use Sarvam only")
            it2 = None
    order = [it2, sarvam] if settings.provider == "indictrans2" else [sarvam, it2]
    chain = [t for t in order if t is not None and t.available]
    return ResilientTranslator(
        chain,
        budget=CharBudget(settings.chars_per_min, settings.session_char_cap, clock),
        failures=settings.breaker_failures,
        open_s=settings.breaker_open_s,
        clock=clock,
    )
