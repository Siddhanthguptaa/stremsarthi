"""Generalisation tests: obfuscation, spelling variants, context-aware words, link structure, SPECIMEN forms.

Every phrasing here is written for this file (it is not taken from `eval/`). They exercise the PATTERNS the
classifier handles, so a change that only memorises a list of messages fails here:

* abuse   : phonetic spelling variants, in-word separators, ``*`` for a letter, spaced letters, leet, fullwidth,
            zero-width characters, Devanagari and mixed script, English insults, threats, sexual harassment;
            neutral uses of ambiguous words (a pet dog, a film, a place, an explanation, a broadcaster's name).
* scam    : UPI IDs written with ``(at)`` / ``[at]`` / ``at the rate`` / Devanagari, defanged and spaced links,
            raw IP links, lookalike brand domains, cheap TLDs with lure words, scam wording next to a link;
            harmless links, emails and questions are left alone.
* doxxing : SPECIMEN Aadhaar-like numbers (leading 0 or 1) and PANs (4th letter X); a real-structure number
            keeps its checksum rule; asking for a third person's address.
"""

from __future__ import annotations

import pytest

from chat.classify import ChatClassifier, detect_scam, normalize
from chat.classify.doxxing import DoxxingDetector, find_numbers
from chat.classify.links import find_links, refang
from chat.classify.rules import default_matcher
from chat.testing.synthetic_chat import verhoeff_check_digit
from contracts import Category, ChatAction, ChatMessage


def flagged(text: str) -> bool:
    return default_matcher().match(normalize(text)).matched


def msg(text: str, i: int = 1, **kw) -> ChatMessage:
    return ChatMessage("twitch", f"g{i}", "chan", f"viewer{i}", "viewer", text, 1_700_000_000.0, **kw)


def verdict(text: str):
    return ChatClassifier().classify_fast(msg(text)).verdict


def scam_cats(text: str) -> set[Category]:
    return {c.category for c in detect_scam(normalize(text).link_text) if c.score >= 0.9}


# --- abuse: spelling, separators, scripts --------------------------------------------------------------

ABUSE_VARIANTS = [
    "tu chhootiya hai",
    "kya gaandooo banda hai",
    "bhosdika hai tu",
    "oye bhenchodd",
    "m@darchod",
    "b h e n c h o d",
    "ch.utiya log",
    "ma-darchod",
    "mad*rchod",
    "bh*sdike",
    "g4ndu",
    "ｍａｄａｒｃｈｏｄ",
    "chu​tiya",
    "tu हरामी hai",
    "तू गांडू है",
    "अबे चूतिये",
    "madar chod",
    "bhen ke laude",
    "lawde ke baal",
    "randdi",
]


@pytest.mark.parametrize("text", ABUSE_VARIANTS)
def test_abuse_spelling_and_obfuscation_variants(text: str) -> None:
    assert flagged(text), text


ABUSE_OTHER = [
    "you piece of garbage",
    "just go die",
    "hope you get cancer",
    "tu nikamma hai bilkul",
    "abe gadhe ki aulad",
    "bina kapdo ke aao stream pe",
    "raat ko akeli milegi kya",
    "tujhe maarunga main",
    "i know where you live",
    "swat team bhej dunga",
    "haath tod dunga tera",
    "mkc",
    "bcmc",
]


@pytest.mark.parametrize("text", ABUSE_OTHER)
def test_abuse_other_phrasings(text: str) -> None:
    assert flagged(text), text


# --- abuse: context-aware words stay quiet when used neutrally ----------------------------------------------

