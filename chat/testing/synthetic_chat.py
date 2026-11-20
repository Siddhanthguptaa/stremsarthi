"""SPECIMEN synthetic chat set for the classifier cascade (deterministic, no real people, no real data).

Every phone number, UPI ID, PAN and Aadhaar-like number here is an obvious pattern (``98765 43210``,
``specimen@ybl``, ``ABCPE1234F``, repeated digits with a computed check digit) and never belongs to anyone.
Abusive lines are built from the same kind of terms the lexicons hold, only so the matcher can be
measured. The set is a stop-gap until the eval-engineer's chat set (T4.1) lands in ``eval/``:
it is written by the same author as the lexicons, so recall measured on it is optimistic.

``build_set(seed)`` returns ``list[Sample]``. ``label`` is one of
``clean | abuse | scam | fake_upi | doxxing``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5), (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7), (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3), (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)  # fmt: skip
_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4), (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7), (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)  # fmt: skip
_INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def verhoeff_check_digit(body: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(body)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[c])


@dataclass(frozen=True, slots=True)
class Sample:
    text: str
    label: str  # clean | abuse | scam | fake_upi | doxxing
    kind: str  # sub-type, for per-slice reporting


CLEAN = [
    "bhai kya gameplay hai",
    "op bhai aaj to maza aa gaya",
    "nice stream bro",
    "first",
    "hello everyone",
    "gg well played",
    "kitne baje aaoge kal",
    "bhai sound nahi aa raha",
    "audio thoda low hai",
    "lets goooo",
    "bhai skin kaunsi hai ye",
    "rank push kab karoge",
    "kya sensitivity hai teri",
    "haan bhai bilkul sahi",
    "ye wala clutch dekha kya",
    "chalo ab next match",
    "bhai thoda zoom karo",
    "subscribed bhai",
    "love from Bihar",
    "love from Karnataka bro",
    "bhai mera naam bol do",
    "pehle wale stream se better hai",
    "mast edit hai",
    "bhai music konsa hai",
    "camera angle badlo",
    "full screen karo na",
    "bhai 100k kab hoga",
    "congrats bhai",
    "lag ho raha hai kya",
    "stream choppy hai",
    "what is the loadout",
    "which sensi do you use",
    "nice headshot",
    "that was insane",
    "how do you play so well",
    "can you play ranked next",
    "cant wait for the tournament",
    "bhai tournament kab hai",
    "aaj ka plan kya hai",
    "bhai ghar pe sab theek",
    "kal exam hai par stream dekh raha hu",
    "bhai tu to pro hai",
    "tum log kahan se ho",
    "bhai mc stan ka gana lagao",
    "mc stan fan here",
    "mc donalds ka ad aaya kya",
    "chhutti hai aaj isliye aaya",
    "kal chutti hai bhai",
    "gandhi jayanti ki chutti",
    "chaudhary sir ki class thi",
    "bhosale sir ne bola",
    "randhir bhai ne bola tha",
    "pagal ho gaya kya bhai",
    "kya bakwas game hai",
    "bhai ye game bekar hai",
    "bhai tension mat le",
    "chod na bhai jaane de",
    "chod do ye topic",
    "sab chod ke aaya hu stream ke liye",
    "mera phone 6 gb ram ka hai",
    "score 10 5 tha last match",
    "4 kills 2 deaths",
    "match 7 me 12 kill kiye",
    "price 599 rupees hai",
    "ye 1299 ka hai",
    "ping 45 ms hai",
    "room id 123456 hai kya",
    "bhai 2024 me kya hoga",
    "uid pe friend request bhejo",
    "add me in game",
    "please play my song request",
    "bhai address bar me type karo",
    "pin the comment please",
    "pin kar do ye message",
    "bhai mera pin code yaad nahi",
    "road pe chalte time game khel raha hu",
    "near me koi hai kya",
    "plot twist tha ye",
    "flat tire ho gaya aaj",
    "sector 7 map kaisa hai",
    "colony me net slow hai",
    "ghar pe net nahi chal raha",
    "ghar se stream dekh raha hu",
    "send me the link of the game",
    "link bhejo na bhai game ka",
    "link in bio kaha hai",
    "click on subscribe bell",
    "dm me for collab bhai",
    "pay attention to the minimap",
    "payment hua kya merch ka",
    "bhai merch ka link do",
    "upi se donate karna hai kaise",
    "super chat kaise karte hai",
    "gpay se super chat nahi hota kya",
    "free fire max download link",
    "free fire ya bgmi kaun sa better",
    "free fire me diamond kaise kharidte hai",
    "bgmi UC top up kaha se kare",
    "giveaway kab hoga bhai",
    "giveaway ka wait hai",
    "next giveaway kab",
    "winner kaun hai bhai",
    "prize kya hai",
    "bhai tu mera inspiration hai",
    "you are the best streamer",
    "dont be toxic guys",
    "bhai mat ladho",
    "ye dekho: https://youtube.com/watch?v=abc123",
    "check this clip https://clips.twitch.tv/abcd",
    "mail: specimen@example.com",
    "contact me at someone@gmail.com for business",
    "@streamer bhai play ranked",
    "@streamer hello bhai",
    "@streamer love you bro",
    "idiot mode on lol",
    "loser queue me phase gaya",
    "lol ur so good",
    "saale ne headshot maar diya",
    "saala kya clutch tha",
    "bakchodi band karo guys",
    "kamina hai ye enemy",
    "bhai ye kaisa bc lag raha hai",
    "arre yaar kya kar raha hai",
    "आज का स्ट्रीम बहुत मस्त था",
    "भाई गेमप्ले कमाल का है",
    "नमस्ते भाई",
    "कल कब आओगे स्ट्रीम पर",
    "बहुत बढ़िया खेले भाई",
    "ये गेम कौन सा है",
    "आवाज़ नहीं आ रही है",
    "छुट्टी है आज इसलिए आया हूँ",
    "गांधी जी की जय",
    "भाई मुझे भी खिलाओ",
    "यह वीडियो बहुत अच्छा है",
    "வணக்கம் அண்ணா",
    "அருமையான ஆட்டம்",
    "నమస్కారం అన్న",
    "చాలా బాగుంది",
    "ನಮಸ್ಕಾರ ಅಣ್ಣ",
    "ತುಂಬಾ ಚೆನ್ನಾಗಿದೆ",
    "নমস্কার দাদা",
    "খুব ভালো খেললে",
    "anna super",
    "thalaiva",
    "semma gameplay",
    "bagundi anna",
    "chennagide bro",
    "ek number bhai",
    "bhai kamaal kar diya",
    "😂😂😂",
    "🔥🔥🔥",
    "❤️",
    "gg",
    "ez",
    "noob",
    "nub team",
    "trash game",
    "bhai tera bhai hu main",
    "behen ki shaadi hai isliye late aaya",
    "maa ne bola stream band karo",
    "papa bula rahe hai bye",
    "bhai teri behen ko bhi dikhao stream",
    "teri maa ko bhi bula le stream pe",
    "teri maa ki kasam bhai op",
    "bhai ek kaam kar rank push kar",
    "ghar ka kaam khatam karke aaya",
    "bhai ye sab fake hai kya",
    "scam hai kya ye",
    "bhai scam alert dena chahiye",
    "beware of fake giveaways guys",
    "report this scammer please",
    "dont click unknown links",
]
CLEAN_TEMPLATES = [
    "{a} {b}",
    "{b} {a}",
    "{a} {b} {c}",
]
CLEAN_PARTS_A = ["bhai", "bro", "yaar", "guys", "boss", "sir", "anna", "dada", "op"]
CLEAN_PARTS_B = [
    "kya shot tha",
    "ye map mast hai",
    "full volume me sun raha hu",
    "ab kya khelte ho",
    "sound check karo",
    "aaj late kyu aaya",
    "thoda aur zoom",
    "match jeetna hai",
    "next kab",
    "nice setup",
    "mic kaisa hai",
    "stream me lag hai",
    "bgmi kab khelega",
    "valorant khelo",
    "minecraft khelo",
    "cricket match dekha kya",
    "ipl score kya hai",
    "kal ka plan",
    "intro mast tha",
    "edit kisne kiya",
]

ABUSE_PREFIX = ["", "", "", "tu ", "abe ", "bhai ", "@streamer ", "oye ", "arre ", "tera "]
ABUSE_SUFFIX = ["", " hai tu", " bhai", " streamer", " kahin ka", " sale", " ho tum", ""]
ABUSE_LATIN = [
    "madarchod",
    "behenchod",
    "bhenchod",
    "bhosdike",
    "chutiya",
    "chutiye",
    "gandu",
    "randi",
    "harami",
    "haramkhor",
    "bakchod",
    "lund",
    "lauda",
    "kutta",
    "kutte",
    "kamina",
    "bsdk",
    "bkl",
    "mkc",
    "jhatu",
    "bhadwa",
    "chinal",
    "fuck you",
    "asshole",
    "bitch",
    "bastard",
    "motherfucker",
    "whore",
    "slut",
    "cunt",
]
ABUSE_DEVANAGARI = [
    "मादरचोद",
    "बहनचोद",
    "भेनचोद",
    "चूतिया",
    "चुतिया",
    "गांडू",
    "रंडी",
    "हरामी",
    "हरामखोर",
    "कुत्ता",
    "कमीना",
    "भोसड़ीके",
    "बकचोद",
    "लंड",
    "भड़वा",
    "छिनाल",
    "झांट",
    "साला",
]
ABUSE_REGIONAL = [
    "otha",
    "punda",
    "thevidiya",
    "koothi",
    "lanja",
    "lanjakoduku",
    "munda",
    "dengey",
    "sule maga",
    "bosudi",
    "khanki",
    "magi",
    "bokachoda",
    "haramjada",
    "ஓத்தா",
    "புண்டை",
    "தேவடியா",
    "లంజ",
    "ముండ",
    "ಸೂಳೆ ಮಗ",
    "ಬೋಸುಡಿ",
    "মাগি",
    "খানকি",
    "চোদনা",
]
ABUSE_OBFUSCATED = [
    "m a d a r c h o d",
    "m*darchod",
    "b.c",
    "m.c",
    "madarrrchod",
    "bhenchodddd",
    "chutiyaaaa",
    "g4ndu",
    "r4ndi",
    "ch*tiya",
    "l*nd",
    "b h e n c h o d",
    "mad​archod",
    "bh3nchod",
    "m@darchod",
    "h4r4mi",
    "bhosd1ke",
    "gaandu",
    "ｂｈｅｎｃｈｏｄ",
    "chut1ya",
]
ABUSE_THREAT = [
    "maar dunga tujhe",
    "jaan se maar dunga",
    "kaat dunga tujhe",
    "tujhe utha lunga",
    "ghar jala denge tera",
    "i will kill you",
    "kill yourself",
    "goli maar dunga",
    "मार दूंगा तुझे",
    "जान से मार दूंगा",
    "काट दूंगा",
    "मार डालूंगा",
]
ABUSE_SEXUAL = [
    "rape kar dunga",
    "send nudes",
    "nude bhej",
    "boobs dikha",
    "balatkar kar dunga",
    "बलात्कार करूंगा",
    "chudai karenge",
]
# abuse a hand-built lexicon does not know: these keep recall honest
ABUSE_UNKNOWN = [
    "nalayak insaan",
    "tu pura bewakoof gadha hai",
    "teri shakal dekh ke ulti aati hai",
    "tujhe stream band kar dena chahiye ghatiya",
    "bhikhari kahin ka",
    "dimag ka dahi kar diya tune",
    "tu to nikamma hai bilkul",
    "gutter ka keeda hai tu",
    "chindi streamer hai tu",
    "tumhari jaisi ladkiyon ko game nahi khelna chahiye",
    "kala kaluta hai tu",
    "mota bhains hai",
    "tera chehra dekh ke ghin aati hai",
    "dhandhe wali hai ye",
    "tu to ek number ka phattu hai",
    "bhaag yaha se nikamme",
    "stream band kar chutiyap mat dikha",
    "thook ke chat ke",
]

SCAM_TEMPLATES = [
    "FREE FIRE DIAMONDS click here {url}",
    "free fire diamonds claim now {url}",
    "giveaway winner dm me on telegram",
    "congratulations you won iphone claim now {url}",
    "you have won 50000 rs claim here {url}",
    "claim your free UC {url}",
    "free robux {url} click now",
    "lucky draw winner contact whatsapp +000",
    "giveaway: join telegram {url}",
    "earn 5000 daily from home dm me on whatsapp",
    "invest 1000 get 10000 dm telegram",
    "crypto trading profit dm inbox",
    "send 100 rs to get 1000 rs paytm {url}",
    "recharge free {url} click",
    "ye link pe click karo muft diamond {url}",
    "bhai free fire diamond chahiye to {url} pe jao",
    "prize jeeta hai tumne claim karo {url}",
    "inaam jeeta whatsapp karo",
    "pay 99 rs and get giveaway entry {url}",
    "{url} pay now to claim reward",
    "paytm.me/specimen pay here",
    "phonepe.com/specimen/pay now",
    "razorpay.me/specimen",
    "rzp.io/specimen pay",
]
SCAM_URLS = [
    "bit.ly/specimen1",
    "tinyurl.com/specimen2",
    "t.ly/specimen3",
    "cutt.ly/specimen4",
    "rb.gy/specimen5",
    "is.gd/specimen6",
    "https://specimen-rewards.xyz/win",
    "www.specimen-prize.online",
]
FAKE_UPI_TEMPLATES = [
    "upi://pay?pa=specimen@ybl&pn=Test&am=100",
    "send money to specimen@ybl",
    "pay 500 to specimen@okaxis now",
    "donate here specimen@paytm",
    "bhejo paisa specimen@oksbi",
    "upi://pay?pa=specimen@okhdfcbank&am=1",
    "scan and pay specimen@ibl",
    "payment karo specimen@axl 100 rs",
    "gpay se bhejo specimen@okicici",
    "enter your upi pin to receive money",
    "UPI PIN daalo aur paise pao",
    "specimen@apl pe 50 rs bhejo refund milega",
    "pay?pa=specimen@ybl",
    "phonepe://pay?pa=specimen@ybl",
    "mere upi pe bhej do specimen@upi",
    "cashback ke liye specimen@fbl pe 10 rs bhejo",
]
PHONE_FORMATS = [
    "{a}",
    "{a5} {b5}",
    "+91 {a5} {b5}",
    "+91-{a5}-{b5}",
    "{a5}-{b5}",
    "0{a}",
    "91{a}",
    "{a3} {b3} {c4}",
    "({a5}) {b5}",
    "{a2} {b2} {c2} {d2} {e2}",
]
PHONE_TEXT = [
    "call him on {p}",
    "uska number hai {p}",
    "number note karo {p}",
    "{p} yahi number hai uska",
    "whatsapp {p} pe bhejo",
    "iska phone {p}",
    "contact {p}",
    "mera number {p}",
    "isko call karo {p} pe",
]
DOX_TEXT = [
    "uska ghar flat 12 sector 9 pin 400001",
    "address leak: house 22 gali 4 near temple 110001",
    "ghar ka address hai plot 5 road 3 560001",
    "he lives near temple road 600001",
    "pata hai uska: colony 7, nagar, 700001",
    "pin code 411001 address uska ghar",
    "dox him bro",
    "leak his address",
    "ghar ka address do uska",
    "doxx karo isko",
    "ye uska PAN hai ABCPE1234F",
    "pan: ABCPE1234F",
    "AADHAAR {aadhaar}",
    "uska aadhaar {aadhaar} hai",
    "aadhar number {aadhaar}",
    "{nine} number hai",
    "rohan specimen verma ka ghar",
    "he studies at specimen public school",
    "specimen city me rehta hai",
    "मेरा नंबर ९८७६५४३२१० है",
    "उसका नंबर ९८७६५४३२१० है",
    "ghar ka pata leak kar do uska",
    "iska number 9 8 7 6 5 4 3 2 1 0",
    "double nine eight seven six five four three two one",
]
DOX_NEGATIVE_NUMBERS = [
    "room id 1234567890",
    "order 1234567890 delivered",
    "tracking id 5551234567",
    "2024 11 21 stream",
    "100 200 300 400",
    "score 1 2 3 4 5 6 7 8 9 10 11 12",
    "price 400001",
    "400001 views ho gaye",
    "bhai 560001 subscribers",
]
PRIVATE_TERMS = ["rohan specimen verma", "specimen city", "specimen public school"]
PRIVATE_PHONES = ["+91 98765 43210"]


def _phone_variants(rng: random.Random) -> str:
    # synthetic-looking Indian mobile numbers: 9/8/7/6 prefix, then a counting pattern
    first = rng.choice("6789")
    rest = "".join(rng.choice("0123456789") for _ in range(9))
    digits = first + rest
    fmt = rng.choice(PHONE_FORMATS)
    return fmt.format(
        a=digits,
        a5=digits[:5], b5=digits[5:],
        a3=digits[:3], b3=digits[3:6], c4=digits[6:],
        a2=digits[0:2], b2=digits[2:4], c2=digits[4:6], d2=digits[6:8], e2=digits[8:10],
    )  # fmt: skip


def _aadhaar_like(rng: random.Random) -> str:
    body = rng.choice("23456789") + "".join(rng.choice("0123456789") for _ in range(10))
    num = body + verhoeff_check_digit(body)
    return rng.choice([num, f"{num[:4]} {num[4:8]} {num[8:]}"])


def build_set(seed: int = 4242) -> list[Sample]:
    rng = random.Random(seed)
    out: list[Sample] = []
    for t in CLEAN:
        out.append(Sample(t, "clean", "curated"))
    for _ in range(300):
        a, b = rng.choice(CLEAN_PARTS_A), rng.choice(CLEAN_PARTS_B)
        out.append(
            Sample(
                rng.choice(CLEAN_TEMPLATES).format(a=a, b=b, c=rng.choice(CLEAN_PARTS_B)), "clean", "template"
            )
        )
    for t in DOX_NEGATIVE_NUMBERS:
        out.append(Sample(t, "clean", "numbers"))

    def abuse(pool: list[str], kind: str, n: int) -> None:
        for _ in range(n):
            core = rng.choice(pool)
            out.append(
                Sample(f"{rng.choice(ABUSE_PREFIX)}{core}{rng.choice(ABUSE_SUFFIX)}".strip(), "abuse", kind)
            )

    abuse(ABUSE_LATIN, "latin", 90)
    abuse(ABUSE_DEVANAGARI, "devanagari", 40)
    abuse(ABUSE_REGIONAL, "regional", 40)
    abuse(ABUSE_OBFUSCATED, "obfuscated", 40)
    for t in ABUSE_THREAT:
        out.append(Sample(t, "abuse", "threat"))
    for t in ABUSE_SEXUAL:
        out.append(Sample(t, "abuse", "sexual"))
    for t in ABUSE_UNKNOWN:
        out.append(Sample(t, "abuse", "unknown_to_lexicon"))

    for _ in range(100):
        out.append(Sample(rng.choice(SCAM_TEMPLATES).format(url=rng.choice(SCAM_URLS)), "scam", "scam"))
    for t in SCAM_TEMPLATES:
        if "{url}" not in t:
            out.append(Sample(t, "scam", "scam_fixed"))
    for _ in range(60):
        out.append(Sample(rng.choice(FAKE_UPI_TEMPLATES), "fake_upi", "fake_upi"))
    for _ in range(80):
        out.append(Sample(rng.choice(PHONE_TEXT).format(p=_phone_variants(rng)), "doxxing", "phone"))
    for t in DOX_TEXT:
        out.append(
            Sample(
                t.format(
                    aadhaar=_aadhaar_like(rng), nine="nine eight seven six five four three two one zero"
                ),
                "doxxing",
                "dox_misc",
            )
        )
    rng.shuffle(out)
    return out
