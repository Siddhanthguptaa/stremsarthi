"""Round 2 generalisation tests (own phrasings, written for this file, none taken from `eval/`).

They exercise STRUCTURES, not memorised lines: threat and harassment structures (verb + object, body part + break),
identity-based abuse, indirect doxxing requests, UPI-ID obfuscation, QR lures, Telugu and Kannada in Latin and native
script, and the false-positive kinds (quoted or reported abuse, friendly banter, moderators talking about scams,
legitimate UPI talk). Policy for each kind is in `chat/classify/README.md`.
"""

from __future__ import annotations

import pytest

from chat.classify import ChatClassifier, detect_scam, normalize
from chat.classify.doxxing import DoxxingDetector, _edit1, indirect_request
from chat.classify.links import refang
from chat.classify.llm import LlmError
from chat.classify.rules import default_matcher
from chat.classify.suspect import detect_suspects
from chat.testing.fake_llm import FakeLlm, ok, reply
from contracts import Category, ChatAction, ChatMessage
from contracts.config import ChatConfig, ClassifyConfig, StreamerPrivateConfig


def rules(text: str):
    return default_matcher().match(normalize(text))


def hard_abuse(text: str) -> bool:
    """Flagged by the lexicon and not softened (reported speech, banter)."""
    rr = rules(text)
    return rr.matched and not rr.soft


def msg(text: str, author_id: str = "viewer1", **kw) -> ChatMessage:
    return ChatMessage("twitch", "r2", "chan", author_id, "viewer", text, 1_700_000_000.0, **kw)


def fast(text: str, cfg: ChatConfig | None = None):
    return ChatClassifier(cfg).classify_fast(msg(text)).verdict


def upi_ids(text: str) -> bool:
    n = normalize(text)
    return any(
        c.category is Category.CHAT_FAKE_UPI and c.score >= 0.9
        for c in detect_scam(n.link_text, fanged=n.fanged)
    )


# --- threats: verb + object structures -------------------------------------------------------------------------

THREATS = [
    "kal milna zara, haddi pasli ek kar dunga",
    "bahar nikal tu, goli thok dunga",
    "tera encounter karwa dunga dekh lena",
    "tere ghar aake pel dunga",
    "stream band kar warna tujhe dekh lunga",
    "teri family ko bhi nahi chhodenge",
    "mere bande tujhe utha lenge",
    "tujhe zinda nahi chhodunga",
    "ek baar mil to sahi, muh tod dunga",
    "jaan le lunga teri",
    "tere tukde tukde kar dunga",
    "tera accident karwa dunga",
    "tu mar ja yaar",
    "tujhe to main chhodunga nahi",
    "i'm gonna find you and hurt you",
    "watch your back, we are coming for you",
    "i will stab you if i see you irl",
    "next time i see you i'll break your legs",
    "somebody should shoot this streamer",
    "i know your address and where you live",
    "सबक सिखा दूंगा तुझे",
    "तुझे जिंदा नहीं छोड़ूंगा",
    "तेरे हाथ पैर तोड़ दूंगा",
    "तू मर जा",
    "ninnu champestha ra",
    "nuvvu ekkada unna champestanu",
    "ninna kollthini mele",
    "hodithini ninna",
    "unna kolla poren da",
    "adichu kolluven unna",
    "నిన్ను చంపేస్తా",
    "ನಿನ್ನನ್ನು ಕೊಲ್ಲುತ್ತೇನೆ",
    "உன்ன கொல்லுவேன்",
    "tui na thakle mere felbo",
    "kill urself please",
    "go jump off a bridge",
]


@pytest.mark.parametrize("text", THREATS)
def test_threat_structures_are_flagged(text: str) -> None:
    assert hard_abuse(text), text


GAME_TALK = [
    "i will destroy you in the next round gg",
    "next round i'll kill you in the 1v1 lol",
    "aaj tujhe clutch mein maar dunga bhai round jeet ke",
    "you are done in this match bro",
    "kill kar dunga tujhe zone me squad wipe",
]


@pytest.mark.parametrize("text", GAME_TALK)
def test_game_talk_is_not_a_threat(text: str) -> None:
    assert not hard_abuse(text), text


