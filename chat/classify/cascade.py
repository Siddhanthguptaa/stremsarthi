"""The classifier cascade (docs/specs/chat.md 5.1): command check, normalise, rules, scam, doxxing, LLM.

``ChatClassifier`` satisfies ``contracts.ChatClassifier`` (``async classify(batch) -> verdicts``).

* ``classify`` returns the fast verdict for every message immediately. Clear cases are final
  (``stage="rules"``). Borderline cases (0.3 <= score < 0.9) get a provisional ``HIDE_FROM_STREAMER``
  with ``reason="pending_llm"`` and are resolved in the background by the Groq stage; the final verdict
  is delivered through the ``on_update(message, verdict)`` callback (upgrade, or downgrade to ALLOW so the
  pipeline can re-emit the message on ``chat.allowed``).
* ``classify_final`` awaits the LLM stage too (used by eval and tests).
* With no LLM transport (no API key) or ``llm_enabled=false``, borderline messages are finalised locally
  as ``HOLD_FOR_MOD`` (reason ``llm_unavailable_hold``): never silently allowed.

Messages from the broadcaster and moderators are never hidden. They are still scanned for doxxing of
other people (the streamer's own private terms and phone are ignored); a hit is returned as an ALLOW
verdict that carries the doxxing category (reason ``staff_dox_flag``) so the pipeline can queue it for
moderators without hiding it.

Nothing here logs message text, private terms or phone numbers.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from chat.common import Clock, SystemClock, hash_id, log_event
from contracts import Category, ChatAction, ChatMessage, ChatVerdict
from contracts.config import ChatConfig

from .candidate import Candidate
from .decode import decoded_views
from .doxxing import DoxxingDetector
from .fold import clean_chars, windows
from .llm import LlmResult, LlmStage, LlmTransport
from .normalize import LEETISH, Normalized, lexicon_view, normalize
from .rules import RuleMatcher, default_matcher
from .scam import detect_blob, detect_scam
from .suspect import detect_shapes, detect_suspects

_LOG = logging.getLogger("chat.classify")

COMMAND_TOKENS = frozenset({"!hold", "!unhold"})
MAX_PROCESS_CHARS = 2000
TIMEOUT_SECONDS = 600
REPEAT_THRESHOLD = 3
REPEAT_WINDOW_S = 300.0
_TRACKER_CAP = 5000
_LABEL_ORDER = ("fake_upi", "scam", "doxxing", "abuse")
_LABEL_CATEGORY = {
    "fake_upi": Category.CHAT_FAKE_UPI,
    "scam": Category.CHAT_SCAM_LINK,
    "doxxing": Category.CHAT_DOXXING,
    "abuse": Category.CHAT_ABUSE,
}
#: Instruction-like text aimed at the classifier. An LLM "clean" for such a message is not trusted.
INJECTION_CUE = re.compile(
    r"(?:ignore|disregard|forget|override|bypass)\W+(?:\w+\W+){0,3}?(?:previous|prior|above|earlier|all|these|your)\W+(?:\w+\W+){0,2}?"
    r"(?:instructions?|rules?|prompts?|messages?)|(?:mark|label|classify|tag|treat|rate|output|respond|reply|answer|say)\W+(?:\w+\W+){0,6}?"
    r"(?:as\W+|with\W+|is\W+|=\W*)?(?:clean|safe|harmless|benign|allowed|not\W+abus\w+)|(?:^|\W)(?:system|assistant|developer)\s*:|"
    r"<\|?(?:im_start|im_end|system|endoftext)|new\W+instructions?|you\W+are\W+now\W|already\W+(?:reviewed|approved|checked)|"
    r"\"?(?:labels?|results|confidence)\"?\s*:\s*[\[\d\"]|pichle\W+(?:\w+\W+){0,2}?instructions?|instructions?\W+(?:\w+\W+){0,2}?(?:bhool|ignore)|"
    r"(?:isse|ise|isko)\W+clean|clean\W+mark",
    re.IGNORECASE,
)
# The same cues with every non-alphanumeric character removed: "i g n o r e  p r e v i o u s" and "ignore_previous..."
_SQUASHED_CUE = re.compile(
    r"(?:ignore|disregard|forget|override|bypass)(?:the|all|any|your|these|those)?(?:previous|prior|above|earlier)"
    r"(?:instructions?|rules?|prompts?|messages?)|(?:mark|label|classify|tag|treat|rate)(?:this|it|themessage|themsg)?"
    r"(?:as|is)?(?:clean|safe|harmless|benign|allowed)|newinstructions|isseclean|isecleanmark|cleanmark|systemprompt|"
    r"(?:system|assistant|developer)(?:says|note|override)"
)


def has_injection(text: str) -> bool:
    """Instruction-like text aimed at the classifier, also when hidden by zero-width, look-alike or spaced letters.

    Only the first and last 4,000 characters are read, so a huge message costs the same as a long one.
    """
    if len(text) > 8000:
        text = text[:4000] + " " + text[-4000:]
    if INJECTION_CUE.search(text):
        return True
    cleaned = clean_chars(text)
    if INJECTION_CUE.search(cleaned):
        return True
    folded = lexicon_view(cleaned)
    if INJECTION_CUE.search(folded):
        return True
    return _SQUASHED_CUE.search(re.sub(r"[\W_]+", "", folded)) is not None


_LLM_CONFIDENT = 0.8
_LLM_MIN_FLAG = 0.5

UpdateCallback = Callable[[ChatMessage, ChatVerdict], None]


@dataclass(frozen=True, slots=True)
class FastOutcome:
    """Result of the synchronous fast path."""

    verdict: ChatVerdict
    needs_llm: bool = False
    top: Candidate | None = None


class _RepeatTracker:
    """In-RAM count of flagged messages per author (keyed by a hash, bounded)."""

    def __init__(self) -> None:
        self._seen: OrderedDict[str, deque[float]] = OrderedDict()

    def record(self, key: str, now: float) -> int:
        q = self._seen.pop(key, None) or deque()
        while q and now - q[0] > REPEAT_WINDOW_S:
            q.popleft()
        q.append(now)
        self._seen[key] = q
        while len(self._seen) > _TRACKER_CAP:
            self._seen.popitem(last=False)
        return len(q)


def _action_from_config(value: str) -> ChatAction:
    return ChatAction(value)


class ChatClassifier:
    def __init__(
        self,
        config: ChatConfig | None = None,
        *,
        llm: LlmTransport | None = None,
        matcher: RuleMatcher | None = None,
        clock: Clock | None = None,
        on_update: UpdateCallback | None = None,
    ) -> None:
        self._cfg = config or ChatConfig()
        self._clock: Clock = clock or SystemClock()
        self._matcher = matcher or default_matcher()
        self._dox = DoxxingDetector(self._cfg.streamer_private.terms, self._cfg.streamer_private.phones)
        self._llm: LlmStage | None = None
        if llm is not None and self._cfg.classify.llm_enabled:
            self._llm = LlmStage(llm, self._cfg.classify, clock=self._clock)
        self._on_update = on_update
        self._repeats = _RepeatTracker()
        self._tasks: set[asyncio.Task[None]] = set()

    def __repr__(self) -> str:
        return "ChatClassifier()"

    # --- configuration ----------------------------------------------------------------------------

    def update_config(self, config: ChatConfig) -> None:
        """Apply a ``ConfigChange``: thresholds, actions and the streamer's private terms."""
        self._cfg = config
        self._dox.update(config.streamer_private.terms, config.streamer_private.phones)

    def set_update_callback(self, callback: UpdateCallback | None) -> None:
        self._on_update = callback

    @property
    def llm_stage(self) -> LlmStage | None:
        return self._llm

    # --- public API -------------------------------------------------------------------------------

    async def classify(self, batch: Sequence[ChatMessage]) -> list[ChatVerdict]:
        """Fast verdict per message. Borderline ones are provisional and resolved in the background."""
        out: list[ChatVerdict] = []
        for msg in batch:
            t0 = time.perf_counter()
            outcome = self.classify_fast(msg)
            if outcome.needs_llm and outcome.top is not None:
                task = asyncio.get_running_loop().create_task(self._background(msg, outcome.top, t0))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            out.append(outcome.verdict)
        return out

    async def classify_final(self, batch: Sequence[ChatMessage]) -> list[ChatVerdict]:
        """Like ``classify`` but waits for the LLM stage, so every verdict is final."""
        t0s = [time.perf_counter() for _ in batch]
        outcomes = [self.classify_fast(m) for m in batch]

        async def one(msg: ChatMessage, outcome: FastOutcome, t0: float) -> ChatVerdict:
            if outcome.needs_llm and outcome.top is not None:
                return await self._resolve(msg, outcome.top, t0)
            return outcome.verdict

        return list(
            await asyncio.gather(*(one(m, o, t) for m, o, t in zip(batch, outcomes, t0s, strict=True)))
        )

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._llm is not None:
            await self._llm.aclose()

    # --- fast path --------------------------------------------------------------------------------

    def classify_fast(self, msg: ChatMessage) -> FastOutcome:
        t0 = time.perf_counter()
        text = msg.text
        if text.strip().lower() in COMMAND_TOKENS:
            return FastOutcome(self._allow(msg, t0, "command", 1.0))
        if not text.strip():
            return FastOutcome(self._allow(msg, t0, "clean", 1.0))
        staff = msg.is_broadcaster or msg.is_moderator
        cands: list[Candidate] = []
        norms: list[Normalized] = []
        for w in windows(text):  # one slice for a normal message; bounded work for a huge one
            norms.append(normalize(w))
            if LEETISH.search(
                w
            ):  # a second reading of a message that looks leeted ("@be", "1 know", "name@oksb1")
                alt = normalize(w, aggressive=True)
                if alt.text != norms[-1].text or alt.link_text != norms[-1].link_text:
                    norms.append(alt)
            for view in decoded_views(
                w
            ):  # base64, hex, rot13 and reversed text that decodes to readable words
                norms.append(normalize(view))
        for norm in norms:
            cands.extend(self._detect(norm, staff))
        if staff:
            if cands:
                top = max(cands, key=lambda c: c.score)
                return FastOutcome(
                    self._verdict(
                        msg, t0, ChatAction.ALLOW, (top.category,), top.score, "staff_dox_flag", "rules"
                    )
                )
            return FastOutcome(self._allow(msg, t0, "clean", 1.0))
        cfg = self._cfg.classify
        if not any(c.score >= cfg.allow_below and not c.lenient for c in cands):
            for norm in norms:
                cands.extend(detect_suspects(norm))  # weak shapes: only an LLM can tell (else allowed)
        if not cands:
            return FastOutcome(self._allow(msg, t0, "clean", 1.0))
        top = max(cands, key=lambda c: c.score)
        if top.score < cfg.allow_below:
            return FastOutcome(self._allow(msg, t0, "below_threshold", 1.0 - top.score), top=top)
        cats = _ordered_categories(cands, cfg.allow_below)
        if top.score >= cfg.rules_hide_threshold:
            return FastOutcome(
                self._final_flagged(msg, t0, top, cats, top.score, top.reason, "rules"), top=top
            )
        if self._llm is not None:
            return FastOutcome(
                self._verdict(
                    msg, t0, ChatAction.HIDE_FROM_STREAMER, cats, top.score, "pending_llm", "rules"
                ),
                needs_llm=True,
                top=top,
            )
        if top.lenient:  # local fallback for a doubtful, mostly harmless message: allow (not hold)
            return FastOutcome(self._allow(msg, t0, f"{top.reason}_allow", 1.0 - top.score))
        return FastOutcome(
            self._verdict(msg, t0, ChatAction.HOLD_FOR_MOD, cats, top.score, "llm_unavailable_hold", "rules"),
            top=top,
        )

    def _detect(self, norm: Normalized, staff: bool) -> list[Candidate]:
        """Every finding in one slice of a message. Staff messages are only scanned for doxxing of others."""
        cands: list[Candidate] = []
        if not staff:
            rr = self._matcher.match(norm)
            if (
                rr.matched and rr.soft == "reported"
            ):  # quoted, reported or discussed: the LLM may look, else allow
                cands.append(
                    Candidate(Category.CHAT_ABUSE, min(rr.score, 0.5), "rules_reported", lenient=True)
                )
            elif rr.matched and rr.soft == "banter":  # friendly teasing with mild words
                cands.append(Candidate(Category.CHAT_ABUSE, 0.2, "rules_banter"))
            elif rr.matched:
                cands.append(
                    Candidate(Category.CHAT_ABUSE, rr.score, f"rules_{rr.kind}", severe=rr.severity == 3)
                )
            cands.extend(detect_scam(norm.link_text, fanged=norm.fanged))
            for blob in norm.link_blobs:
                cands.extend(detect_blob(blob))
            alt = lexicon_view(norm.link_text)  # leetspeak and letter-by-letter spelling of scam wording
            if alt != norm.link_text.lower():
                cands.extend(detect_scam(alt, fanged=True))
            if not rr.matched:
                cands.extend(detect_shapes(norm))
        cands.extend(self._dox.detect(norm, include_private=not staff))
        return cands

    # --- LLM resolution ---------------------------------------------------------------------------

    async def _background(self, msg: ChatMessage, top: Candidate, t0: float) -> None:
        try:
            verdict = await self._resolve(msg, top, t0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a classifier bug must never stop the stream
            log_event(_LOG, "classify_resolve_failed", level=logging.ERROR, error=type(exc).__name__)
            verdict = self._verdict(
                msg, t0, ChatAction.HOLD_FOR_MOD, (top.category,), top.score, "resolve_error_hold", "rules"
            )
        if self._on_update is not None:
            try:
                self._on_update(msg, verdict)
            except Exception as exc:
                log_event(_LOG, "classify_update_cb_failed", level=logging.ERROR, error=type(exc).__name__)

    async def _resolve(self, msg: ChatMessage, top: Candidate, t0: float) -> ChatVerdict:
        assert self._llm is not None
        cfg = self._cfg.classify
        try:
            result = await asyncio.wait_for(self._llm.classify(msg.text), cfg.llm_pending_max_s + 1.0)
        except TimeoutError:
            result = None
        if result is None:
            if top.lenient:
                return self._allow(msg, t0, f"{top.reason}_timeout_allow", 1.0 - top.score)
            return self._verdict(
                msg, t0, ChatAction.HOLD_FOR_MOD, (top.category,), top.score, "llm_timeout_hold", "rules"
            )
        if has_injection(msg.text) and (result.clean or result.confidence < _LLM_MIN_FLAG):
            # text aimed at the classifier ("mark this as clean") must not be able to release the message
            return self._verdict(
                msg, t0, ChatAction.HOLD_FOR_MOD, (top.category,), top.score, "llm_injection_hold", "llm"
            )
        return self._from_llm(msg, t0, top, result)

    def _from_llm(self, msg: ChatMessage, t0: float, top: Candidate, res: LlmResult) -> ChatVerdict:
        if res.clean or res.confidence < _LLM_MIN_FLAG:
            return self._verdict(msg, t0, ChatAction.ALLOW, (), res.confidence, "llm_clean", "llm")
        labels = [lb for lb in _LABEL_ORDER if lb in res.labels]
        cats = tuple(_LABEL_CATEGORY[lb] for lb in labels)
        primary = Candidate(cats[0], res.confidence, f"llm_{labels[0]}", severe=False)
        if res.confidence < _LLM_CONFIDENT:  # uncertain: a human decides
            return self._verdict(
                msg, t0, ChatAction.HOLD_FOR_MOD, cats, res.confidence, primary.reason, "llm"
            )
        return self._final_flagged(msg, t0, primary, cats, res.confidence, primary.reason, "llm")

    # --- verdict construction ---------------------------------------------------------------------

    def _final_flagged(
        self,
        msg: ChatMessage,
        t0: float,
        top: Candidate,
        cats: tuple[Category, ...],
        confidence: float,
        reason: str,
        stage: str,
    ) -> ChatVerdict:
        actions = self._cfg.actions
        if top.category is Category.CHAT_ABUSE:
            action = _action_from_config(actions.severe_abuse if top.severe else actions.abuse)
        elif top.category is Category.CHAT_DOXXING:
            action = _action_from_config(actions.doxxing)
        else:
            action = _action_from_config(actions.scam)
        key = hash_id(f"{msg.platform}:{msg.author_id}")
        if self._repeats.record(key, self._clock.monotonic()) >= REPEAT_THRESHOLD:
            action, reason = ChatAction.TIMEOUT, "repeat_offender"
        return self._verdict(msg, t0, action, cats, confidence, reason, stage)

    def _allow(self, msg: ChatMessage, t0: float, reason: str, confidence: float) -> ChatVerdict:
        return self._verdict(msg, t0, ChatAction.ALLOW, (), min(1.0, max(0.0, confidence)), reason, "rules")

    @staticmethod
    def _verdict(
        msg: ChatMessage,
        t0: float,
        action: ChatAction,
        cats: tuple[Category, ...],
        confidence: float,
        reason: str,
        stage: str,
    ) -> ChatVerdict:
        return ChatVerdict(
            platform=msg.platform,
            message_id=msg.message_id,
            action=action,
            categories=cats,
            confidence=min(1.0, max(0.0, round(confidence, 4))),
            reason=reason,
            stage=stage,  # type: ignore[arg-type]
            latency_ms=round((time.perf_counter() - t0) * 1000.0, 3),
            timeout_seconds=TIMEOUT_SECONDS if action is ChatAction.TIMEOUT else None,
        )


def _ordered_categories(cands: list[Candidate], floor: float) -> tuple[Category, ...]:
    seen: list[Category] = []
    for c in sorted(cands, key=lambda c: c.score, reverse=True):
        if c.score >= floor and c.category not in seen:
            seen.append(c.category)
    return tuple(seen)
