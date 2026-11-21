"""Abuse messages (Hinglish in Latin and Devanagari, regional languages, English, threats, obfuscated spellings)
and the benign messages that merely contain a borderline word. Own vocabulary and templates."""

from __future__ import annotations

import random
from collections.abc import Callable

from .record import Msg, obfuscate, pick

# --- Latin-script (Hinglish) vocabulary ----------------------------------------------------------------------------
SLUR_LATN = [
    "chutiya", "chutia", "chutiye", "chootiya", "chuutiya", "chutiyaa", "chutiyapanti",
    "madarchod", "maderchod", "madrchod", "madar chod", "mdrchod",
    "behenchod", "bhenchod", "bhnchod", "behnchod", "bhen ke lode",
    "bhosdike", "bhosadike", "bhosdi ke", "bhosdika",
    "gandu", "gaandu", "gandoo", "gaand mara",
    "harami", "haraami", "haramkhor", "haramzade",
    "randi", "randwa", "randi ke bachche",
    "kutte", "kamine kutte", "kutte ki aulad",
    "lauda", "lawde", "lode", "loda",
    "kamina", "kameena", "nalayak", "ullu ka pattha", "gadhe ki aulad", "tatti", "chakke", "bakland", "hijra",
]  # fmt: skip
OPENERS = ["abe", "oye", "tu", "tum", "ye", "wo", "streamer", "bhai", "sun", "arre", "", "", "teri", "saale"]
TAILS = [
    "kitna kharab khelta hai", "stream band kar", "uninstall kar de game", "tujhe kuch nahi aata",
    "nikal yahan se", "mat aana ab", "chhod de streaming", "dekh ke khel", "kaun sikhata hai tujhe ye sab",
    "log pagal hain jo tujhe dekhte hain", "teri aukat nahi hai", "tera chehra dekh ke ulti aati hai",
    "game bech de apna", "chup ho ja", "bahut bada hai tu", "aaj ka match bhi dubaya",
]  # fmt: skip
LATN_PATTERNS = [
    "{op} {s} {tail}",
    "tu {s} hai {tail}",
    "{s} ki tarah khel raha hai",
    "{s} streamer",
    "sab keh rahe hain ye {s} hai",
    "{s}!!!",
    "kya {s} gameplay hai",
    "teri to {s}",
    "ja be {s}",
    "{op} {s} {s2}",
    "{tail} {s}",
    "kaisa {s} banda hai ye",
    "{s} {tail} 😡",
]
ABBREV = ["mc", "bc", "bsdk", "mkc", "bkl", "mcbc", "bhk", "bkc"]
ABBREV_PATTERNS = [
    "tu {a} hai {tail}", "{a} {tail}", "{a} kya khel raha hai", "ye {a} streamer hai", "{op} {a} {a2}",
    "{a} stream band kar", "sab {a} hain yahan", "{tail} {a}",
]  # fmt: skip
PROFANE_PRAISE = [
    "{s} kya shot mara bhai", "{s} ye to op hai", "{s} kya clutch tha", "arre {s} kitna zabardast khela",
]  # fmt: skip

# --- Devanagari ------------------------------------------------------------------------------------------------------
SLUR_DEVA = [
    "चूतिया", "चुतिया", "चूतिये", "मादरचोद", "मादर चोद", "बहनचोद", "भेनचोद", "भोसड़ी के", "भोसडीके", "भोसड़ीके",
    "गांडू", "गान्डू", "हरामी", "हरामखोर", "हरामज़ादे", "रंडी", "कमीना", "कमीने", "निकम्मा", "चक्के", "हिजड़ा",
    "लौड़े", "लंड", "बकलोल", "टट्टी", "कुत्ते", "कुत्ते की औलाद", "गधे की औलाद",
]  # fmt: skip
DEVA_PATTERNS = [
    "अबे {s} स्ट्रीम बंद कर", "तू बहुत बड़ा {s} है", "{s} कहीं का", "तेरी तो {s}", "ये {s} क्या खेल रहा है",
    "भाई {s} जैसा खेलता है", "{s}!!!", "साले {s} निकल यहाँ से", "{s} स्ट्रीमर", "कितना {s} है ये बंदा",
    "tu तो पूरा {s} है", "ये {s} है भाई", "सब कहते हैं तू {s} है",
]  # fmt: skip