# --- sexual harassment -----------------------------------------------------------------------------------------

SEXUAL = [
    "raat ko akeli aa jana mere ghar",
    "kitne mein chalegi ek raat ke liye",
    "tu to ekdum maal hai yaar",
    "item lag rahi hai aaj",
    "show me your body on cam",
    "zoom in on her chest please",
    "kapde utaar ke dikha na",
    "mere saath hotel chalegi kya",
    "wanna sleep with you",
    "ek raat ka rate kya hai tera",
    "nude video bhej de",
    "bed pe aa jao ab",
    "rape kar dunga tujhe",
    "teri to rape honi chahiye",
    "gang rape deserve karti hai",
    "मेरे साथ सोएगी क्या",
    "कितने में चलेगी तू",
    "कपड़े उतारो ना",
    "रेप कर दूंगा",
]


@pytest.mark.parametrize("text", SEXUAL)
def test_sexual_harassment_structures_are_flagged(text: str) -> None:
    assert hard_abuse(text), text


SEXUAL_OK = [
    "rape case ki khabar dekhi kya news pe",
    "balatkar ke khilaf law aana chahiye",
    "item shop me kya aaya aaj",
    "bhai hot sauce kaisi hai",
    "kitne mein mila ye headphone",
    "raat ko stream kitne baje hai",
    "nice body kit on that car",
    "lagta hai maal bahut aaya hai warehouse me",
]


@pytest.mark.parametrize("text", SEXUAL_OK)
def test_sexual_words_in_ordinary_use_are_not_flagged(text: str) -> None:
    assert not hard_abuse(text), text


# --- identity-based abuse ---------------------------------------------------------------------------------------

IDENTITY = [
    "sab katue aise hi hote hain",
    "ye mulle log kabhi nahi sudhrenge",
    "all muslims are terrorists",
    "sab chamar log aise hi hote hain gande",
    "tu bhangi hai kya",
    "go back to pakistan",
    "pakistan chale jao tum log",
    "dirty biharis everywhere",
    "ye sab madrasi log jahil hote hain",
    "ladkiyan gaming ke liye bani hi nahi hain",
    "women belong in the kitchen not in streams",
    "go back to the kitchen",
    "tu chakka hai",
    "tu gay hai kya, disgusting",
    "gay log disgusting hote hain",
    "teri jaat kya hai pehle ye bata",
    "neech jaat ke log",
    "chinki jaisi shakal hai teri",
    "pajeet streamer",
    "ye sab kafir log",
]


@pytest.mark.parametrize("text", IDENTITY)
def test_identity_abuse_is_flagged(text: str) -> None:
    assert hard_abuse(text), text


IDENTITY_OK = [
    "muslim friends ke saath eid manayi",
    "hindu festival aa raha hai diwali ki taiyari",
    "sikh community ne langar lagaya",
    "bihari litti chokha khaya aaj",
    "madrasi style dosa banaya",
    "dalit literature padh raha hu",
    "gay marriage ka verdict aaya hai",
    "women's cricket ka match dekha kya",
    "girls team ne tournament jeeta",
    "all muslims are not terrorists, stop spreading hate",
    "jaat ka matlab kya hota hai",
    "chamar surname ka itihas padha",
]


@pytest.mark.parametrize("text", IDENTITY_OK)
def test_group_names_and_discussion_are_not_flagged(text: str) -> None:
    assert not hard_abuse(text), text


# --- regional languages: Telugu, Kannada (Latin and native), Tamil, Bengali -------------------------------------

REGIONAL = [
    "nee yavva dengutha",
    "lanjakoduka em chestunnav",
    "erripuka gadu",
    "yedava nuvvu",
    "boku modda",
    "badkhow gadu",
    "నీ యమ్మ దెంగుతా",
    "వెధవ ఎక్కడ ఉన్నావ్",
    "బాడ్కో",
    "bolimaga yenu madtha idiya",
    "soole makkale nodri",
    "bosudike ninge",
    "halkat naayi maga",
    "ಸೂಳೆಮಗನೆ ಏನು ಮಾಡ್ತಿದ್ದೀಯ",
    "ಬೋಳಿಮಗನೆ",
    "ಮುಂಡೆ ಮಗ",
    "oombu da loosu",
    "kenapayale ketta",
    "thaayoli",
    "சனியன் பிடிச்சவன்",
    "shuorer bachcha ekta",
    "bhoda tui",
]


