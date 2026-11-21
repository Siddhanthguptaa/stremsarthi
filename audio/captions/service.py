"""Caption service (task T5.3): ``Transcript`` events in, redacted and translated ``CaptionEvent``s out.

Subscribes to ``Topic.TRANSCRIPT`` (local only), ``Topic.MUTE_SPAN`` and ``Topic.CONFIG`` on the bus and publishes
``Topic.CAPTION``. Never imports the orchestrator: it only sees events. Per transcript:

1. drop the words that repeat the previous segment's overlap, keep the rest as a *unit*;
2. wait ``settle_s`` for the mute verdict of that segment (spans are published right after the transcript);
3. mask every word a mute span covers (and, as a second net, every word the spoken-PII rules flag) with ``▮▮▮``;
4. only the visible text goes to the translator (Sarvam, IndicTrans2 in the demo profile, or nothing in dev);
5. publish captions with ``pts_start`` = first word start and ``pts_end`` = last word end + 0.3 s.

A mute span that arrives after its caption was published triggers a correction: the same ``pts_start``/``pts_end``
are published again with the new redaction (the engine must let the later event for a key replace the earlier
one; the display time is ``D`` after the capture time, so a correction arrives well before it is drawn).

If the translation is not ready by ``pts_start + D - 0.3`` (mapped to this clock), ``captions.on_late`` decides:
``skip`` or ``show_source``. Every network call is async with a timeout and never blocks the frame path.
Transcript text lives only in memory; logs carry counts, ids and timings.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from audio.captions.langs import LANG_SCRIPT, SARVAM_CODES, Plan, normalise_code, plan_conversion
from audio.captions.redact import (
    MuteLedger,
    Piece,
    drop_overlap,
    has_visible,
    mask_pieces,
    pieces_text,
    rule_intervals,
    word_flags,
)
from audio.captions.settings import CaptionSettings
from audio.captions.timing import ClockMap, schedule, wrap_pages
from audio.captions.translate import Converted, ResilientTranslator, build_translator
from contracts.enums import Topic
from contracts.events import CaptionEvent, StageTiming
from contracts.speech import Transcript, Word

log = logging.getLogger("audio.captions")

_MASK_RUN = re.compile("▮+")


@dataclass(eq=False)
class Unit:
    """One transcript's new words. Transient: held in memory for corrections, never logged."""

    seq: int
    words: list[Word]
    lang: str
    t_recv: float
    pts_start: float
    pts_end: float
    emitted: bool = False
    masked: frozenset[int] = frozenset()
    gen: int = 0
    pages: list[str] = field(default_factory=list)
    # slots currently live in the engine: pts_start -> pts_end. The engine replaces a caption only when pts_start
    # matches, so a correction must also replace every old start that its new page layout no longer uses.
    live: dict[float, float] = field(default_factory=dict)


def _load_rules(config: Any) -> Any | None:
    try:
        from audio.pii.rules import SpokenRules  # noqa: PLC0415

        a = config.audio
        return SpokenRules(watch_window_s=a.watch_window_s, address_mute=a.address_mute)
    except Exception as exc:  # the mute spans alone still redact
        log.info("caption rule check unavailable (%s)", type(exc).__name__)
        return None