# --- regional languages (Latin transliteration and native script) ----------------------------------------------------
REGIONAL = {
    "kn": {
        "latn": ["sule maga", "bosdi", "soole", "bosdi maga", "sule makkale", "thika"],
        "native": ["ಸೂಳೆ ಮಗ", "ಬೋಸಡಿ", "ಬೋಸಡಿ ಮಗನೇ", "ಸೂಳೆ ಮಕ್ಕಳೇ"],
        "pat_latn": ["ey {s} stream nillisu", "nee {s}", "{s} ge ontara gothilla", "{s} aata aadbeda"],
        "pat_native": ["ಏ {s} ಸ್ಟ್ರೀಮ್ ನಿಲ್ಲಿಸು", "ನೀನು {s}", "{s} ಆಟ ಆಡಬೇಡ", "ಹೋಗೋ {s}"],
    },
    "ta": {
        "latn": ["otha", "punda", "pundamavane", "thevdiya", "thevidiya mavane", "koothi", "ommala", "sunni"],
        "native": ["ஒத்தா", "புண்டை", "தேவடியா", "தேவடியா மவனே", "கூதி", "ஓம்மாள"],
        "pat_latn": ["dei {s} stream nirutthu", "nee oru {s}", "{s} maadhiri vilayaadura", "poda {s}"],
        "pat_native": ["டேய் {s} ஸ்ட்ரீம் நிறுத்து", "நீ ஒரு {s}", "{s} மாதிரி விளையாடுற", "போடா {s}"],
    },
    "te": {
        "latn": ["lanja", "lanjakodaka", "dengu", "pooka", "erripuka", "gudda", "lanjamunda"],
        "native": ["లంజ", "లంజకొడకా", "దెంగు", "పూకు", "ఎర్రిపూకా"],
        "pat_latn": ["ra {s} stream aapu", "nuvvu {s}", "{s} la aadutunnav", "po {s}"],
        "pat_native": ["రా {s} స్ట్రీమ్ ఆపు", "నువ్వు {s}", "{s} లా ఆడుతున్నావ్", "పో {s}"],
    },
    "bn": {
        "latn": ["magi", "khanki", "bokachoda", "haramjada", "kuttar baccha", "gandu", "chodna"],
        "native": ["মাগি", "খানকি", "বোকাচোদা", "হারামজাদা", "কুত্তার বাচ্চা", "গান্ডু"],
        "pat_latn": ["ei {s} stream bondho kor", "tui ekta {s}", "{s} er moto khelis", "jaa {s}"],
        "pat_native": ["এই {s} স্ট্রিম বন্ধ কর", "তুই একটা {s}", "{s} এর মতো খেলিস", "যা {s}"],
    },
}

ENGLISH = [
    "fuck you loser", "you are a fucking idiot streamer", "piece of shit gameplay", "stfu you dumbass",
    "what a bitch move trash player", "asshole uninstall the game", "kys loser", "go die noob",
    "you suck dick", "shut the fuck up already", "bastard cheater report him", "motherfucker camping again",
    "worthless piece of trash streamer", "dumb bitch cant even aim", "fucking retard team", "cunt play as usual",
    "get cancer streamer", "your mom should have aborted you", "stupid slut", "pathetic little prick",
]  # fmt: skip
ENGLISH_VARY = ["{t}", "{t} lol", "{t} 🤡", "bro {t}", "{t} fr", "seriously {t}", "{t}!!!", "ngl {t}"]
THREATS = [
    "tujhe maar dunga", "ghar aake maar dunga tujhe", "dekh lunga tujhe bahar mil", "tera account hack kar dunga",
    "tere ghar pe swat bhej dunga", "tujhe jaan se maar denge", "sar tod dunga tera", "tere parivar ko bhi nahi chhodunga",
    "bahar nikal tera game bajata hu", "तुझे मार दूंगा", "घर आके मारूंगा तुझे", "तेरा अकाउंट हैक कर दूंगा",
    "i will find where you live", "i know where you live streamer", "watch your back i will end you",
]  # fmt: skip
SEXUAL = [
    "nude bhej", "kapde utaar ke dikha", "sexy ho tum video call pe aao", "send nudes babe", "apni photo bina kapdo ki bhej",
    "raat ko akeli ho kya ghar aa jau", "tere boobs dikha", "chal hotel chalte hain", "तेरी नंगी फोटो भेज", "रात को मिलने आ जा अकेली",
]  # fmt: skip


def _uniq(rng: random.Random, n: int, make: Callable[[], Msg]) -> list[Msg]:
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


def _latn(rng: random.Random) -> Msg:
    s = pick(rng, SLUR_LATN)
    text = pick(rng, LATN_PATTERNS).format(
        op=pick(rng, OPENERS), s=s, s2=pick(rng, SLUR_LATN), tail=pick(rng, TAILS)
    )
    return Msg(" ".join(text.split()), "abuse", "slur_latn", "hinglish")


