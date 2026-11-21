"""Doxxing messages (SPECIMEN phone numbers in many spellings, addresses with PIN codes, the streamer's configured
private terms, Aadhaar-like and PAN-like numbers, intent phrases), hard negatives (numbers and the word "address" in
harmless use), and staff messages (a broadcaster's own data must pass; a moderator quoting a stranger's number is
flagged but never hidden)."""

from __future__ import annotations

import random
from collections.abc import Callable

from ..common import specimen
from ..common.identifiers import DEVANAGARI_DIGITS
from .record import Msg, pick

WORDS_HI = ["zero", "ek", "do", "teen", "char", "paanch", "chhe", "saat", "aath", "nau"]
WORDS_EN = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
CITIES = [
    "Pune",
    "Indore",
    "Mysuru",
    "Chennai",
    "Hyderabad",
    "Kolkata",
    "Jaipur",
    "Nagpur",
    "Lucknow",
    "Surat",
]
BUILDINGS = ["Sunrise", "Lotus", "Green Valley", "Blue Orchid", "Specimen", "Maple Court", "Silver Oak"]
COLONIES = ["Shanti Nagar", "Gandhi Colony", "Model Town", "Lake View Society", "Specimen Vihar", "Ram Nagar"]


def phone_forms(num: str, rng: random.Random) -> tuple[str, str]:
    """(spelling, form name) of a 10-digit number."""
    form = rng.choice(
        [
            "plain",
            "spaced",
            "dash",
            "plus91",
            "plus91_dash",
            "zero",
            "parens",
            "digit_spaced",
            "devanagari",
            "split3",
            "dotted",
        ]
    )
    a, b = num[:5], num[5:]
    text = {
        "plain": num,
        "spaced": f"{a} {b}",
        "dash": f"{a}-{b}",
        "plus91": f"+91 {a} {b}",
        "plus91_dash": f"+91-{num}",
        "zero": f"0{num}",
        "parens": f"(+91) {num}",
        "digit_spaced": " ".join(num),
        "devanagari": "".join(DEVANAGARI_DIGITS[int(c)] for c in num),
        "split3": f"{num[:4]} {num[4:7]} {num[7:]}",
        "dotted": f"{a}.{b}",
    }[form]
    return text, form


def spoken_phone(num: str, rng: random.Random) -> str:
    words = WORDS_HI if rng.random() < 0.6 else WORDS_EN
    out: list[str] = []
    i = 0
    while i < len(num):
        if i + 1 < len(num) and num[i] == num[i + 1] and rng.random() < 0.6:
            out.append(rng.choice(["double", "dabal"]))
            out.append(words[int(num[i])])
            i += 2
        else:
            out.append(words[int(num[i])])
            i += 1
    return " ".join(out)


PHONE_CONTEXTS = [
    "iska number {p} hai call karo", "{p} pe call karke pareshan karo", "uska whatsapp {p} spam karo sab", "this guy's number: {p}",
    "uski maa ka number {p} hai", "{p} ye number viral karo", "ye hai uska personal number {p}", "{p} call karo aur bolo stream band kare",
    "number nikal liya {p}", "streamer ka asli number {p}", "dm mat karo seedha call karo {p}", "sab log isko {p} pe msg karo",
    "फोन नंबर {p} है इसका कॉल करो", "इसका नंबर {p} सब फैलाओ", "{p}", "call him {p}", "his mom's number is {p} tell her", "{p} ye number leak hua hai",
]  # fmt: skip


def third_party_phone(rng: random.Random) -> str:
    return specimen.phone_number(rng)


def _uniq(n: int, make: Callable[[], Msg]) -> list[Msg]:
    out: list[Msg] = []
    seen: set[str] = set()
    tries = 0
    while len(out) < n and tries < n * 60:
        tries += 1
        m = make()
        if m.text not in seen:
            seen.add(m.text)
            out.append(m)
    return out


def _phone(rng: random.Random) -> Msg:
    p, form = phone_forms(third_party_phone(rng), rng)
    text = pick(rng, PHONE_CONTEXTS).format(p=p)
    lang = (
        "deva"
        if any("ऀ" <= c <= "ॿ" for c in text)
        else ("en" if text.startswith(("this", "his", "call him")) else "hinglish")
    )
    return Msg(text, "doxxing", "phone", lang, tags=[f"form:{form}"])