NEUTRAL = [
    "mera kutta kal bimar tha",
    "kutte ki photo bhejo please",
    "pados ka kutta bhaunk raha hai",
    "bc ka full form kya hota hai",
    "bbc news dekha kya",
    "https://www.bbc.com/news/world",
    "Lund University Sweden mein hai",
    "Randy Orton aaya tha entry pe",
    "sale lagi hai flipkart pe",
    "aaj ki sale kab tak hai",
    "kaminey ka gaana lagao",
    "sab cutie pie lag rahi hai",
    "bhai chutney kaisi banti hai",
    "Chaudhary sahab ka stream hai",
    "gandhi ji ki photo",
    "ullu bana diya tune mujhe lol",  # mild, not aimed
    "that was a dumb mistake by the game",
    "what a pathetic lag in this match",
    "the trash can in the map is funny",
    "I will destroy you in the next round gg",
]


@pytest.mark.parametrize("text", NEUTRAL)
def test_neutral_uses_are_not_flagged(text: str) -> None:
    assert not flagged(text), text


FRAMED = [
    "tu kutta hai",
    "kutte jaisa khelta hai",
    "abe kutte",
    "ye banda kutta hai",
    "saale tu hi chutiya hai",
    "you are an idiot",
    "tera gameplay trash hai",
]


@pytest.mark.parametrize("text", FRAMED)
def test_same_words_aimed_at_someone_are_flagged(text: str) -> None:
    assert flagged(text), text


def test_links_are_never_read_as_words() -> None:
    n = normalize("dekho https://www.bbc.com/news aur bit.ly/bc-mc-gandu-video")
    assert "bbc" not in n.text and "gandu" not in n.text


def test_short_lexicon_words_need_an_exact_spelling() -> None:
    # phonetic folding must not turn ordinary English words into slurs
    for word in ("cut", "chat", "cutie", "sheet", "count", "cake", "sunny", "beach", "niger", "gouda"):
        assert not flagged(word), word
        assert not flagged(f"tu {word} hai"), word


# --- scam / fake UPI ------------------------------------------------------------------------------------------

UPI_OBFUSCATED = [
    "pay 99 to shop4567 (at) okaxis",
    "shop4567 [at] ybl pe bhejo 50 rs",
    "refund ke liye ₹20 bhejo shop4567 at the rate paytm",
    "send 10 rupees to my.shop-7 {at} okicici",
    "₹50 भेजो shop99 एट द रेट ybl",
    "shop4567 @ ybl pe payment karo",
    "pay shop4567＠paytm now",
]


@pytest.mark.parametrize("text", UPI_OBFUSCATED)
def test_obfuscated_upi_ids_are_fake_upi(text: str) -> None:
    assert Category.CHAT_FAKE_UPI in scam_cats(text), text
    v = verdict(text)
    assert v.action is ChatAction.DELETE and v.categories[0] is Category.CHAT_FAKE_UPI


def test_ordinary_at_is_not_a_upi_id() -> None:
    for text in ("meet me at paytm office", "lets talk at ybl tomorrow", "mail me at rahul@gmail.com"):
        assert Category.CHAT_FAKE_UPI not in scam_cats(text), text


SCAM_LINKS = [
    "claim your gift http://203.0.113.9/offer",
    "check this out http://198.51.100.7/free",
    "kyc update karo nahi to account band hxxps://sbi-secure-kyc.top/login",
    "get reward now paytm-cashback-offer[.]click/win",
    "bhai loan chahiye? turant approval instantloan24.buzz/apply",
    "your account is blocked verify now at hdfc-bank-verify . site",
    "free diamond milega yaha jaao t . ly/xyz",
    "lucky winner ho tum http://amazon-gift-winner.xyz",
    "youtube support: appeal your strike: ytb-appeal-center.vip/form",
    "telegram pe join karo double paisa guaranteed freeearn-india.site/join",
    "hack download karo unlimited uc bgmi-hack-apk.monster",
    "aapne lottery jeeti hai claim karo lotterywinner-india.cc",
]


@pytest.mark.parametrize("text", SCAM_LINKS)
def test_scam_link_structures(text: str) -> None:
    assert Category.CHAT_SCAM_LINK in scam_cats(text), text