class CaptionService:
    def __init__(
        self,
        config: Any,
        bus: Any,
        translator: ResilientTranslator | None = None,
        settings: CaptionSettings | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        rules: Any | None = None,
    ) -> None:
        self.bus = bus
        self.clock = clock
        self.settings = settings or CaptionSettings.from_config(config)
        self.translator = translator or build_translator(config, self.settings, clock=clock)
        self.rules = (
            rules if rules is not None else (_load_rules(config) if self.settings.local_rules else None)
        )
        self.ledger = MuteLedger(keep_s=self.settings.keep_s + 18.0)
        self.cmap = ClockMap()
        self._window: deque[Word] = deque()
        self._prev_end: float | None = None
        self._units: deque[Unit] = deque(maxlen=96)
        self._rule_seen: set[tuple[int, int]] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._seq = 0
        self.stats: dict[str, int] = dict.fromkeys(
            (
                "transcripts",
                "units",
                "emitted",
                "masked_words",
                "skipped_late",
                "shown_source",
                "degraded",
                "corrections",
                "mask_only",
                "piecewise",
                "vacated",
            ),
            0,
        )
        self.latencies: deque[float] = deque(maxlen=4096)  # transcript received -> caption published
        self.margins: deque[float] = deque(maxlen=4096)  # deadline minus publish time (negative: late)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop: asyncio.Event | None = None
        self._ready = threading.Event()

    # ------------------------------------------------------------------ bus
    async def run(self) -> None:
        sub = self.bus.subscribe([Topic.TRANSCRIPT, Topic.MUTE_SPAN, Topic.CONFIG])
        try:
            async for topic, ev in sub:
                try:
                    self.handle(topic, ev)
                except Exception as exc:  # a caption bug must never reach the audio path
                    log.warning("caption event failed (%s)", type(exc).__name__)
        finally:
            closer = getattr(sub, "close", None)
            if closer is not None:
                closer()

    def handle(self, topic: Topic, ev: Any) -> None:
        if topic == Topic.TRANSCRIPT:
            self._on_transcript(ev)
        elif topic == Topic.MUTE_SPAN:
            self.ledger.add(ev.pts_start, ev.pts_end)
            self._recheck()
        elif topic == Topic.CONFIG:
            self._on_config(ev)

    def _on_config(self, ev: Any) -> None:
        upd: dict[str, Any] = {}
        for key, val in dict(ev.diff).items():
            if key == "captions.enabled" and isinstance(val, bool):
                upd["enabled"] = val
            elif key == "captions.target_lang" and isinstance(val, str):
                code = normalise_code(val)
                if code in LANG_SCRIPT or code in SARVAM_CODES:
                    upd["target_lang"] = code
            elif key == "captions.on_late" and val in ("skip", "show_source"):
                upd["on_late"] = val
        if upd:
            self.settings = replace(self.settings, **upd)
            if upd.get("enabled") is False:
                self._window.clear()
                self._units.clear()

    # ------------------------------------------------------------------ transcripts
    def _on_transcript(self, tr: Transcript) -> None:
        s = self.settings
        now = self.clock()
        self.stats["transcripts"] += 1
        self.cmap.observe(tr.pts_end, now, tr.latency_s)
        if not s.enabled:
            return
        new = drop_overlap(list(self._window)[-4:], tr.words, self._prev_end)
        self._prev_end = tr.pts_end
        floor = tr.pts_end - s.keep_s
        while self._window and self._window[0].t1 < floor:
            self._window.popleft()
        while self._units and self._units[0].pts_end < floor:
            self._units.popleft()
        self.ledger.prune(floor)
        if not new:
            return
        self._window.extend(new)
        for lo, hi in rule_intervals(self.rules, list(self._window)):
            key = (round(lo * 20), round(hi * 20))
            if key not in self._rule_seen:
                self._rule_seen.add(key)
                self.ledger.add(lo, hi)
        if len(self._rule_seen) > 4096:
            self._rule_seen.clear()
        self._seq += 1
        unit = Unit(self._seq, list(new), tr.lang, now, new[0].t0, new[-1].t1)
        self._units.append(unit)
        self.stats["units"] += 1
        self._spawn(self._process(unit, correction=False))
        self._recheck()

    def _spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _recheck(self) -> None:
        """A mute that arrived after a caption was published: publish that caption again, redacted."""
        iv = self.ledger.intervals()
        for u in list(self._units):
            if not u.emitted:
                continue
            flags = word_flags(u.words, iv, self.settings.min_overlap_s)
            if any(f and i not in u.masked for i, f in enumerate(flags)):
                self.stats["corrections"] += 1
                self._spawn(self._process(u, correction=True))

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ------------------------------------------------------------------ one unit
    async def _process(self, unit: Unit, *, correction: bool) -> None:
        try:
            await self._process_inner(unit, correction)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # no caption beats a caption that may leak: nothing new is published
            log.warning("caption unit %d failed (%s)", unit.seq, type(exc).__name__)
            if correction:  # the old text is known to be stale (a mute covers it): take it off the screen
                self._vacate(unit, set(), unit.lang)

    async def _process_inner(self, unit: Unit, correction: bool) -> None:
        s = self.settings
        if not correction:
            await asyncio.sleep(s.settle_s)
        if not self.settings.enabled:
            return
        s = self.settings
        unit.gen += 1
        gen = unit.gen
        flags = word_flags(unit.words, self.ledger.intervals(), s.min_overlap_s)
        unit.masked = frozenset(i for i, f in enumerate(flags) if f)
        self.stats["masked_words"] += len(unit.masked)
        pieces = mask_pieces(unit.words, flags)
        if not pieces:
            self._vacate(unit, set(), unit.lang)
            return
        src_lang = normalise_code(unit.lang)
        t_conv = self.clock()
        late = False
        if not has_visible(pieces):
            self.stats["mask_only"] += 1
            conv = Converted(s.mask_token, src_lang, "mask", False)
        else:
            plain = " ".join(p.text for p in pieces if p.text is not None)
            plan = plan_conversion(unit.lang, plain, s.target_lang)
            due = self.cmap.due(unit.pts_start, s.delay_s, s.soft_margin_s)
            if plan.action == "none":
                conv = Converted(pieces_text(pieces, s.mask_token), plan.out_lang, "none", False)
            else:
                budget = s.translate_timeout_s
                if due is not None and not correction:
                    budget = min(budget, due - self.clock())
                if budget <= 0:
                    late = True
                    conv = self._source(pieces, plan, src_lang)
                else:
                    conv = await self._convert(pieces, plan, budget, src_lang)
                    if conv.degraded:
                        self.stats["degraded"] += 1
            if not correction and plan.action != "none" and due is not None and self.clock() > due:
                late = True
        if late and not correction:
            if s.on_late == "skip":
                self.stats["skipped_late"] += 1
                return
            self.stats["shown_source"] += 1
            conv = self._source(pieces, plan, src_lang)
        if unit.gen != gen:  # a newer pass (a correction) owns the output now
            return
        self._emit(unit, conv, t_conv)

    def _source(self, pieces: list[Piece], plan: Plan, src_lang: str) -> Converted:
        lang = plan.src if plan.src != "auto" else src_lang
        return Converted(pieces_text(pieces, self.settings.mask_token), lang, "passthrough", True)

    async def _convert(self, pieces: list[Piece], plan: Plan, budget: float, src_lang: str) -> Converted:
        s = self.settings
        text = pieces_text(pieces, s.mask_token)
        n_masks = sum(1 for p in pieces if p.text is None)
        t_end = self.clock() + budget
        conv = await self.translator.convert(text, plan, budget)
        if conv.degraded or n_masks == 0:
            return self._fix_lang(conv, src_lang)
        if len(_MASK_RUN.findall(conv.text)) == n_masks:  # the translator kept every mask
            return Converted(_MASK_RUN.sub(s.mask_token, conv.text), conv.lang, conv.provider, False)
        # a mask was mangled or dropped: translate only the visible runs, join them around the masks
        visible = [p for p in pieces if p.text is not None]
        remaining = t_end - self.clock()
        if len(visible) > 3 or remaining <= 0:
            return self._source(pieces, plan, src_lang)
        self.stats["piecewise"] += 1
        parts = await asyncio.gather(
            *(self.translator.convert(p.text or "", plan, remaining) for p in visible)
        )
        if any(c.degraded for c in parts):
            return self._source(pieces, plan, src_lang)
        it = iter(parts)
        out = [s.mask_token if p.text is None else _MASK_RUN.sub("", next(it).text).strip() for p in pieces]
        return Converted(" ".join(out), conv.lang, conv.provider, False)

    @staticmethod
    def _fix_lang(conv: Converted, src_lang: str) -> Converted:
        if conv.degraded and conv.lang in ("unknown", "auto"):
            return Converted(conv.text, src_lang, conv.provider, True)
        return conv

    # ------------------------------------------------------------------ output
    def _emit(self, unit: Unit, conv: Converted, t_conv: float) -> None:
        s = self.settings
        pages = wrap_pages(conv.text, s.max_chars_per_line, s.max_lines)
        lang = normalise_code(conv.lang)
        now = self.clock()
        slots = schedule(pages, unit.pts_start, unit.pts_end, s)
        # replacements for slot starts the new layout does not reuse go out FIRST, then the new pages
        self._vacate(unit, {sl.pts_start for sl in slots}, lang)
        for slot in slots:
            self.bus.publish(Topic.CAPTION, CaptionEvent(slot.pts_start, slot.pts_end, lang, slot.text, True))
            unit.live[slot.pts_start] = slot.pts_end
        unit.emitted = True
        unit.pages = pages
        self.stats["emitted"] += 1
        self.latencies.append(now - unit.t_recv)
        due = self.cmap.due(unit.pts_start, s.delay_s, s.soft_margin_s)
        if due is not None:
            self.margins.append(due - now)
        self.bus.publish(
            Topic.TIMING, StageTiming("caption_translate", unit.seq, unit.pts_start, t_conv, max(t_conv, now))
        )

    def _vacate(self, unit: Unit, keep: set[float], lang: str) -> None:
        """Replace every live slot of ``unit`` whose start is not in ``keep`` by a zero-length mask-only caption
        (same ``pts_start``, so the engine drops the old text; zero length, so nothing is drawn from it and it
        cannot cover a neighbouring page under the engine's newest-start-wins rule)."""
        code = normalise_code(lang)
        for start in list(unit.live):
            if any(abs(start - k) < 1e-6 for k in keep):
                continue
            self.bus.publish(Topic.CAPTION, CaptionEvent(start, start, code, self.settings.mask_token, True))
            del unit.live[start]
            self.stats["vacated"] += 1

    # ------------------------------------------------------------------ thread host (like the audio host)
    def start(self) -> None:
        if self._thread is not None:
            return
        self._ready.clear()
        t = threading.Thread(target=self._thread_main, name="audio-captions", daemon=True)
        self._thread = t
        t.start()
        if not self._ready.wait(5.0):
            raise RuntimeError("caption service did not start")

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._stop = asyncio.Event()
        main = loop.create_task(self.run())
        self._ready.set()
        try:
            loop.run_until_complete(self._until_stopped(main))
        finally:
            tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in tasks:
                t.cancel()
            if tasks:
                loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            loop.run_until_complete(self.translator.aclose())
            loop.close()

    async def _until_stopped(self, main: asyncio.Task[None]) -> None:
        assert self._stop is not None
        await self._stop.wait()
        main.cancel()
        await asyncio.gather(main, return_exceptions=True)

    def close(self) -> None:
        loop, thread, stop = self._loop, self._thread, self._stop
        if loop is None or thread is None or stop is None:
            return
        loop.call_soon_threadsafe(stop.set)
        thread.join(5.0)
        self._loop = self._thread = self._stop = None


def build(config: Any, bus: Any) -> CaptionService:
    """Entry point: ``audio.captions:build``. The caller runs ``start()`` / ``close()`` (own thread and loop)."""
    return CaptionService(config, bus)