def _phone_spoken(rng: random.Random) -> Msg:
    p = spoken_phone(third_party_phone(rng), rng)
    text = pick(
        rng, ["iska number {p}", "uska number hai {p} call karo", "his number {p}", "{p} ye number hai uska"]
    ).format(p=p)
    return Msg(text, "doxxing", "phone_spoken", "hinglish", tags=["form:spoken"])


def _phone_embedded(rng: random.Random) -> Msg:
    num = third_party_phone(rng)
    text = pick(
        rng, ["tel:{n}", "call{n}now", "no.{n}", "ph:{n}", "contact-{n}", "mob {n} hai uska", "num={n}"]
    ).format(n=num)
    return Msg(text, "doxxing", "phone_embedded", "hinglish", tags=["form:embedded"])


def address_text(rng: random.Random) -> tuple[str, str]:
    flat = rng.randint(101, 989)
    pin = specimen.pin_code(rng)
    city = pick(rng, CITIES)
    form = rng.randrange(5)
    if form == 0:
        return (
            f"flat {flat} {pick(rng, BUILDINGS)} apartments sector {rng.randint(2, 48)} {city} {pin}",
            "flat",
        )
    if form == 1:
        return f"house no {flat} {pick(rng, COLONIES)} {city} pin {pin}", "house"
    if form == 2:
        return (
            f"plot {flat} {pick(rng, COLONIES)} near {pick(rng, ['bus stand', 'old market', 'water tank'])} {city} - {pin}",
            "plot",
        )
    if form == 3:
        return f"ghar number {flat} {pick(rng, COLONIES)} gali {rng.randint(1, 9)} {city} {pin}", "ghar"
    return f"{flat}/{rng.randint(1, 9)} {pick(rng, BUILDINGS)} society {city} pincode {pin}", "slash"


def _address(rng: random.Random) -> Msg:
    a, form = address_text(rng)
    text = pick(rng, [
        "uska address {a}", "ghar ka pata: {a}", "ye rehta hai yahan {a} jao sab", "address leak: {a}", "his home address is {a}", "iska ghar {a} hai bhej do kuch",
        "yahan rehta hai ye {a} milne jao", "पता: {a}",
    ]).format(a=a)  # fmt: skip
    return Msg(text, "doxxing", "address", "hinglish", tags=[f"form:{form}"])


def streamer_private(rng: random.Random, terms: list[str], phone: str) -> Msg:
    t = pick(rng, terms)
    form = rng.randrange(8)
    tail = pick(rng, ["", "", " sabko bata do", " 😈", " pata kar lo", " jaldi", " yahi hai", " confirm hai"])
    if form == 0:
        text = f"uska asli naam {t} hai"
    elif form == 1:
        text = f"{t} yahi rehta hai"
    elif form == 2:
        p, _ = phone_forms(phone, rng)
        text = f"streamer ka number {p} hai call karo"
    elif form == 3:
        text = f"{t.upper()} ye hai iski asli pehchaan"
    elif form == 4:
        text = f"{t.replace(' ', '  ')} ka ghar yahin hai"
    elif form == 5:
        text = f"sab note kar lo {t}"
    elif form == 6:
        p, _ = phone_forms(phone, rng)
        text = f"{p} ye hai iska private number"
    else:
        text = f"iske baare mein pata chala {t} se judi baat hai"
    return Msg(
        text + tail,
        "doxxing",
        "streamer_private",
        "hinglish",
        tags=["private_phone" if form in (2, 6) else "private_term"],
    )


def _aadhaar_pan(rng: random.Random) -> Msg:
    """SPECIMEN Aadhaar numbers start with 0 or 1 and SPECIMEN PANs have an invalid 4th letter, so a detector that
    validates real-number structure cannot flag them: reported by kind, never gating."""
    if rng.random() < 0.65:
        n = specimen.aadhaar_number(rng)
        shown = pick(rng, [f"{n[:4]} {n[4:8]} {n[8:]}", n, f"{n[:4]}-{n[4:8]}-{n[8:]}"])
        text = pick(
            rng,
            [
                "uska aadhaar {x} hai",
                "aadhaar number {x} iska",
                "his aadhar {x}",
                "{x} ye uska aadhaar card number hai",
            ],
        ).format(x=shown)
        return Msg(text, "doxxing", "aadhaar", "hinglish", gate=False, tags=["specimen_number"])
    pan = specimen.pan_number(rng)
    text = pick(
        rng, ["uska PAN {x}", "pan card number {x} hai iska", "his PAN is {x}", "{x} iska pan hai"]
    ).format(x=pan)
    return Msg(text, "doxxing", "pan", "hinglish", gate=False, tags=["specimen_number"])


