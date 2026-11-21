"""Spoken-PII rules on HAND-MADE, Whisper-style transcripts (not real ASR output, not real people's data).

Each transcript marks the sensitive words with ``[[ ... ]]``. Recall counts a sensitive word as caught when a span
from the rules-only detector (no LLM: the no-key path) covers it. Clean sentences must produce no span.
Numbers are SPECIMEN-style and made up.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from audio.pii.detector import SpokenPiiDetector
from audio.pii.rules import SpokenRules
from contracts.speech import TranscriptWindow, Word

STEP = 0.40

# (id, transcript with [[pii]] markers)
PII_CASES = [
    ("hi_deva_words_phone", "मेरा नंबर है [[नौ आठ सात छह पाँच चार तीन दो एक शून्य]] बात कर लो"),
    ("hi_deva_digits_phone", "मेरा नंबर है [[9876543210]] इस पर कॉल करना"),
    ("hi_deva_script_digits", "मेरा नंबर है [[९८७६५४३२१०]] है"),
    (
        "latn_words_phone",
        "mera number hai [[nau aath saat chhe paanch char teen do ek shunya]] call kar lena",
    ),
    ("latn_digit_groups", "mera number likh lo [[98765 43210]] hai"),
    ("latn_single_digits", "number hai [[9 8 7 6 5 4 3 2 1 0]] noted"),
    ("latn_double_triple", "mera number hai [[nau aath double saat teen triple do ek paanch]] ok"),
    (
        "aadhaar_words",
        "mera aadhaar number hai [[do teen chaar paanch chhe saat aath nau ek do teen chaar]] bas",
    ),
    ("aadhaar_groups", "aadhaar [[2345 6789 0123]] hai mera"),
    ("upi_at_the_rate", "mera upi id hai [[rahul underscore sharma at the rate of okhdfcbank]] bhej dena"),
    ("upi_deva", "यूपीआई आईडी है [[राहुल एट द रेट ओके एसबीआई]] भेज दो"),
    ("otp_words", "otp aaya hai [[chaar paanch teen ek nau do]] bata raha hoon"),
    ("pin_words", "mera pin code hai [[ek ek zero zero ek chhe]] mumbai"),
    ("address", "mera ghar ka pata hai [[flat number 402 shanti nagar andheri east mumbai 400069]] aa jao"),
]
CLEAN_CASES = [
    ("clean_greeting", "aaj ka stream shuru karte hain doston like aur subscribe karna mat bhoolna"),
    ("clean_game_numbers", "pehle round mein maine teen kill kiye aur paanch minute baad zone band hoga"),
    ("clean_deva", "आज हम बीजीएमआई खेलेंगे और मैं आपको तीन टिप्स बताऊंगा"),
    ("clean_price", "yeh phone do hazaar paanch sau rupaye ka hai bahut sasta"),
    ("clean_time", "kal raat das baje stream hoga aur ek ghante chalega"),
]

# Second batch: other ways Whisper writes the same speech (hyphen or comma splits, +91, pairs, english words, ...).
EXTRA_PII = {
    "hyphen_groups": "mera number [[98765-43210]] hai",
    "plus91": "call me on [[+91 98765 43210]] anytime",
    "pairs": "number hai [[98 76 54 32 10]] noted",
    "deva_pairs": "मेरा नंबर [[९८ ७६ ५४ ३२ १०]] है",
    "english_digits": "my number is [[nine eight seven six five four three two one zero]] call me",
    "ninety_eight": "my number is [[ninety eight seven six five four three two one zero]] ok",
    "double_nine": "mera number hai [[double nine eight seven six five four three two one]] bas",
    "double_digit": "mera number hai [[double 9 8 7 6 5 4 3 2 1]] bas",
    "deva_double": "मेरा नंबर है [[डबल नौ आठ सात छह पाँच चार तीन दो एक]] ठीक है",
    "comma_split": "number hai [[9, 8, 7, 6, 5, 4, 3, 2, 1, 0]] ok",
    "comma_words": "number hai [[nau, aath, saat, chhe, paanch, char, teen, do, ek, shunya]] ok",
    "ta_digits_latn": "en number [[onbathu ettu ezhu aaru ainthu naangu moondru irandu onru poojyam]] podunga",
    "upi_spelt": "mera upi hai [[r a h u l at the rate of o k s b i]] bhej do",
    "upi_ybl": "upi id [[rahul123 at ybl]] hai",
    "upi_at_sign": "mera id hai [[rahul.sharma@okaxis]] bhej do",
    "otp_digits": "otp hai [[4 5 3 1 9 2]] bata do",
    "otp_deva": "ओटीपी है [[चार पाँच तीन एक नौ दो]] बता रहा हूं",
    "aadhaar_deva": "आधार नंबर है [[दो तीन चार पाँच छह सात आठ नौ एक दो तीन चार]] बस",
    "card16": "card number [[4111 1111 1111 1111]] expiry",
    "address_deva": "मेरा पता है [[फ्लैट नंबर 402 शांति नगर अंधेरी ईस्ट मुंबई चार सौ ]] आ जाओ",
    "split_seg_word": "mera number hai [[nau aath saat chhe paanch]] [[char teen do ek shunya]]",
    "haan_filler": "mera number hai [[nau aath saat chhe]] umm [[paanch char teen do ek shunya]]",
}
EXTRA_CLEAN = {
    "c_year": "yeh 2024 ka game hai aur maine 15 kill kiye",
    "c_hz": "mere 5 hazaar subscribers ho gaye hain thank you",
    "c_score": "score hai 120 aur 45 kill",
    "c_phone_talk": "phone ka battery do percent bacha hai",
    "c_at": "main kal at the stream aaunga jo stream pe aaya",
    "c_count": "ek do teen chalo shuru karte hain",
    "c_deva_count": "एक दो तीन चलो शुरू करते हैं",
    "c_gift": "super chat mein sau rupaye bheje thanks bhai",
    "c_levels": "level ek se nau tak ke tips dunga",
}
PII_CASES += list(EXTRA_PII.items())
CLEAN_CASES += list(EXTRA_CLEAN.items())


def build(marked: str) -> tuple[TranscriptWindow, list[bool]]:
    words: list[Word] = []
    flags: list[bool] = []
    inside = False
    for tok in marked.split():
        if tok.startswith("[["):
            inside, tok = True, tok[2:]
        end = tok.endswith("]]")
        if end:
            tok = tok[:-2]
        if tok:
            i = len(words)
            words.append(Word(tok, i * STEP, i * STEP + 0.8 * STEP, False, 1))
            flags.append(inside)
        if end:
            inside = False
    return TranscriptWindow(tuple(words), words[0].t0, words[-1].t1, 0), flags


def detect(win: TranscriptWindow):
    return asyncio.run(SpokenPiiDetector(SpokenRules()).detect(win))


def covered(win, flags, spans) -> tuple[int, int]:
    hit = tot = 0
    for w, f in zip(win.words, flags, strict=True):
        if f:
            tot += 1
            mid = (w.t0 + w.t1) / 2
            hit += any(s.pts_start - 1e-6 <= mid <= s.pts_end + 1e-6 for s in spans)
    return hit, tot


@pytest.mark.parametrize(("cid", "text"), PII_CASES, ids=[c[0] for c in PII_CASES])
def test_sensitive_words_are_covered(cid, text):
    win, flags = build(text)
    hit, tot = covered(win, flags, detect(win))
    assert hit == tot, f"{cid}: {hit}/{tot} sensitive words covered"


@pytest.mark.parametrize(("cid", "text"), CLEAN_CASES, ids=[c[0] for c in CLEAN_CASES])
def test_clean_sentences_are_not_muted(cid, text):
    win, _ = build(text)
    assert detect(win) == [], cid


def test_recall_and_false_positive_summary():
    hit = tot = 0
    for _cid, text in PII_CASES:
        win, flags = build(text)
        h, t = covered(win, flags, detect(win))
        hit, tot = hit + h, tot + t
    fp = sum(1 for _cid, text in CLEAN_CASES if detect(build(text)[0]))
    assert hit / tot >= 0.97 and fp == 0, (hit, tot, fp)
    assert re.fullmatch(r"\d+", str(len(PII_CASES) + len(CLEAN_CASES)))
