"""Scam / fake UPI (cases 3, 4) and doxxing (case 5). SPECIMEN data only: no real UPI IDs, numbers or IDs."""

from __future__ import annotations

import pytest

from chat.classify import DoxxingDetector, detect_scam, find_numbers, normalize, verhoeff_valid
from chat.testing.synthetic_chat import verhoeff_check_digit
from contracts import Category

SCAM_POSITIVE = [
    "upi://pay?pa=specimen@ybl&pn=Test&am=100",
    "UPI://PAY?PA=specimen@okaxis",
    "pay?pa=specimen@paytm",
    "send money to specimen@ybl",
    "pay 500 to specimen@okaxis now",
    "donate here specimen@paytm",
    "bhejo paisa specimen@oksbi",
    "scan and pay specimen@ibl",
    "payment karo specimen@axl 100 rs",
    "gpay se bhejo specimen@okicici",
    "specimen@apl pe 50 rs bhejo",
    "phonepe://pay?pa=specimen@ybl",
    "mere upi pe bhej do specimen@upi",
    "enter your upi pin to receive money",
    "UPI PIN daalo aur paise pao",
    "FREE FIRE DIAMONDS click here bit.ly/specimen",
    "free fire diamonds claim now tinyurl.com/specimen",
    "congratulations you won iphone claim now bit.ly/specimen",
    "claim your free UC t.ly/specimen",
    "free robux cutt.ly/specimen click now",
    "giveaway winner dm me on telegram",
    "lucky draw winner contact whatsapp",
    "giveaway: join telegram t.me/specimen",
    "earn 5000 daily from home dm me on whatsapp",
    "crypto trading profit dm inbox",
    "invest 1000 get 10000 dm telegram",
    "send 100 rs to get 1000 rs paytm",
    "pay 99 rs and get giveaway entry rb.gy/specimen",
    "paytm.me/specimen pay here",
    "phonepe.com/specimen/pay now",
    "razorpay.me/specimen",
    "rzp.io/specimen pay",
    "is.gd/specimen recharge free",
    "bit.ly/specimen pay now to claim reward",
    "prize jeeta hai tumne claim karo https://specimen-prize.xyz/win",
    "free diamonds https://specimen-rewards.xyz/win",
    "you have won 50000 rs claim here www.specimen-prize.online",
    "ｕｐｉ://pay?pa=specimen@ybl",
    "upi\u200b://pay?pa=specimen@ybl",
    "send to SPECIMEN@YBL 100 rs",
]
SCAM_NEGATIVE = [
    "check this https://youtube.com/watch?v=abc123",
    "clip: https://clips.twitch.tv/abcd",
    "https://www.wikipedia.org/wiki/India",
    "mail me at specimen@gmail.com",
    "contact specimen@example.com for business",
    "specimen@sbi.co.in",
    "specimen@icici.com",
    "specimen@axisbank.com ke baare me",
    "@streamer hello bhai",
    "@streamer love you",
    "@some_user nice play",
    "hello @everyone",
    "payment hua kya merch ka",
    "upi se donate karna hai kaise",
    "super chat kaise karte hai",
    "gpay se super chat nahi hota kya",
    "free fire max download link",
    "free fire ya bgmi kaun sa better",
    "giveaway kab hoga bhai",
    "giveaway ka wait hai",
    "winner kaun hai bhai",
    "prize kya hai",
    "link bhejo na bhai game ka",
    "click on subscribe bell",
    "dm me for collab bhai",
    "pay attention to the minimap",
    "send me your feedback please",
    "bgmi UC top up kaha se kare",
    "the claim was false lol",
    "beware of fake giveaways guys",
    "dont click unknown links",
    "report this scammer please",
    "bhai profile dekho",
    "mera email specimen@outlook.com hai",
    "specimen@ybl.in is a website",
    "bhai 500 rs ka merch hai",
    "rs 100 off sale",
    "nice stream https://twitch.tv/specimen",
    "support at https://streamlabs.com/specimen",
    "chat rules: be nice",
]


def top(text: str):
    return max(detect_scam(normalize(text).link_text), key=lambda c: c.score, default=None)


def test_scam_fixture_recall_and_no_false_positives() -> None:
    assert len(SCAM_POSITIVE) == len(SCAM_NEGATIVE) == 40
    missed = [t for t in SCAM_POSITIVE if (top(t) is None or top(t).score < 0.9)]
    assert len(missed) / 40 <= 0.05, missed
    false_pos = [t for t in SCAM_NEGATIVE if (top(t) is not None and top(t).score >= 0.3)]
    assert not false_pos, false_pos


def test_emails_and_mentions_never_flag() -> None:
    for t in [
        "specimen@gmail.com",
        "specimen@sbi.co.in",
        "mail specimen@icici.com now pay",
        "@streamer pay attention",
    ]:
        cands = detect_scam(normalize(t).link_text)
        assert not any(c.category is Category.CHAT_FAKE_UPI for c in cands), t


