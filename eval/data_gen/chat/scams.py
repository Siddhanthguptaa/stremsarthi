"""Scam-link messages, fake UPI payment lures, and benign messages that look similar (legit links, talk about UPI).

Every domain carries the label ``specimen`` and every UPI id is ``zz-specimen-<hex>@<handle>`` or a SPECIMEN
phone pool number, so nothing here points at, or pays, anybody."""

from __future__ import annotations

import random
from collections.abc import Callable

from ..common import specimen
from .record import Msg, pick

TLDS = ["xyz", "top", "click", "icu", "site", "live", "online", "cc", "vip", "club", "monster", "buzz"]
BRANDS = ["freefire", "bgmi", "paytm", "phonepe", "gpay", "sbi", "hdfc", "icici", "jio", "amazon", "flipkart", "dream11", "youtube", "instagram"]
LURES = ["diamond", "uc", "reward", "bonus", "claim", "gift", "winner", "cashback", "offer", "verify", "kyc", "loan", "lucky"]
SHORTENERS = ["bit.ly", "tinyurl.com", "cutt.ly", "rb.gy", "shorturl.at", "is.gd", "t.ly"]
HEX = "0123456789abcdef"


def _code(rng: random.Random, n: int = 5) -> str:
    return "".join(rng.choice(HEX) for _ in range(n))


def fake_domain(rng: random.Random) -> str:
    b, l1, l2 = pick(rng, BRANDS), pick(rng, LURES), pick(rng, LURES)
    form = rng.randrange(4)
    host = {
        0: f"{b}-{l1}-specimen",
        1: f"{l1}{b}-specimen{rng.randint(1, 99)}",
        2: f"{b}.{l1}-{l2}-specimen",
        3: f"get-{l1}-{b}-specimen",
    }[form]
    return f"{host}.{pick(rng, TLDS)}"


def fake_url(rng: random.Random) -> str:
    kind = rng.randrange(6)
    if kind == 0:
        return f"https://{fake_domain(rng)}/{pick(rng, LURES)}?id={_code(rng, 6)}"
    if kind == 1:
        return f"http://{fake_domain(rng)}"
    if kind == 2:
        return f"{fake_domain(rng)}/{_code(rng, 4)}"
    if kind == 3:
        return f"{pick(rng, SHORTENERS)}/specimen-{_code(rng, 5)}"
    if kind == 4:
        return f"t.me/specimen_{pick(rng, LURES)}_{_code(rng, 3)}"
    return f"http://198.51.100.{rng.randint(2, 250)}/{pick(rng, LURES)}"  # TEST-NET-2 address


def disguise(url: str, rng: random.Random) -> tuple[str, str]:
    """A link written so a naive matcher misses it."""
    how = rng.choice(["hxxp", "bracket_dot", "dot_word", "spaced", "caps", "space_slash"])
    if how == "hxxp":
        return url.replace("https", "hxxps").replace("http", "hxxp"), how
    if how == "bracket_dot":
        return url.replace(".", "[.]"), how
    if how == "dot_word":
        return url.replace(".", " dot ", 2), how
    if how == "spaced":
        return url.replace(".", " . ", 2), how
    if how == "caps":
        return url.upper(), how
    return url.replace("/", " / ", 1), how


