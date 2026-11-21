"""Carrier phrases for the audio suite: cues before PII, filler chatter, number-bearing distractors, address parts.

Written for this eval set only (the wording is deliberately not the cue list of ``audio/pii``): "lexicon"
cues are the everyday phrasings a speaker would use, "paraphrase" cues carry the same intent in other words,
and "none" means the number is just said. ``"stt|tts"`` marks words whose recogniser form differs from what
the offline English TTS can say.
"""

from __future__ import annotations

import random

from .numbers import Tok


def toks(s: str) -> list[Tok]:
    out = []
    for w in s.split():
        a, _, b = w.partition("|")
        out.append(Tok(a, b or a))
    return out


# --- cues: kind -> lang -> list of phrases (lexicon-style) -------------------------------------------------------
_PHONE = {
    "hi_latn": [
        "mera number hai",
        "mera whatsapp number",
        "mobile number note karo",
        "call karna is number par",
    ],
    "hi_deva": ["मेरा|mera नंबर|number है|hai", "व्हाट्सएप|whatsapp नंबर|number", "कॉल|call करना|karna"],
    "en": ["my number is", "whatsapp me on", "call me on", "my phone number is"],
    "kn": ["ನನ್ನ|nanna ನಂಬರ್|number", "ಕಾಲ್|call ಮಾಡಿ|maadi"],
    "ta": ["என்|en நம்பர்|number", "கால்|call பண்ணுங்க|pannunga"],
    "te": ["నా|naa నంబర్|number", "కాల్|call చేయండి|cheyandi"],
    "bn": ["আমার|amar নাম্বার|number", "কল|call করো|koro"],
}
_OTP = {
    "hi_latn": ["otp hai", "otp aaya hai", "verification code hai"],
    "hi_deva": ["ओटीपी|otp है|hai", "कोड|code है|hai"],
    "en": ["the otp is", "verification code is", "my code is"],
    "kn": ["ಒಟಿಪಿ|otp ಇದು|idu", "ಕೋಡ್|code ಇದು|idu"],
    "ta": ["ஓடிபி|otp இது|idhu", "கோட்|code இது|idhu"],
    "te": ["ఓటిపి|otp ఇది|idi", "కోడ్|code ఇది|idi"],
    "bn": ["ওটিপি|otp এটা|eta", "কোড|code এটা|eta"],
}
_UPI = {
    "hi_latn": ["mera upi id hai", "gpay karo is id par", "phonepe karo"],
    "hi_deva": ["मेरा|mera यूपीआई|upi आईडी|id है|hai", "जीपे|gpay करो|karo"],
    "en": ["my upi id is", "send on gpay to", "pay me on"],
    "kn": ["ನನ್ನ|nanna upi id ಇದು|idu"],
    "ta": ["என்|en upi id இது|idhu"],
    "te": ["నా|naa upi id ఇది|idi"],
    "bn": ["আমার|amar upi id এটা|eta"],
}
_AADHAAR = {
    "hi_latn": ["mera aadhaar number hai", "aadhaar number note karo"],
    "hi_deva": ["मेरा|mera आधार|aadhaar नंबर|number है|hai"],
    "en": ["my aadhaar number is"],
    "kn": ["ನನ್ನ|nanna aadhaar ನಂಬರ್|number"],
    "ta": ["என்|en aadhaar நம்பர்|number"],
    "te": ["నా|naa aadhaar నంబర్|number"],
    "bn": ["আমার|amar aadhaar নাম্বার|number"],
}
_ADDRESS = {
    "hi_latn": ["mera address hai", "ghar ka pata hai", "address note karo"],
    "hi_deva": ["मेरा|mera पता|pata है|hai", "घर|ghar का|ka पता|pata"],
    "en": ["my address is", "i live at", "deliver it to"],
    "kn": ["ನನ್ನ|nanna address ಇದು|idu"],
    "ta": ["என்|en address இது|idhu"],
    "te": ["నా|naa address ఇది|idi"],
    "bn": ["আমার|amar address এটা|eta"],
}
_PARAPHRASE = {
    "phone": ["save this one quickly", "yahan message kar dena", "jaldi note kar lo", "sab log likh lo bhai"],
    "otp": ["abhi jo aaya wo hai", "sms mein ye dikha", "ye wala dalna hai"],
    "upi": ["yahan payment bhejo", "support karna ho toh", "donate karo yahan"],
    "aadhaar": ["id proof ye hai", "kyc ke liye ye hai"],
    "address": ["parcel yahan bhejo", "milne aana ho toh", "yahan aa jao"],
}
_CUES = {"phone": _PHONE, "otp": _OTP, "upi": _UPI, "aadhaar": _AADHAAR, "address": _ADDRESS}


def cue_tokens(kind: str, lang: str, mode: str, rng: random.Random) -> list[Tok]:
    if mode == "none":
        return []
    if mode == "paraphrase":
        return toks(rng.choice(_PARAPHRASE[kind]))
    return toks(rng.choice(_CUES[kind][lang]))