def test_upi_deep_link_is_fake_upi_098() -> None:
    c = top("upi://pay?pa=specimen@ybl&am=500")
    assert c is not None and c.category is Category.CHAT_FAKE_UPI and c.score >= 0.98


def test_upi_id_without_money_word_is_borderline_only() -> None:
    c = top("specimen@ybl")
    assert c is not None and c.category is Category.CHAT_FAKE_UPI and 0.3 <= c.score < 0.9


def test_bare_shortener_is_borderline_giveaway_with_link_is_hard() -> None:
    assert 0.3 <= top("bit.ly/specimen").score < 0.9
    assert top("free fire diamonds bit.ly/specimen").score >= 0.9


# --- doxxing --------------------------------------------------------------------------------------

D = DoxxingDetector(["Rohan Specimen Verma", "Specimen City"], ["+91 98765 43210"])


def dox(text: str, **kw):
    return D.detect(normalize(text), **kw)


@pytest.mark.parametrize(
    "text",
    [
        "call 9876501234",
        "98765 01234",
        "+91 98765 01234",
        "+91-98765-01234",
        "098765 01234",
        "91 98765 01234",
        "987 650 1234",
        "(98765) 01234",
        "98 76 50 12 34",
        "९८७६५०१२३४ पर कॉल करो",
        "9 8 7 6 5 0 1 2 3 4",
        "nine eight seven six five zero one two three four",
        "double nine eight seven six five zero one two three",
        "नौ आठ सात छह पांच शून्य एक दो तीन चार",
    ],
)
def test_phone_in_many_formats(text: str) -> None:
    got = [c for c in dox(text) if c.reason == "dox_phone"]
    assert got and got[0].score >= 0.97 and got[0].category is Category.CHAT_DOXXING, text


@pytest.mark.parametrize(
    "text",
    [
        "room id 1234567890",
        "order 5551234567",
        "score 6 7 8 9 10 11 12",
        "price 400001",
        "400001 views",
        "100 200 300 400",
        "2024 11 21 stream",
        "call 12345",
        "id 123456789012345678",
    ],
)
def test_non_mobile_numbers_are_not_phones(text: str) -> None:
    assert not [c for c in dox(text) if c.reason in ("dox_phone", "dox_private_phone")], text


def test_pin_needs_an_address_cue() -> None:
    assert [c for c in dox("uska ghar 400001 me hai")][0].reason == "dox_pin_address"
    assert dox("house 22 near temple 560 001")[0].reason == "dox_pin_address"
    assert not dox("400001 views ho gaye")
    assert not dox("pin 400001 ")[1:]  # "pin" is itself an address cue: one hit, no more


def test_verhoeff_and_aadhaar_like() -> None:
    body = "22222222222"
    number = body + verhoeff_check_digit(body)
    assert verhoeff_valid(number) and not verhoeff_valid(number[:-1] + str((int(number[-1]) + 1) % 10))
    assert ("aadhaar", number) in find_numbers(f"id {number[:4]} {number[4:8]} {number[8:]}")
    wrong = body + str((int(verhoeff_check_digit(body)) + 1) % 10)
    assert not [k for k, _ in find_numbers(wrong) if k == "aadhaar"]
    assert [c.reason for c in dox(number)] == ["dox_aadhaar_like"]


def test_pan_and_intent_phrases() -> None:
    assert dox("pan ABCPE1234F hai")[0].reason == "dox_pan"
    assert not dox("ABCDE1234Z")  # 4th letter is not a valid holder type
    for t in ["dox him", "doxxed lol", "leak his address", "ghar ka address do", "घर का पता लीक करो"]:
        assert [c.reason for c in dox(t)] == ["dox_intent"], t
    assert not dox("hello bhai address bar me type karo ghar se")


def test_streamer_private_terms_and_phone() -> None:
    assert dox("rohan specimen verma ka ghar")[0].reason == "dox_private_term"
    assert dox("ROHAN  SPECIMEN  VERMA")[0].score == 0.97
    assert dox("he lives in specimen city")[0].reason == "dox_private_term"
    assert dox("call 9876543210")[0].reason == "dox_private_phone"
    assert not dox("specimenology is fun")  # whole word match
    # the streamer's own messages never trip their own private data
    assert not dox("rohan specimen verma here, call 9876543210", include_private=False)[1:]
    assert dox("rohan specimen verma here", include_private=False) == []


def test_private_terms_can_be_updated_and_are_not_printed() -> None:
    d = DoxxingDetector()
    assert not d.detect(normalize("zzqq private name"))
    d.update(["zzqq private name"], [])
    assert d.detect(normalize("zzqq private name"))
    assert "zzqq" not in repr(d)