@pytest.mark.parametrize("text", REGIONAL)
def test_telugu_kannada_tamil_bengali_abuse(text: str) -> None:
    assert hard_abuse(text), text


REGIONAL_OK = [
    "nayi video kab aayegi bhai",
    "tika lagwa liya kal",
    "sunni deol ki movie dekhi",
    "nee video chala bagundi",
    "ninna stream chennagide",
    "nuvvu chala baga aadav",
    "unga video super",
    "pukka plan bana liya aaj",
    "munde ne goal kiya punjabi match me",
]


@pytest.mark.parametrize("text", REGIONAL_OK)
def test_ordinary_regional_chat_is_not_flagged(text: str) -> None:
    assert not hard_abuse(text), text


# --- quoted, reported, discussed abuse ---------------------------------------------------------------------------

REPORTED = [
    "usne mujhe chutiya bola, ban karo usko",
    "someone called me madarchod in dm lol",
    'he said "tu gandu hai" and left the stream',
    'kal ek banda bol raha tha ki "bhenchod tu band kar"',
    "mods please ban the guy who wrote mc in chat",
    "report kar diya usko jisne mujhe kutta bola",
    "the comment said go kill yourself and i reported it",
    "gaali mat do bhai, chutiya mat bolo",
    "don't call people bhosdike here",
    "kisi ko madarchod bolna acchi baat nahi hai",
    'usne kaha "teri maa ki" aur chala gaya',
    "yt comments me log randi likh dete hain, disgusting",
    "mujhe kisi ne harami bola aaj",
    "the word chutiya is a slur, please dont use it",
]


@pytest.mark.parametrize("text", REPORTED)
def test_reported_or_quoted_abuse_is_not_a_hard_flag(text: str) -> None:
    v = fast(text)
    assert v.action is ChatAction.ALLOW, (text, v.reason)


REPORTED_BUT_DIRECT = [
    "tu chutiya hai",
    "tera gameplay trash hai bhosdike",
    "bhenchod stream band kar",
    "madarchod tu hi hai jisne mujhe haraya",
]


@pytest.mark.parametrize("text", REPORTED_BUT_DIRECT)
def test_direct_abuse_is_still_flagged(text: str) -> None:
    assert fast(text).action is not ChatAction.ALLOW, text


# --- friendly banter ---------------------------------------------------------------------------------------------

BANTER = [
    "abe saale kya khela hai bhai 😂",
    "saale itna op player hai yaar",
    "arre saale bhai ne to kamaal kar diya",
    "oye kamine ye to meri kill thi lol",
    "abey gadhe bhai gg",
    "kutte ne kya clutch maara yaar op",
    "chal harami aaj to tu jeet gaya lol bhai",
    "tu pagal hai kya bhai itna accha khelta hai",
]


@pytest.mark.parametrize("text", BANTER)
def test_friendly_banter_is_allowed(text: str) -> None:
    assert fast(text).action is ChatAction.ALLOW, text


PROFANE_PRAISE = [
    "bol bhai bc mast stream",
    "kya baat hai bc maza aa gaya",
    "bc kya headshot tha lol",
]


@pytest.mark.parametrize("text", ["bsdk kya clutch tha", "chutiya kya shot mara bhai"])
def test_strong_profanity_used_as_praise_is_still_flagged(text: str) -> None:
    assert fast(text).action is not ChatAction.ALLOW, text


@pytest.mark.parametrize("text", PROFANE_PRAISE)
def test_profanity_used_as_praise_is_still_held(text: str) -> None:
    # policy: real profanity (bc, bsdk, chutiya ...) is held for a moderator even when friendly; only jocular mild words
    # (saale, kamine, gadhe ...) in a friendly frame are allowed. The LLM stage can overrule the hold.
    assert fast(text).action is ChatAction.HOLD_FOR_MOD, text


