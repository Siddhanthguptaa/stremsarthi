"""PII spans to padded, merged mute spans (docs/specs/audio.md 5.8) and the cross-segment number carry."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from audio.orchestrator.window import norm
from contracts.enums import Category
from contracts.speech import PiiSpan, TranscriptWindow, Word

# Compact set used only to decide "the span ends on a number word" and to carry leading digits across a
# segment boundary. The full number tables live in audio/pii (T3.3).
NUMBER_WORDS = frozenset(
    """zero oh one two three four five six seven eight nine ten double triple
    shunya ek do teen char chaar paanch panch chhe chhah che saat sat aath ath nau das sau hazaar hazar lakh dabal tripal
    sonne ondu eradu mooru naalku aidu aaru elu entu ombattu poojyam onru irandu moondru naangu ainthu ezhu ettu onbathu
    sunna okati rendu moodu naalugu enimidi tommidi shunyo dui tin choy shat aat noy
    शून्य ज़ीरो एक दो तीन चार पांच पाँच छह छः छे सात आठ नौ दस सौ हज़ार हजार लाख डबल ट्रिपल
    ಸೊನ್ನೆ ಒಂದು ಎರಡು ಮೂರು ನಾಲ್ಕು ಐದು ಆರು ಏಳು ಎಂಟು ಒಂಬತ್ತು
    பூஜ்யம் ஒன்று இரண்டு மூன்று நான்கு ஐந்து ஆறு ஏழு எட்டு ஒன்பது
    సున్నా ఒకటి రెండు మూడు నాలుగు ఐదు ఆరు ఏడు ఎనిమిది తొమ్మిది
    শূন্য এক দুই তিন চার পাঁচ ছয় সাত আট নয়""".split()
)


def is_number_token(text: str) -> bool:
    n = norm(text)
    if not n:
        return False
    return n in NUMBER_WORDS or all(ch.isdigit() for ch in n)


@dataclass(frozen=True, slots=True)
class PlannedSpan:
    pts_start: float
    pts_end: float
    category: Category
    confidence: float
    reason: str
    granularity: str  # "word" | "segment" | "failsafe"
    tail_extended: bool = False
    anchor_t1: float = 0.0  # unpadded end of the last PII word (for the carry)


@dataclass(frozen=True, slots=True)
class Carry:
    """Watch state: the last span ended on a number word at a segment end, so digits may continue."""

    anchor_t1: float
    category: Category
    confidence: float


def _code(text: str) -> str:
    return "".join(c if (c.isalnum() and c.isascii()) or c in "_:.-/" else "_" for c in text)[:64] or "pii"


class SpanPlanner:
    def __init__(
        self,
        pad_s: float = 0.2,
        merge_gap_s: float = 0.3,
        min_span_s: float = 0.3,
        tail_ext_s: float = 0.5,
        tail_touch_s: float = 0.2,
        number_gap_s: float = 1.0,
    ) -> None:
        self.pad_s = pad_s
        self.merge_gap_s = merge_gap_s
        self.min_span_s = min_span_s
        self.tail_ext_s = tail_ext_s
        self.tail_touch_s = tail_touch_s
        self.number_gap_s = number_gap_s

    # ------------------------------------------------------------------ carry
    def leading_numbers(self, carry: Carry | None, window: TranscriptWindow) -> list[PiiSpan]:
        """Leading number tokens of the newest segment that continue a muted run from the previous segment."""
        if carry is None:
            return []
        new: Sequence[Word] = window.words[window.new_from_index :]
        run: list[Word] = []
        prev_t1 = carry.anchor_t1
        for w in new:
            if w.t0 - prev_t1 >= self.number_gap_s or not is_number_token(w.text):
                break
            run.append(w)
            prev_t1 = w.t1
        if not run:
            return []
        approx = any(w.approx for w in run)
        return [
            PiiSpan(
                carry.category,
                run[0].t0,
                run[-1].t1,
                carry.confidence,
                "carry_number",
                "segment" if approx else "word",
            )
        ]

    # ------------------------------------------------------------------ plan
    def plan(
        self,
        spans: Sequence[PiiSpan],
        window: TranscriptWindow,
        seg_bounds: Mapping[int, tuple[float, float]],
        newest_end: float,
    ) -> list[PlannedSpan]:
        raw: list[PlannedSpan] = []
        for s in spans:
            lo, hi = s.pts_start, s.pts_end
            inside = [w for w in window.words if w.t1 >= lo - 1e-3 and w.t0 <= hi + 1e-3]
            gran = "segment" if s.granularity == "segment" else "word"
            for w in inside:
                if w.approx:
                    gran = "segment"
                    b = seg_bounds.get(w.seg_id)
                    if b is not None:
                        lo, hi = min(lo, b[0]), max(hi, b[1])
            tail = False
            if inside:
                last = max(inside, key=lambda w: w.t1)
                tail = is_number_token(last.text) and s.pts_end >= newest_end - self.tail_touch_s
            anchor = max([s.pts_end] + [w.t1 for w in inside])
            end = hi + self.pad_s + (self.tail_ext_s if tail else 0.0)
            reason = _code(("ambiguous:" if s.ambiguous else "") + s.reason)
            raw.append(
                PlannedSpan(
                    max(0.0, lo - self.pad_s), end, s.category, s.confidence, reason, gran, tail, anchor
                )
            )
        raw.sort(key=lambda p: p.pts_start)
        merged: list[PlannedSpan] = []
        for p in raw:
            if merged and p.pts_start - merged[-1].pts_end < self.merge_gap_s:
                m = merged[-1]
                top = p if p.confidence > m.confidence else m
                merged[-1] = PlannedSpan(
                    m.pts_start,
                    max(m.pts_end, p.pts_end),
                    top.category,
                    top.confidence,
                    top.reason,
                    "segment" if "segment" in (m.granularity, p.granularity) else "word",
                    m.tail_extended or p.tail_extended,
                    max(m.anchor_t1, p.anchor_t1),
                )
            else:
                merged.append(p)
        out: list[PlannedSpan] = []
        for p in merged:
            if p.pts_end - p.pts_start < self.min_span_s:
                mid = (p.pts_start + p.pts_end) / 2
                lo = max(0.0, mid - self.min_span_s / 2)
                p = PlannedSpan(
                    lo, lo + self.min_span_s, p.category, p.confidence, p.reason, p.granularity,
                    p.tail_extended, p.anchor_t1,
                )  # fmt: skip
            out.append(p)
        return out
