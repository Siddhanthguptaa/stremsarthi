"""Plain clean chat: Hinglish, English, Devanagari and regional greetings, emotes, questions, support messages."""

from __future__ import annotations

import random

from .record import Msg, pick

GREET = [
    "hello", "hi everyone", "namaste", "good evening", "gm bhai", "hey streamer", "first", "yo", "kaise ho sab", "jai hind", "ram ram", "sat sri akal",
    "hii", "heyyy", "good morning chat", "late aaya sorry", "aa gaya main", "kya chal raha hai", "welcome back", "sab theek?",
]  # fmt: skip
CORE_LATN = [
    "kya gameplay hai", "ye shot to op tha", "clutch maar diya", "rank push kab karoge", "sensitivity kya hai teri", "aaj ka match mast tha",
    "zone kahan hai", "drop kahan karna hai", "wo sniper kahan se aaya", "gun ka naam kya hai", "ye skin kaunsi hai", "phone kaunsa hai tumhara",
    "fps kitna aa raha hai", "audio thoda low hai", "sound nahi aa raha", "camera ka angle sahi hai", "next match kab hai", "kal stream kitne baje hai",
    "squad mein kaun kaun hai", "tournament ke liye taiyar ho", "ek number khel raha hai", "bhai headshot kaise lagate ho", "recoil control seekhna hai",
    "lobby mein kitne players hain", "aaj ka target kya hai", "thoda sa zoom karo screen", "bhai config share karo", "gyroscope on hai kya",
    "kya sensitivity settings hain", "ye map mujhe pasand hai", "stream bahut smooth hai", "internet fast hai kya tumhara", "controller use karte ho kya",
    "full squad aa gaya", "revive kar do mujhe", "enemy peeche se aa raha hai", "loot kar lo pehle", "bhai medkit de do", "airdrop kahan gira",
    "legendary hai tu bhai", "kamaal ka gameplay hai", "mast khela aaj", "maza aa gaya sach mein", "bahut badhiya stream hai", "love from Bihar", "love from Mumbai",
    "mere liye shoutout kar do", "pehli baar dekh raha hu mast hai", "subscribe kar diya maine", "notification on kar diya", "like thok diya", "chat mein sab achhe hain",
]  # fmt: skip
CORE_DEVA = [
    "बहुत बढ़िया खेला भाई", "आज का स्ट्रीम मस्त था", "गेमप्ले कमाल का है", "अगला मैच कब है", "आवाज़ थोड़ी कम आ रही है", "सेंसिटिविटी कितनी रखते हो", "ये शॉट तो ज़बरदस्त था",
    "सब लोग कैसे हैं", "नमस्ते दोस्तों", "आज रैंक पुश करेंगे क्या", "कल कितने बजे आओगे", "बहुत मज़ा आया आज", "लव फ्रॉम दिल्ली", "शाबाश भाई ऐसे ही खेलते रहो", "कैमरा ठीक है क्या",
    "गन का नाम क्या है", "ये मैप बहुत अच्छा है", "स्ट्रीम बहुत स्मूद है", "सबको राम राम", "जय हिंद साथियों",
]  # fmt: skip
CORE_EN = [
    "nice play", "that was insane", "gg well played", "what a clutch", "love the stream", "can you show your settings", "which phone are you using",
    "audio is a bit low", "great game today", "how long have you been playing", "first time here this is fun", "pog", "lets goooo", "that aim is crazy", "no way he missed that",
    "chat is wild today", "hello from Nepal", "hi from the UK", "can you play something chill next", "thanks for the stream",
]  # fmt: skip
REGIONAL = {
    "kn": [
        "namaskara ellarigu",
        "chennagide stream",
        "ivattu match super",
        "next match yavaga",
        "ಒಳ್ಳೆಯ ಆಟ ಅಣ್ಣ",
        "ನಮಸ್ಕಾರ ಎಲ್ಲರಿಗೂ",
        "ಸ್ಟ್ರೀಮ್ ತುಂಬಾ ಚೆನ್ನಾಗಿದೆ",
    ],
    "ta": [
        "vanakkam ellarukkum",
        "super ah irukku stream",
        "innaikku match semma",
        "next match eppo",
        "வணக்கம் எல்லாருக்கும்",
        "நல்லா விளையாடுறீங்க அண்ணா",
        "ஸ்ட்ரீம் செம்ம",
    ],
    "te": [
        "namaskaram andariki",
        "stream chala bagundi",
        "ee roju match super",
        "next match eppudu",
        "నమస్కారం అందరికీ",
        "చాలా బాగా ఆడారు అన్నా",
        "స్ట్రీమ్ సూపర్",
    ],
    "bn": [
        "nomoskar sobaike",
        "stream khub bhalo hocche",
        "aajker match darun",
        "porer match kokhon",
        "নমস্কার সবাইকে",
        "খুব ভালো খেললে দাদা",
        "স্ট্রিম দারুণ",
    ],
}
SUFFIX = ["", "", "", " 🔥", " 👍", " 😂", " ❤️", " 🙌", " bhai", " yaar", " lol", " 😍", "!!", " 💪", " 🎮"]
EMOTES = [
    "LUL",
    "Pog",
    "PogChamp",
    "KEKW",
    "GG",
    "W",
    "🔥🔥🔥",
    "❤️❤️",
    "😂😂😂",
    "OP",
    "F",
    "👏👏👏",
    "gg ez",
    "sheesh",
    "ez win",
]
SUPPORT = [
    "{n} rs donate kiya hai dekh lena",
    "gifted 5 subs aaj",
    "super chat bheja hai padh lo",
    "membership le li maine",
    "bits cheer kiye 100",
]


def clean_plain(rng: random.Random, n: int) -> list[Msg]:
    out: list[Msg] = []
    seen: set[str] = set()
    tries = 0
    while len(out) < n and tries < n * 80:
        tries += 1
        r = rng.random()
        if r < 0.40:
            t, lang = (
                f"{pick(rng, GREET)} {pick(rng, CORE_LATN)}" if rng.random() < 0.4 else pick(rng, CORE_LATN),
                "hinglish",
            )
        elif r < 0.52:
            t, lang = pick(rng, CORE_DEVA), "deva"
        elif r < 0.68:
            t, lang = pick(rng, CORE_EN), "en"
        elif r < 0.80:
            lg = pick(rng, list(REGIONAL))
            t, lang = pick(rng, REGIONAL[lg]), lg
        elif r < 0.90:
            t, lang = pick(rng, EMOTES) + (" " + pick(rng, EMOTES) if rng.random() < 0.4 else ""), "en"
        elif r < 0.95:
            t, lang = pick(rng, SUPPORT).format(n=rng.choice([10, 20, 50, 100, 200])), "hinglish"
        else:
            t, lang = pick(rng, GREET), "hinglish"
        t = (t + pick(rng, SUFFIX)).strip()
        if t not in seen:
            seen.add(t)
            out.append(Msg(t, "clean", "plain", lang))
    return out