BANTER_BUT_NOT_FRIENDLY = [
    "saale tu bekaar hai nikal yahan se",
    "bc tera gameplay worst hai",
    "kutte tu stream band kar",
    "bhai teri maa ki aankh lol",
    "chutiya hai tu bilkul",
]


@pytest.mark.parametrize("text", BANTER_BUT_NOT_FRIENDLY)
def test_hostile_use_of_the_same_words_is_not_banter(text: str) -> None:
    assert fast(text).action is not ChatAction.ALLOW, text


# --- fake UPI: obfuscated IDs and QR lures -----------------------------------------------------------------------

UPI_OBFUSCATED = [
    "send 50 to shop4567 at ybl",
    "shop4567 [at] okaxis pe bhejo",
    "refund ke liye ramesh.k at oksbi pe pay karo",
    "pay now: sunita_77 ät paytm",
    "donate here  charity99 (at) okicici",
    "paise bhejo rahul-shop @ ybl",
    "gift01 at the rate upi ₹20 bhej do",
    "send to abc123 {at} axl",
    "amit.verma99 AT ibl please send money",
    "pay ramesh at ybl",
    "bhejo shop 4567@ ybl pe",
    "my upi is lucky.draw77[at]okhdfcbank claim now",
    "name[at]okaxis",
]


@pytest.mark.parametrize("text", UPI_OBFUSCATED)
def test_obfuscated_upi_ids_are_hard_flagged(text: str) -> None:
    assert upi_ids(text), text


NOT_UPI = [
    "meet me at paytm office tomorrow",
    "lets talk at ybl later",
    "mail me at rahul@gmail.com",
    "i am at home right now",
    "stream at night aaj",
    "pay at counter",
    "he is good at upi payments",
    "bhai upi se pay karna hai kaise karu",
]


@pytest.mark.parametrize("text", NOT_UPI)
def test_ordinary_at_is_not_a_upi_id(text: str) -> None:
    assert not upi_ids(text), text


QR_LURE = [
    "scan karo aur paise paao",
    "scan this to receive money",
    "scan the qr code to get 500 rupees",
    "ye qr scan karo reward milega",
    "scan karke ₹100 cashback le lo",
    "qr code scan kar do refund milega",
    "scan this code and claim your prize",
]


@pytest.mark.parametrize("text", QR_LURE)
def test_qr_lures_are_fake_upi(text: str) -> None:
    assert upi_ids(text), text


QR_OK = [
    "qr code kaise scan karte hain paytm me?",
    "how do i scan a qr code",
    "qr scan mat karna fraud hota hai",
    "bhai scan nahi ho raha ye document",
    "ye barcode scanner kaunsa hai",
]


@pytest.mark.parametrize("text", QR_OK)
def test_questions_and_warnings_about_scanning_are_not_lures(text: str) -> None:
    assert not upi_ids(text), text


# --- moderators and viewers talking about scams ----------------------------------------------------------------

SCAM_TALK = [
    "mods please ban anyone posting upi ids like abc@ybl, it's a scam",
    "reminder: streamer will never ask for money. don't send anything to any upi id",
    "scam alert!! koi bhi bit.ly link pe click mat karo, free diamond fake hai",
    "beware of fake giveaways, they say claim reward at xyz-claim.top, do not click",
    "yeh jo upi id daal raha hai wo fraud hai, report karo",
    "someone posted a phishing link above, mods delete it",
    "free fire diamond wale scam se bacho, link mat kholo",
    "banned the guy who spammed upi://pay?pa=x@ybl, report these bots",
    "dhyan rakho, koi paise maange to mat bhejna upi pe",
    "hum kabhi upi id nahi maangte, scam se bacho",
    "qr scan mat karna fraud hota hai, mods ban karenge",
]


@pytest.mark.parametrize("text", SCAM_TALK)
def test_scam_talk_is_not_a_hard_flag(text: str) -> None:
    v = fast(text)
    assert v.action is ChatAction.ALLOW, (text, v.reason)