HARMLESS_LINKS = [
    "ye dekho https://github.com/someone/project",
    "https://www.youtube.com/watch?v=abc123 mast hai",
    "https://en.wikipedia.org/wiki/Hinglish",
    "clip: https://clips.twitch.tv/SomeClipName",
    "mail me at john.doe@gmail.com for collabs",
    "streamer ka insta instagram.com/someone",
    "free diamond kaise milte hain bina link ke?",
    "how do i get free uc without any hack",
    "check the patch notes https://store.steampowered.com/news",
    "paytm se recharge kiya aaj",
    "bhai upi se pay karna hai kaise karu",
]


@pytest.mark.parametrize("text", HARMLESS_LINKS)
def test_harmless_links_and_questions_are_not_scams(text: str) -> None:
    assert scam_cats(text) == set(), text


def test_refang_and_find_links() -> None:
    assert refang("hxxp://a-b[.]top/x") == "http://a-b.top/x"
    assert refang("go to claim-it dot xyz/now") == "go to claim-it.xyz/now"
    assert refang("id (at) ybl") == "id@ybl"
    (link,) = find_links(refang("see sbi-login-update[.]top/kyc."))
    assert link.domain == "sbi-login-update.top" and link.path == "/kyc" and not link.is_ip
    assert find_links("mail a.b@example.com now") == []
    assert find_links("version 1.2.3 released") == []


def test_fixed_links_flow_through_cascade_as_delete() -> None:
    v = verdict("verify your kyc now http://203.0.113.50/sbi")
    assert v.action is ChatAction.DELETE and Category.CHAT_SCAM_LINK in v.categories


# --- doxxing: SPECIMEN forms and third-party requests ---------------------------------------------------------


def _aadhaar_like(first: str) -> str:
    body = first + "23456789" + "01"
    return body + verhoeff_check_digit(body)


@pytest.mark.parametrize("first", ["0", "1", "2", "7"])
def test_verhoeff_valid_twelve_digits_are_aadhaar_like_for_specimen_and_real_structure(first: str) -> None:
    number = _aadhaar_like(first)
    assert len(number) == 12
    kinds = [k for k, _ in find_numbers(f"uska number {number} hai")]
    assert "aadhaar" in kinds


def test_twelve_digits_with_bad_checksum_are_not_aadhaar_like() -> None:
    number = _aadhaar_like("1")
    bad = number[:-1] + str((int(number[-1]) + 3) % 10)
    assert "aadhaar" not in [k for k, _ in find_numbers(bad)]


def test_pan_specimen_letter_x_and_real_holder_types() -> None:
    d = DoxxingDetector()
    for pan in ("QRSXT4821Z", "qrsxt4821z", "QRSPT4821Z", "QRSCT4821Z"):
        reasons = [c.reason for c in d.detect(normalize(f"his pan is {pan}"))]
        assert "dox_pan" in reasons, pan
    for pan in ("QRSZT4821Z", "QRSQT4821Z", "QRSXT48217", "XQRSXT4821Z"):
        assert "dox_pan" not in [c.reason for c in d.detect(normalize(f"his pan is {pan}"))], pan


ASKING = [
    "iska ghar ka address kisi ko pata ho to batao",
    "uski real location nikalo koi",
    "streamer ka phone number leak karo yaar",
    "his home address do guys",
    "uska number chahiye, dm karo",
    "इसका पता बताओ कोई",
]


@pytest.mark.parametrize("text", ASKING)
def test_asking_for_someones_details_is_doxxing(text: str) -> None:
    reasons = [c.reason for c in DoxxingDetector().detect(normalize(text))]
    assert "dox_intent" in reasons, text


NOT_ASKING = [
    "mera address bar me link paste karo",
    "bhai address bar kahan hai",
    "iska number of kills kitna hai",
    "uski location kaun si map hai",
    "his number 7 jersey is cool",
    "share your screen please",
    "my number is not working today",
]


@pytest.mark.parametrize("text", NOT_ASKING)
def test_ordinary_use_of_the_same_words_is_not_doxxing(text: str) -> None:
    assert [c.reason for c in DoxxingDetector().detect(normalize(text))] == [], text
