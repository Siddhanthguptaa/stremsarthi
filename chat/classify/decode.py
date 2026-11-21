"""A cheap decoder pass for messages that hide their text in an encoding.

It only fires when a string LOOKS encoded and the decoded form is printable text:

* base64 and hex tokens (long, only the right alphabet, decode to mostly printable UTF-8 with letters in it);
* rot13 and reversed text: the plain message has no common words but the transformed one has, so ordinary chat is never
  touched.

Work is capped (a few tokens, bounded length, at most three decoded views). Decoded text is only returned to the caller
to classify; nothing here logs or stores text.
"""

from __future__ import annotations

import base64
import binascii
import codecs
import re
import unicodedata

_B64 = re.compile(r"^[A-Za-z0-9+/]{16,}={0,2}$")
_HEX = re.compile(r"^(?:[0-9a-fA-F]{2}){8,}$")
_WORD = re.compile(r"[a-z]+|[ऀ-ॿ]+")
_COMMON = frozenset(
    """hai hain ho hu tu tum tera teri tere ka ki ke ko me mein se ne aur ya to toh ye yeh wo woh kya nahi nhi bhai mera meri
    है का की के को तू तुम तेरा तेरी में से और यह वो नहीं क्या अबे बंद कर भाई मेरा
    the and you your ur is are was for of to in on it this that with pay send me my not what who where live address number
    free click link claim win win now account kyc loan upi bhejo karo kar de do abhi yaha""".split()
)
codecs.lookup("rot13")  # load the codec at import, not inside the first message
MAX_VIEWS = 3
MAX_CHARS = 2000


def _printable(raw: bytes) -> str | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text or len(text) > MAX_CHARS:
        return None
    ok = sum(c.isprintable() or c in "\n\t" for c in text)
    letters = sum(unicodedata.category(c)[0] in "LMN" for c in text)  # letters, vowel signs and digits
    if ok / len(text) < 0.97 or letters < 6 or letters / len(text) < 0.5:
        return None
    return text


def _common_count(text: str) -> int:
    return sum(w in _COMMON for w in _WORD.findall(text.lower()))


def decoded_views(text: str) -> list[str]:
    """Decoded readings of ``text`` worth classifying too (usually empty)."""
    if len(text) < 14:
        return []
    out: list[str] = []
    for tok in text.split()[:30]:
        if len(tok) < 16 or len(tok) > 4 * MAX_CHARS:
            continue
        dec: str | None = None
        if _HEX.match(tok):
            dec = _printable(bytes.fromhex(tok))
        elif _B64.match(tok) and len(tok) % 4 in (0, 2, 3):
            try:
                dec = _printable(base64.b64decode(tok + "=" * (-len(tok) % 4), validate=True))
            except (binascii.Error, ValueError):
                dec = None
        if dec:
            out.append(dec)
        if len(out) >= MAX_VIEWS:
            return out
    head = text[: 2 * MAX_CHARS]
    plain = _common_count(head)
    low = head[:80].lower()
    named = {"rot13": "rot13" in low or "rot-13" in low, "rev": "ulta" in low or "reverse" in low}
    rot, rev = codecs.encode(head, "rot13"), head[::-1]
    for key, view in (("rot13", rot), ("rev", rev)):
        n = _common_count(view)
        # the message names the encoding, or the transformed text has common words and the plain one has none
        if named[key] or (n >= 2 and n > 2 * plain):
            out.append(view)
        if len(out) >= MAX_VIEWS:
            break
    return out