def _deva(rng: random.Random) -> Msg:
    text = pick(rng, DEVA_PATTERNS).format(s=pick(rng, SLUR_DEVA))
    return Msg(text, "abuse", "slur_deva", "deva")


def _regional(rng: random.Random) -> Msg:
    lang = pick(rng, list(REGIONAL))
    r = REGIONAL[lang]
    native = rng.random() < 0.5
    s = pick(rng, r["native" if native else "latn"])
    text = pick(rng, r["pat_native" if native else "pat_latn"]).format(s=s)
    return Msg(text, "abuse", "slur_regional", lang, tags=["native_script"] if native else [])


def _english(rng: random.Random) -> Msg:
    return Msg(pick(rng, ENGLISH_VARY).format(t=pick(rng, ENGLISH)), "abuse", "english", "en")


def _threat(rng: random.Random) -> Msg:
    t = pick(rng, THREATS)
    text = pick(rng, ["{t}", "{t} yaad rakhna", "bhai {t}", "{t} 😡", "sun {t}"]).format(t=t)
    lang = (
        "deva" if any("ऀ" <= c <= "ॿ" for c in t) else ("en" if t.startswith(("i ", "watch")) else "hinglish")
    )
    return Msg(text, "abuse", "threat", lang)


def _sexual(rng: random.Random) -> Msg:
    t = pick(rng, SEXUAL)
    lang = "deva" if any("ऀ" <= c <= "ॿ" for c in t) else "hinglish"
    return Msg(pick(rng, ["{t}", "{t} 😏", "hey {t}", "{t} plz"]).format(t=t), "abuse", "sexual", lang)


def _abbrev(rng: random.Random) -> Msg:
    text = pick(rng, ABBREV_PATTERNS).format(
        a=pick(rng, ABBREV), a2=pick(rng, ABBREV), op=pick(rng, OPENERS), tail=pick(rng, TAILS)
    )
    return Msg(" ".join(text.split()), "abuse", "abbrev", "hinglish")


def _obfuscated(rng: random.Random) -> Msg:
    base = pick(
        rng,
        [s for s in SLUR_LATN if " " not in s]
        + ["madarchod", "chutiya", "bhosdike", "behenchod", "gandu", "harami"],
    )
    word, how = obfuscate(base, rng)
    text = pick(rng, LATN_PATTERNS[:9]).format(op=pick(rng, OPENERS), s=word, s2=word, tail=pick(rng, TAILS))
    return Msg(" ".join(text.split()), "abuse", "obfuscated", "hinglish", tags=["obfuscated", how])


def _praise(rng: random.Random) -> Msg:
    s = pick(rng, ["bhosdike", "madarchod", "behenchod", "chutiya", "bc", "mc", "bsdk"])
    return Msg(
        pick(rng, PROFANE_PRAISE).format(s=s), "abuse", "profane_praise", "hinglish", tags=["non_directed"]
    )


MAKERS = {
    "slur_latn": _latn, "slur_deva": _deva, "slur_regional": _regional, "english": _english, "threat": _threat,
    "sexual": _sexual, "abbrev": _abbrev, "obfuscated": _obfuscated, "profane_praise": _praise,
}  # fmt: skip
ABUSE_COUNTS = {
    "slur_latn": 150, "slur_deva": 90, "slur_regional": 90, "english": 40, "threat": 30, "sexual": 20,
    "abbrev": 30, "obfuscated": 55, "profane_praise": 15,
}  # fmt: skip


def abuse(rng: random.Random, counts: dict[str, int] | None = None) -> list[Msg]:
    out: list[Msg] = []
    for kind, n in (counts or ABUSE_COUNTS).items():
        out += _uniq(rng, n, lambda k=kind: MAKERS[k](rng))
    return out


