"""Caption test material: conjunct-heavy sentences per script (the raqm shaping test) and a spoken timeline for the
arrival-time test. Written for this eval only; the phone number in the timeline is a SPECIMEN-style synthetic one.

Shaping texts avoid depending on meaning: each string mixes ordinary words with conjuncts, vowel signs and reordering
cases that the basic layout renders wrongly (Devanagari conjuncts and short-i reordering, Tamil and Telugu and
Kannada consonant clusters and split vowels, Bengali conjuncts and split vowels).
"""

from __future__ import annotations

import random

SHAPING: dict[str, list[str]] = {
    "hi": ["क्षमा करें श्रीमान् ज्ञान और प्रश्न", "स्ट्रीमिंग शुरू करते हैं धन्यवाद दोस्तों", "विद्यार्थियों का सम्मेलन कार्यक्रम"],
    "kn": ["ನಮಸ್ಕಾರ ಸ್ನೇಹಿತರೆ ಇಂದಿನ ಪ್ರಸಾರಕ್ಕೆ ಸ್ವಾಗತ", "ಕ್ರೀಡೆ ಮತ್ತು ಸಂಗೀತ ಪ್ರಶ್ನೆ"],
    "ta": ["வணக்கம் நண்பர்களே இன்றைய நேரலைக்கு வரவேற்கிறோம்", "ஸ்ரீ க்ஷேத்ரம் பொத்தான் கொள்கை"],
    "te": ["నమస్కారం మిత్రులారా నేటి ప్రసారానికి స్వాగతం", "శ్రీ క్రీడలు ప్రశ్న స్నేహితులు"],
    "bn": ["নমস্কার বন্ধুরা আজকের সম্প্রচারে স্বাগতম", "ক্ষমা বিদ্যালয় শ্রদ্ধা সন্ত্রাস"],
}
SCRIPT_NAMES = {"hi": "Devanagari", "kn": "Kannada", "ta": "Tamil", "te": "Telugu", "bn": "Bengali"}

_SENTENCES = [
    "namaste doston aaj ke stream mein aap sabka swagat hai",
    "aaj hum ek naya game khelenge aur chat ke sawaal bhi lenge",
    "pehle thoda sa setup check kar lete hain phir shuru karenge",
    "agar awaaz theek hai toh chat mein ek message likh do",
    "ye map bahut bada hai isliye team ke saath rehna zaroori hai",
    "kal ka match humne jeeta tha aur aaj bhi koshish karenge",
]
_PHONE = "mera number hai nau aath saat chhe paanch chaar teen do ek shunya".split()


def timeline(seed: int, n: int = 24) -> dict:
    """Segments of speech as a recogniser would deliver them: words with exact times, the capture-to-transcript
    latency of each, and one segment with a spoken number that the audio module mutes (a mute span follows it)."""
    rng = random.Random(seed)
    t = 1.0
    segs: list[dict] = []
    for i in range(n):
        phone = i == 7
        words = (
            list(_PHONE) if phone else rng.choice(_SENTENCES).split()[: rng.randint(4, 6)]
        )  # short, as streaming STT delivers
        timed, c = [], t
        for w in words:
            d = rng.uniform(0.18, 0.30)
            timed.append([w, round(c, 3), round(c + d, 3)])
            c += d + 0.04
        segs.append(
            {
                "id": f"c{i:03d}",
                "pts_start": timed[0][1],
                "pts_end": timed[-1][2],
                "words": timed,
                "lang": "hi-Latn",
                "stt_latency_s": round(rng.uniform(0.6, 1.2), 3),
                "translate_latency_s": round(rng.uniform(0.15, 0.5), 3),
                "mute": [timed[3][1] - 0.02, timed[-1][2] + 0.05] if phone else None,
                "secret_words": [w for w, _, _ in timed[3:]] if phone else [],
            }
        )
        t = c + rng.uniform(0.4, 1.0)
    return {"delay_s": 4.0, "segments": segs}