SCAM_REAL = [
    "pay 10 rs to shop99@ybl and get 100 back",
    "claim your prize now bit.ly/win-big free diamonds",
    "100% legit not a scam send 50 to abc@oksbi",
    "upi://pay?pa=win@ybl&am=10",
]


@pytest.mark.parametrize("text", SCAM_REAL)
def test_real_scams_are_still_hard_flagged(text: str) -> None:
    v = fast(text)
    assert v.action in (ChatAction.DELETE, ChatAction.HIDE_FROM_STREAMER, ChatAction.TIMEOUT), (
        text,
        v.reason,
    )


# --- doxxing: indirect requests, typos, addresses ----------------------------------------------------------------

DOX_INDIRECT = [
    "who knows where he lives",
    "does anyone know where she stays",
    "koi bata sakta hai ye banda kahan rehta hai",
    "iska ghar kaha hai kisi ko pata hai kya",
    "streamer ka address kya hai",
    "where does he live bro",
    "anyone know his real name and school",
    "uska school kaunsa hai koi batao",
    "kisi ko uski location pata hai",
    "इसका घर कहाँ है बताओ",
    "find out where she works and dm me",
    "ye ladki kahan padhti hai pata karo",
]


@pytest.mark.parametrize("text", DOX_INDIRECT)
def test_indirect_doxxing_requests(text: str) -> None:
    assert indirect_request(normalize(text).text) or DoxxingDetector().detect(normalize(text)), text


DOX_INDIRECT_OK = [
    "where do you live bro which city",
    "bhai tum kahan rehte ho",
    "kahan hai wo stream shuru karo",
    "where does the game save files live",
    "mera ghar bahut door hai yaar",
    "his number of kills is crazy",
    "uska address bar me paste karo",
    "who knows where to find free fire diamonds",
    "iska location map pe dikhao game ka",
]


@pytest.mark.parametrize("text", DOX_INDIRECT_OK)
def test_ordinary_questions_are_not_doxxing_requests(text: str) -> None:
    assert not DoxxingDetector().detect(normalize(text)), text


def _private_cfg() -> ChatConfig:
    return ChatConfig(
        streamer_private=StreamerPrivateConfig(
            terms=["Rohan Mehta", "Gurugram", "Springfield Public School", "Mumbai"], phones=[]
        )
    )


DOX_TYPOS = [
    "rohan mehtaa ka ghar",
    "he lives in gurugam",
    "gurugramm wala hai ye",
    "springfeld public school me padhta hai",
    "springfield publicschool",
    "mumbaii se hai",
    "rohan meta",
]


@pytest.mark.parametrize("text", DOX_TYPOS)
def test_misspelt_private_terms_are_found(text: str) -> None:
    verdict = fast(text, _private_cfg())
    assert verdict.action is not ChatAction.ALLOW and Category.CHAT_DOXXING in verdict.categories, text


DOX_TYPOS_OK = [
    "rohit sharma ne century maari",
    "gurudwara chalo aaj",
    "mumbles ka gaana lagao",
    "mehr ka matlab kya hota hai",
]


@pytest.mark.parametrize("text", DOX_TYPOS_OK)
def test_similar_words_are_not_private_terms(text: str) -> None:
    assert fast(text, _private_cfg()).action is ChatAction.ALLOW, text


DOX_ADDRESS = [
    "H.No 45, Sector 12, Rohini",
    "flat 302 shanti apartments, mg road",
    "address is plot 17 lane 4 vijay nagar colony",
    "house no 22 gali 5 mohalla sadar",
    "मकान नंबर 45 सेक्टर 12 रोहिणी",
    "address: 12/B park street kolkata",
]


@pytest.mark.parametrize("text", DOX_ADDRESS)
def test_written_out_addresses_are_flagged(text: str) -> None:
    assert Category.CHAT_DOXXING in fast(text).categories, text


DOX_ADDRESS_OK = [
    "flat 50% off in the sale",
    "sector 5 map pe push karte hain",
    "plot twist at 17 min",
    "main street fighter khelunga aaj",
    "room 3 me clutch kiya",
]