INTENTS = [
    "iska address leak karo", "dox him", "uska ghar ka pata nikalo", "address bhej do iska", "ghar ka address do iska koi",
    "kisi ko iska number pata hai? leak karo", "doxx kar do isko", "isko dox karte hain sab milke", "uska ghar ka pata nikal ke bhejo",
    "iska home address chahiye", "leak his address", "uska address kisi ke paas hai to batao", "इसका घर का पता निकालो", "एड्रेस लीक करो इसका",
    "iska number nikalo aur leak karo", "uski location dhundo sab", "who has his home address", "ghar ka pata de do iska please",
]  # fmt: skip


def _intent(rng: random.Random) -> Msg:
    t = pick(rng, INTENTS)
    return Msg(
        pick(rng, ["{t}", "{t} 😈", "sab {t}", "{t} jaldi"]).format(t=t),
        "doxxing",
        "intent",
        "deva" if "ऀ" <= t[0] <= "ॿ" else "hinglish",
    )


DOX_COUNTS = {
    "phone": 75,
    "phone_spoken": 15,
    "phone_embedded": 15,
    "address": 60,
    "streamer_private": 40,
    "aadhaar_pan": 30,
    "intent": 25,
}


def doxxing(
    rng: random.Random, terms: list[str], phone: str, counts: dict[str, int] | None = None
) -> list[Msg]:
    makers: dict[str, Callable[[], Msg]] = {
        "phone": lambda: _phone(rng),
        "phone_spoken": lambda: _phone_spoken(rng),
        "phone_embedded": lambda: _phone_embedded(rng),
        "address": lambda: _address(rng),
        "streamer_private": lambda: streamer_private(rng, terms, phone),
        "aadhaar_pan": lambda: _aadhaar_pan(rng),
        "intent": lambda: _intent(rng),
    }
    out: list[Msg] = []
    for kind, n in (counts or DOX_COUNTS).items():
        out += _uniq(n, makers[kind])
    return out


# --- hard negatives ----------------------------------------------------------------------------------------------------
def _digits(rng: random.Random, n: int, first: str = "0123456789") -> str:
    return rng.choice(first) + "".join(rng.choice("0123456789") for _ in range(n - 1))


def hard_negative_makers(rng: random.Random) -> list[Callable[[], str]]:
    return [
        lambda: f"order id {_digits(rng, 10, '12345')} abhi tak nahi aaya",
        lambda: f"mera bgmi id {_digits(rng, 10, '5')} hai add kar lo",
        lambda: f"uid {_digits(rng, 9, '123')} pe friend request bhejo",
        lambda: f"{_digits(rng, 9, '6789')} followers ho gaye kya itne",  # 9 digits: not a mobile number
        lambda: f"{_digits(rng, 11, '6789')} ye kya number hai random",  # 11 digits
        lambda: (
            f"match time {rng.randint(10, 23)}:{rng.randint(10, 59)}:{rng.randint(10, 59)} pe start hua tha"
        ),
        lambda: f"date {rng.randint(10, 28)}/{rng.randint(10, 12)}/2024 ko hai tournament",
        lambda: f"{rng.randint(1, 9)},{rng.randint(10, 99)},{rng.randint(100, 999)} rupaye ka phone hai",
        lambda: f"{rng.randint(100, 999)}000 subs hone wale hain ab",
        lambda: f"level {rng.randint(10, 99)} pe hu abhi {rng.randint(100, 9999)} points hain",
        lambda: f"112 pe call karo emergency mein {pick(rng, ['', 'bhai', 'yaar'])}".strip(),
        lambda: f"ambulance 108 aur police 100 yaad rakhna {pick(rng, ['', 'sab', 'log'])}".strip(),
        lambda: f"main {pick(rng, CITIES)} mein rehta hu {pick(rng, ['', 'bhai', 'aur tum'])}".strip(),
        lambda: (
            f"{pick(rng, CITIES)} se hu bhai {pick(rng, ['', 'yahan baarish hai', 'garmi bahut hai'])}".strip()
        ),
        lambda: f"mera naam aarav hai {pick(rng, ['', 'aur tumhara', 'nice to meet you'])}".strip(),
        lambda: (
            f"email address kya hai {pick(rng, ['business ke liye', 'collab ke liye', 'sponsor ke liye'])}"
        ),
        lambda: f"IP address change karne se ping kam hota hai kya {pick(rng, ['', 'bhai'])}".strip(),
        lambda: f"website address nahi chal raha {pick(rng, ['', 'bhai', 'yaar'])}".strip(),
        lambda: f"coupon code {_digits(rng, 4)} lagao 10% off milega",
        lambda: f"ATM pin {_digits(rng, 4)} kabhi share mat karna {pick(rng, ['', 'dosto'])}".strip(),
        lambda: f"{_digits(rng, 6, '123456789')} score ka record toot gaya aaj",
        lambda: f"room id {_digits(rng, 7)} password {rng.randint(1000, 9999)} custom match join karo",
        lambda: f"pin code kaise dalte hain form mein {pick(rng, ['', 'bhai', 'koi batao'])}".strip(),
        lambda: f"tracking number {_digits(rng, 13, '1234')} se parcel track karo",
    ]