# --- filler chatter (non-PII) ------------------------------------------------------------------------------------
FILLER_LATIN = [
    "bhai aaj ka match bahut zabardast tha",
    "chalo ab next round shuru karte hain",
    "chat mein sabko hello bolna hai",
    "wo sniper wala spot dekha kya tumne",
    "aaj hum ranked push karenge sab milke",
    "thoda sa pani pi lu phir aata hu",
    "subscribe karna mat bhoolna dosto",
    "ye gun ka recoil bahut tough hai yaar",
    "kal ka stream thoda late hoga bhai",
    "mic check ek do sab theek se sun rahe ho",
    "welcome back everyone lets get started",
    "that was an insane clutch honestly",
    "thanks for the support today you guys are great",
    "we are going to try a new map now",
    "drop kahan karna hai tum log bolo",
    "haan bhai sab sahi chal raha hai",
    "aaj ka target hai conqueror tak pahunchna",
    "internet thoda slow hai isliye lag aa raha hai",
    "arre wo enemy peeche se aa gaya",
    "nice shot bro full health pe tha",
    "thoda aur wait karo loot kar lete hain",
    "mujhe lagta hai zone shrink hone wala hai",
    "bahut maza aa raha hai aaj ka session",
    "ek minute rukna chat padh leta hu",
]
FILLER_DEVA = [
    "भाई|bhai आज|aaj का|ka मैच|match बहुत|bahut बढ़िया|badhiya था|tha",
    "चलो|chalo अब|ab अगला|agla राउंड|round शुरू|shuru करते|karte हैं|hain",
    "सबको|sabko नमस्ते|namaste बोलना|bolna है|hai चैट|chat में|mein",
    "आज|aaj हम|hum रैंक|rank पुश|push करेंगे|karenge",
    "सब्सक्राइब|subscribe करना|karna मत|mat भूलना|bhoolna दोस्तों|dosto",
    "थोड़ा|thoda पानी|paani पी|pee लूं|loon फिर|phir आता|aata हूं|hoon",
    "ये|ye गन|gun का|ka रिकॉइल|recoil बहुत|bahut टफ|tough है|hai",
    "कल|kal का|ka स्ट्रीम|stream थोड़ा|thoda लेट|late होगा|hoga",
]
FILLER_REGIONAL = {
    "kn": [
        "ಇವತ್ತು|ivattu ಮ್ಯಾಚ್|match ಶುರು|shuru ಮಾಡೋಣ|maadona",
        "ಎಲ್ಲರಿಗೂ|ellarigu ನಮಸ್ಕಾರ|namaskara",
        "ಚಾಟ್|chat ನೋಡ್ತೀನಿ|nodteeni ಒಂದು|ondu ನಿಮಿಷ|nimisha",
    ],
    "ta": [
        "இன்னைக்கு|innaikku மேட்ச்|match ஆரம்பிக்கலாம்|aarambikkalam",
        "எல்லாருக்கும்|ellarukkum வணக்கம்|vanakkam",
        "சாட்|chat பாக்கறேன்|paakkaren ஒரு|oru நிமிஷம்|nimisham",
    ],
    "te": [
        "ఈరోజు|eeroju మ్యాచ్|match మొదలుపెడదాం|modalupedadam",
        "అందరికీ|andariki నమస్కారం|namaskaram",
        "చాట్|chat చూస్తా|choosta ఒక|oka నిమిషం|nimisham",
    ],
    "bn": [
        "আজ|aaj ম্যাচ|match শুরু|shuru করি|kori",
        "সবাইকে|sobaike নমস্কার|nomoskar",
        "চ্যাট|chat দেখছি|dekhchi এক|ek মিনিট|minute",
    ],
}
# --- number-bearing distractors (never PII): hard negatives for over-muting ---------------------------------------
DISTRACTORS_LATIN = [
    "score teen do hai abhi",
    "level paanch pe hu abhi",
    "dus minute baad aata hu",
    "match number saat hai aaj",
    "chaar kill ho gaye ek hi round mein",
    "teen sau rupaye ka skin hai",
    "ek do teen chalo start karte hain",
    "room mein paanch log hain abhi",
    "mera rank number ek hai server pe",
    "gun number teen nikalo jaldi",
    "i have two hundred subscribers now",
    "it is level nine already",
    "the squad has four players left",
    "sau percent sure hu main",
]
DISTRACTORS_DEVA = [
    "स्कोर|score तीन|teen दो|do है|hai अभी|abhi",
    "दस|das मिनट|minute बाद|baad आता|aata हूं|hoon",
    "चार|chaar किल|kill हो|ho गए|gaye",
]
# --- address parts (SPECIMEN: generic names only) -----------------------------------------------------------------
BUILDINGS = ["sunrise", "lotus", "green valley", "blue orchid", "specimen", "maple court", "silver oak"]
LANDMARKS = ["city mall", "metro station", "water tank", "bus stand", "post office", "old market"]
CITIES = ["pune", "indore", "mysuru", "chennai", "hyderabad", "kolkata", "jaipur", "nagpur"]
ADDRESS_WORDS = {
    "hi_latn": {"flat": "flat number", "sector": "sector", "near": "near", "pin": "pincode"},
    "hi_deva": {
        "flat": "फ्लैट|flat नंबर|number",
        "sector": "सेक्टर|sector",
        "near": "के|ke पास|paas",
        "pin": "पिनकोड|pincode",
    },
    "en": {"flat": "house number", "sector": "sector", "near": "near", "pin": "pin code"},
}