# --- benign messages that contain a borderline substring or word ------------------------------------------------------
HARD_NEG_ABUSE = [
    "kal chutti hai kya bhai", "chutti ke din stream karna", "chutney ki recipe dekhni hai", "mere kutte ne aaj naya trick seekha",
    "kutta bhaag gaya gate khula tha", "gaon mein gadhe ki sawari kari thi", "Gandhinagar mein rehta hu", "Gandhi jayanti pe off hai kya",
    "bhosari Pune mein hi hai na", "Randy Orton ki entry dekhi kya", "brand new phone liya aaj", "grandma ki recipe try ki",
    "Brandon ne aaj gift kiya", "mc donalds ka burger khaya", "MC ne shaadi mein bahut achha bola", "kal MCQ test hai padhai karni hai",
    "bc matlab before christ hota hai", "British Columbia mein BC kehte hain", "madar teresa ke baare mein padha", "madarsa ke bachche bhi dekhte hain",
    "dengue ho gaya hai 5 din se bukhar hai", "pundit ji ne muhurat nikala hai", "thikana batao kahan aana hai", "sab thik hai bhai",
    "Assam ki chai pi rahe hain", "class ke baad stream dekhunga", "pass ho gaya exam", "assassin's creed khelo na", "bass boosted headphones liye",
    "Essex mein cousin rehta hai", "Scunthorpe ke match ki baat ho rahi thi", "cocktail party hai aaj", "peacock ka video dekha", "Dickens ki book padh raha hu",
    "Penistone naam ka gaon hai UK mein", "Lund university Sweden mein hai", "Cockburn street kahan hai", "classic game hai ye", "hello everyone kaise ho",
    "shell shock 2 khelo kabhi", "Sussex county ka match tha", "Rancho ka dialogue yaad aaya", "Chutia Ranchi ka ek ilaka hai", "kutte ka video viral hai dekho", "mera pet dog bahut cute hai", "kutta bhi itna samajhdar nahi hota ye dekho 😂", "gadha aur ghoda dono dekhe zoo mein",
    "kamaal kar diya tune bhai",
    "tujhe dekh ke maza aa gaya killer hai tu", "enemy ko uda diya headshot se", "usko maar diya aakhri zone mein", "squad wipe kar dete hain aaj",
    "bhai tu to murder kar deta hai lobby mein", "kya bakwas sensitivity hai teri lekin chal jayega", "bekar gameplay tha aaj ka", "stream quality kharab hai thodi",
    "tu bohot slow khelta hai yaar", "noob team mili thi aaj", "ye game boring lag raha hai ab", "trash match tha ye honestly", "nikamma server hai ye game ka",
    "ghatiya update diya hai developers ne", "pagal ho gaye hain devs is patch mein", "paagal hu main is game ke liye", "dimag kharab ho gaya lag se",
    "behen ki shaadi hai kal off lunga", "meri behen bhi tujhe dekhti hai", "bhai behen dono fan hain tere",
    "chhod na bhai next round dekh", "lund ka naam kabhi suna hai Sweden wala",
    "Bhosale sir ka lecture tha aaj", "Randhawa ka naam suna hai punjabi singer", "Gandotra sir ne padhaya tha",
    "Kutty Padmini ki movie dekhi", "Thikri ka kaam chalta hai wahan", "haramain sharif ki video dekhi",
    "kamina nahi kaminey movie ka gaana chalao", "Kaminey movie ka dialogue maara", 
]  # fmt: skip
HARD_NEG_AMBIGUOUS = [  # honestly arguable: reported by kind, not gating
    "saala kya zabardast shot tha", "arre saale maza aa gaya",
    "chod do ye topic aage badho", "kya chutiyapa chal raha hai yahan aaj sab theek hai na",
    "saale tune to kill steal kar liya", "oye kamine ye to meri kill thi", "abe pagal hai kya itna op", "tu to bada harami nikla meri kill le gaya 😂",
    "bhosdi... sorry sorry typo", "bc kya khela hai aaj bhai", "bc ye lag kab jayega", "mc stream dekh raha hu kya khela", "saala bakchod hai tu mazak mein bol raha hu",
    "kutte kamine log hain ye enemy team", "gadha hai tu bhai mazak kar raha hu", "kya chutiyapa hai ye lag bhai server ka", "ye update to chutiyapa hai", "arre yaar chutiya bana diya tune",
    "bhai tu to harami hai 😂 meri kill le li", "oye gandu mera bhi hissa de loot mein", "kamina nikla ye to enemy hai", "bkl kya shot tha", "bsdk kya clutch tha bhai", "mkc kya khela",
]  # fmt: skip


def hard_negatives(rng: random.Random, n: int) -> list[Msg]:
    base = list(HARD_NEG_ABUSE)
    rng.shuffle(base)
    out: list[Msg] = []
    seen: set[str] = set()
    suffixes = ["", "", " 😂", " 🔥", " bhai", " yaar", " lol", " 👍"]
    for t in base * 3:
        text = (t + pick(rng, suffixes)).strip()
        if text not in seen:
            seen.add(text)
            out.append(Msg(text, "clean", "hard_neg_abuse", "hinglish", tags=["hard_negative"]))
        if len(out) >= n:
            break
    return out


def ambiguous(rng: random.Random) -> list[Msg]:
    return [
        Msg(t, "clean", "ambiguous_mild", "hinglish", gate=False, tags=["hard_negative", "ambiguous"])
        for t in HARD_NEG_AMBIGUOUS
    ]