@pytest.mark.parametrize("text", DOX_ADDRESS_OK)
def test_address_words_in_game_talk_are_not_flagged(text: str) -> None:
    assert Category.CHAT_DOXXING not in fast(text).categories, text


# --- cascade: the local fallback and the LLM route -----------------------------------------------------------------


def test_suspect_shapes_are_lenient_and_allowed_without_an_llm() -> None:
    text = "tu kisi kaam ka nahi hai yaar sach me"
    assert any(c.lenient for c in detect_suspects(normalize(text)))
    v = fast(text)
    assert v.action is ChatAction.ALLOW and v.reason.endswith("_allow")


def test_ordinary_chat_rarely_reaches_the_llm() -> None:
    chat = [
        "bhai aaj ka stream mast tha",
        "kab aayega next video",
        "gg wp bhai",
        "first lol",
        "kya khela hai bhai op",
        "love from kolkata",
        "can you play valorant next",
        "music konsa hai ye",
        "aaj match kab hai",
        "chat me sab ka dhanyawad",
        "bro rank push karte hain",
        "subscribe kar liya maine",
        "voice thoda low hai",
        "mic check please",
        "kal kitne baje live aaoge",
        "nice setup bro",
        "which sensitivity do you use",
        "ye game kaise download kare",
        "namaste sabko",
        "good morning everyone",
    ]
    hits = sum(bool(detect_suspects(normalize(t))) for t in chat)
    assert hits <= 2, hits


# --- lenient candidates and the LLM ----------------------------------------------------------------------------------


def _llm_cfg() -> ChatConfig:
    return ChatConfig(classify=ClassifyConfig(llm_flush_ms=5, llm_pending_max_s=1.0, llm_timeout_ms=200))


async def test_lenient_candidate_goes_to_the_llm_and_follows_its_answer() -> None:
    text = "tu kisi kaam ka nahi hai yaar sach me"
    flag = FakeLlm(
        default=lambda texts: reply([ok(i, ["abuse"], 0.95, "streamer") for i in range(len(texts))])
    )
    clf = ChatClassifier(_llm_cfg(), llm=flag)
    [prov] = await clf.classify([msg(text)])
    assert prov.action is ChatAction.HIDE_FROM_STREAMER and prov.reason == "pending_llm"
    [final] = await clf.classify_final([msg(text)])
    assert (
        final.stage == "llm"
        and final.action is not ChatAction.ALLOW
        and Category.CHAT_ABUSE in final.categories
    )
    await clf.aclose()
    clean = ChatClassifier(_llm_cfg(), llm=FakeLlm())
    [final] = await clean.classify_final([msg(text)])
    assert final.action is ChatAction.ALLOW and final.stage == "llm"
    await clean.aclose()


async def test_lenient_candidate_is_allowed_when_the_llm_is_unreachable() -> None:
    llm = FakeLlm(default=lambda texts: (_ for _ in ()).throw(LlmError("down")))
    clf = ChatClassifier(_llm_cfg(), llm=llm)
    [final] = await clf.classify_final([msg("tu kisi kaam ka nahi hai yaar sach me")])
    assert final.action is ChatAction.ALLOW and final.reason.endswith("_timeout_allow")
    # while a regular borderline message (a rules hit) is held, never allowed
    [held] = await clf.classify_final([msg("bc tera gameplay worst hai", author_id="other")])
    assert held.action is ChatAction.HOLD_FOR_MOD and held.reason == "llm_timeout_hold"
    await clf.aclose()


def test_spaced_and_obfuscated_ids_are_normalised() -> None:
    assert refang("pay on s h o p 1 2 @ y b l now") == "pay on shop12@ybl now"
    assert refang("send to shop4567 at ybl") == "send to shop4567@ybl"
    assert refang("meet me at paytm office") == "meet me at paytm office"
    assert refang("hey @streamer look") == "hey @streamer look"  # a mention is not an ID


def test_one_typo_matches_but_two_do_not() -> None:
    assert _edit1("gurugram", "gurugramm") and _edit1("gurugram", "gurugam") and _edit1("mumbai", "mumabi")
    assert not _edit1("gurugram", "gurgaon") and not _edit1("mumbai", "mumbles")