SCAM_CARRIERS = {
    "giveaway": [
        "FREE FIRE 10000 DIAMONDS FREE claim here {u}", "bhai ye link se free diamond mil rahe hain {u}", "BGMI UC free hack working {u}",
        "streamer ne giveaway rakha hai jaldi claim karo {u}", "congratulations aap winner ho iPhone 15 claim karo {u}", "free redeem code milega {u} jaldi karo",
        "aaj ka giveaway 50000 rs click karo {u}", "सबको फ्री डायमंड मिल रहे हैं यहाँ क्लिक करो {u}", "elite pass free hai {u} sirf aaj ke liye", "100% working free uc generator {u}",
    ],
    "shortener": [
        "ye dekho {u}", "must watch {u}", "bhai jaldi dekh {u} 😱", "open karo sab {u}", "sirf aaj {u}", "exclusive leaked video {u}", "{u} claim your prize", "link in bio {u}",
    ],
    "invite": [
        "join my telegram group daily 5000 profit guaranteed {u}", "work from home 800 rs per hour join whatsapp group {u}", "paisa double hota hai yahan join karo {u}",
        "WhatsApp group join karo free tips milenge {u}", "dream11 grand league winning team telegram pe milti hai {u}", "भाई यहाँ जुड़ो रोज़ कमाई पक्की {u}", "trading signals free join fast {u}",
    ],
    "kyc_bank": [
        "SBI account blocked tonight update KYC now {u}", "HDFC alert your account will be suspended complete KYC {u}", "aapka bank account band ho jayega kyc update karo {u}",
        "Paytm KYC expire ho gayi hai abhi update karo {u}", "आपका खाता बंद हो जाएगा केवाईसी अपडेट करो {u}", "ICICI card block ho gaya verify here {u}",
    ],
    "lottery": [
        "KBC lottery winner aapka number select hua hai {u}", "you have won 25 lakh lottery contact now {u}", "Jio lucky draw 5 lakh winner claim {u}", "aapne car jeeti hai claim karne ke liye {u}",
        "आप लॉटरी जीत गए हैं यहाँ क्लिक करें {u}",
    ],
    "loan": ["instant loan 50000 no documents apply {u}", "turant loan bina document {u}", "personal loan 5 minute me approve {u} limited offer", "loan chahiye? bas ye link {u}"],
    "crypto": [
        "bitcoin double in 24 hours {u}", "invest 1000 get 5000 crypto {u}", "USDT earning platform join {u}", "free airdrop token claim {u}", "ye coin 100x jayega abhi lo {u}",
    ],
    "support_call": [
        "your channel will be banned verify at {u}", "youtube support: appeal your strike here {u}", "account unban chahiye? yahan contact karo {u}", "monetisation verification pending {u}",
    ],
    "phishing": [
        "login here to verify your channel and keep monetisation {u}", "instagram verified badge free apply {u}", "gmail login required to see the clip {u}", "sign in to claim the streamer reward {u}",
    ],
    "hack_tool": [
        "free fire aimbot download {u}", "bgmi wallhack 100% safe {u}", "unlimited diamond hack apk {u}", "headshot hack no ban {u}", "mod apk unlock all skins {u}",
    ],
}
SCAM_COUNTS = {
    "giveaway": 50, "shortener": 30, "invite": 30, "kyc_bank": 25, "lottery": 20, "loan": 15, "crypto": 20, "support_call": 15, "phishing": 15, "hack_tool": 20,
}  # fmt: skip


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


def scams(rng: random.Random, counts: dict[str, int] | None = None) -> list[Msg]:
    out: list[Msg] = []
    for kind, n in (counts or SCAM_COUNTS).items():

        def make(kind: str = kind) -> Msg:
            url = fake_url(rng)
            tags: list[str] = []
            if rng.random() < 0.22:
                url, how = disguise(url, rng)
                tags = ["obfuscated", how]
            text = pick(rng, SCAM_CARRIERS[kind]).format(u=url)
            lang = "deva" if any("ऀ" <= c <= "ॿ" for c in text) else "hinglish"
            return Msg(text, "scam", kind, lang, tags=tags)

        out += _uniq(n, make)
    return out


# --- fake UPI ---------------------------------------------------------------------------------------------------------
LURE_TEXT = [
    "pay {amt} get 1000 diamonds", "send {amt} to claim your prize", "donate {amt} for the streamer's surgery", "{amt} bhejo aur free uc lo",
    "{amt} ki fees do coaching join karo", "verification ke liye {amt} bhejo", "refund ke liye {amt} bhejo", "{amt} bhejo giveaway entry pakki",
    "wrong transfer ho gaya {amt} wapas karo", "stream support: {amt} bhejo shoutout milega", "भाई {amt} भेजो इनाम पक्का", "{amt} भेजो तुरंत कोड मिलेगा",
]
AMOUNTS = ["Rs 10", "₹49", "99 rs", "₹199", "500 rupaye", "₹1", "Rs 25", "1000rs", "₹5"]


def _upi_id(rng: random.Random, phone_style: bool = False) -> str:
    handle = pick(rng, [h for h in specimen.UPI_HANDLES])
    if phone_style:
        return f"{specimen.phone_number(rng)}@{handle}"
    return specimen.upi_id(rng, handle)