def hard_negatives(rng: random.Random, n: int) -> list[Msg]:
    makers = hard_negative_makers(rng)
    out: list[Msg] = []
    seen: set[str] = set()
    tries = 0
    while len(out) < n and tries < n * 80:
        tries += 1
        t = " ".join(rng.choice(makers)().split())
        if t not in seen:
            seen.add(t)
            out.append(Msg(t, "clean", "hard_neg_dox", "hinglish", tags=["hard_negative"]))
    return out


# --- staff messages ----------------------------------------------------------------------------------------------------
def staff_self(rng: random.Random, terms: list[str], phone: str, n: int) -> list[Msg]:
    """The broadcaster's own data and ordinary moderator talk: must pass untouched (action ALLOW, not hidden)."""
    out: list[Msg] = []
    seen: set[str] = set()
    tries = 0
    while len(out) < n and tries < n * 80:
        tries += 1
        r = rng.randrange(6)
        if r == 0:
            p, _ = phone_forms(phone, rng)
            m = Msg(
                f"business ke liye mera number {p} hai",
                "clean",
                "staff_self",
                staff="broadcaster",
                tags=["own_phone"],
            )
        elif r == 1:
            m = Msg(
                f"mera asli naam {pick(rng, terms)} hai ye sab jante hain",
                "clean",
                "staff_self",
                staff="broadcaster",
                tags=["own_term"],
            )
        elif r == 2:
            m = Msg(
                pick(
                    rng,
                    [
                        "chat rules follow karo sab",
                        "spam mat karo please",
                        "is user ko timeout de diya",
                        "links allowed nahi hain yahan",
                        "sab shaant raho match chal raha hai",
                    ],
                ),
                "clean",
                "staff_mod",
                staff="moderator",
            )
        elif r == 3:
            m = Msg(
                f"welcome sab log {pick(rng, ['aaj ka stream shuru', 'phir se aa gaye', 'mast chat hai'])} {pick(rng, ['', '🔥', '👍'])}".strip(),
                "clean",
                "staff_mod",
                staff="moderator",
            )
        elif r == 4:
            m = Msg(
                f"{pick(rng, ['thanks for the follow', 'gifted subs ke liye shukriya', 'raid ke liye thanks'])} {pick(rng, ['', '❤️'])}".strip(),
                "clean",
                "staff_self",
                staff="broadcaster",
            )
        else:
            m = Msg(
                f"main {pick(rng, CITIES)} se hu {pick(rng, ['aur yahan', 'bhai', ''])}".strip(),
                "clean",
                "staff_self",
                staff="broadcaster",
            )
        if m.text not in seen:
            seen.add(m.text)
            out.append(m)
    return out


def staff_dox(rng: random.Random, n: int) -> list[Msg]:
    out: list[Msg] = []
    seen: set[str] = set()
    tries = 0
    while len(out) < n and tries < n * 80:
        tries += 1
        p, form = phone_forms(third_party_phone(rng), rng)
        t = pick(
            rng,
            [
                "is user ne is number se spam kiya tha {p}",
                "ban list: {p} ye number block karo",
                "yeh dekho spammer ka number {p}",
                "reported number {p} sab dhyan rakho",
            ],
        ).format(p=p)
        if t not in seen:
            seen.add(t)
            out.append(
                Msg(t, "doxxing_staff", "staff_dox", staff="moderator", gate=False, tags=[f"form:{form}"])
            )
    return out