def _fake_upi(rng: random.Random, kind: str) -> Msg:
    amt = pick(rng, AMOUNTS)
    lure = pick(rng, LURE_TEXT).format(amt=amt)
    tags: list[str] = []
    if kind == "deeplink":
        uid = _upi_id(rng)
        link = f"upi://pay?pa={uid}&pn=SPECIMEN%20{pick(rng, ['GIVEAWAY', 'SUPPORT', 'REWARD'])}&am={rng.randint(1, 999)}&cu=INR"
        text = pick(rng, ["{l} {k}", "{k}", "{k} {l}", "click karo {k}"]).format(l=lure, k=link)
    elif kind == "app_deeplink":
        uid = _upi_id(rng)
        scheme = pick(rng, ["phonepe://pay?pa=", "paytmmp://pay?pa=", "tez://upi/pay?pa=", "gpay://upi/pay?pa=", "upi://collect?pa="])
        text = f"{lure} {scheme}{uid}&am={rng.randint(1, 500)}"
    elif kind == "pay_to_id":
        uid = _upi_id(rng)
        text = pick(rng, ["{l} on {u}", "{l} UPI: {u}", "{u} pe {l}", "{l} -> {u}", "upi id {u} {l}"]).format(l=lure, u=uid)
    elif kind == "phone_upi":
        uid = _upi_id(rng, phone_style=True)
        text = pick(rng, ["{l} {u}", "gpay number {u} {l}", "{l} on {u}"]).format(l=lure, u=uid)
    elif kind == "collect":
        uid = _upi_id(rng)
        text = pick(rng, [
            "maine {a} ka collect request bheja hai {u} se approve karo prize milega",
            "request approve kar do {u} {a} ka refund milega",
            "approve the collect request from {u} for {a} to receive reward",
        ]).format(a=amt, u=uid)  # fmt: skip
    elif kind == "obfuscated":
        uid = _upi_id(rng)
        local, handle = uid.split("@")
        form = rng.randrange(5)
        tags = ["obfuscated"]
        shown = {
            0: f"{local} [at] {handle}",
            1: f"{local} (at) {handle}",
            2: f"{local} at the rate {handle}",
            3: f"{local} @ {handle}",
            4: f"{local}＠{handle}",
        }[form]
        text = f"{lure} {shown}"
    else:  # qr_lure
        text = pick(rng, [
            "QR scan karke {a} bhejo prize pakka", "scan the QR on screen and pay {a} to unlock", "screen pe jo QR hai usse {a} bhejo shoutout ke liye",
            "scan karke {a} dalo bhai", "QR code scan karo aur {a} pay karo reward lo",
        ]).format(a=amt)  # fmt: skip
    lang = "deva" if any("ऀ" <= c <= "ॿ" for c in text) else "hinglish"
    return Msg(text, "fake_upi", kind, lang, tags=tags)


UPI_COUNTS = {"deeplink": 40, "app_deeplink": 25, "pay_to_id": 50, "phone_upi": 25, "collect": 20, "obfuscated": 25, "qr_lure": 15}


def fake_upi(rng: random.Random, counts: dict[str, int] | None = None) -> list[Msg]:
    out: list[Msg] = []
    for kind, n in (counts or UPI_COUNTS).items():
        out += _uniq(n, lambda k=kind: _fake_upi(rng, k))
    return out


# --- hard negatives ---------------------------------------------------------------------------------------------------
LEGIT_URLS = [
    "https://www.youtube.com/watch?v=AbCdEfGhIjK", "https://youtu.be/AbCdEfGhIjK", "https://www.twitch.tv/specimenstreamer", "https://www.instagram.com/specimen_streamer",
    "https://en.wikipedia.org/wiki/Battle_royale_game", "https://maps.google.com/?q=Pune", "https://github.com/specimen/overlay", "https://www.reddit.com/r/Android",
    "https://store.steampowered.com/app/570", "https://www.bbc.com/news", "https://twitter.com/specimen_streamer", "https://discord.com/channels/specimen",
]
LEGIT_CARRIERS = [
    "bhai ye clip dekho {u}", "yahan se patch notes padho {u}", "streamer ka instagram {u}", "wiki pe likha hai {u}", "map ka link {u}", "yt pe full video {u}",
    "sab ye dekho {u} mast hai", "new trailer {u}", "yaar ye tutorial {u}", "check this {u}",
]
BENIGN_PAY = [
    "bhai maine UPI se pay kar diya", "gpay pe cashback mila aaj", "paytm kar diya bhai", "phonepe down hai kya", "UPI se kitna limit hota hai ek din ka",
    "donation kaise karte hain streamlabs se", "super chat kaise bhejte hain", "free fire ka top up kahan se karte ho", "diamond kitne ke aate hain", "kyc ho gaya kya tumhara",
    "giveaway kab hoga streamer", "free fire khelo bhai", "free diamond kaise milte hain bina link ke", "bitcoin ka rate kitna hai aaj", "telegram group hai kya streamer ka",
    "loan lena hai ghar ke liye papa se baat ki", "lottery ka result aaya kya sunday ko", "paise bhej diye bhai mummy ne", "upi id kaise banate hain", "gpay ya phonepe kaunsa better hai",
    "recharge kar diya tha jio ka", "paytm wallet band ho gaya", "emi pe phone liya maine", "bank mein kaam tha isliye late aaya", "amazon pe sale chal rahi hai",
]  # fmt: skip


def hard_negatives(rng: random.Random, n: int) -> list[Msg]:
    pool: list[str] = []
    for u in LEGIT_URLS:
        for c in rng.sample(LEGIT_CARRIERS, 4):
            pool.append(c.format(u=u))
    pool += BENIGN_PAY * 2
    rng.shuffle(pool)
    out: list[Msg] = []
    seen: set[str] = set()
    for t in pool:
        t2 = t if t not in seen else t + pick(rng, [" 😂", " bhai", " yaar", " 👍"])
        if t2 in seen:
            continue
        seen.add(t2)
        out.append(Msg(t2, "clean", "hard_neg_scam", "hinglish", tags=["hard_negative"]))
        if len(out) >= n:
            break
    return out


__all__ = ["fake_upi", "hard_negatives", "scams"]
