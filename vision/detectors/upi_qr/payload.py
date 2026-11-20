"""Is a decoded QR payload a payment? Returns a machine code, never the payload.

A QR is masked as ``UPI_QR`` when its text is

- a UPI deep link: ``upi://...`` or ``upi:...`` (any case; also found inside a URL query, or percent-encoded),
- a payment-app scheme link: ``bhim://``, ``tez://``, ``phonepe://``, ``paytmmp://``, ``gpay://`` ...
- a payment URL: contains ``pay?pa=`` or a ``pa=<vpa>@<handle>`` query parameter, or points at a known
  payment-link host (``razorpay.me``, ``rzp.io``, ``paytm.me``, ``phon.pe``, ``pay.google.com`` ...),
- a bare UPI ID with a known bank handle (``name@oksbi``).

Anything else (a website, Wi-Fi sign-in, a contact card, an e-mail) is not a payment. The text lives in this
function's frame only: callers get the code (``upi_scheme``, ``upi_app_link``, ``pay_link``, ``upi_id``) or ``None``.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

REASON_UPI_SCHEME = "upi_scheme"
REASON_APP_LINK = "upi_app_link"
REASON_PAY_LINK = "pay_link"
REASON_UPI_ID = "upi_id"

_APP_SCHEMES = (
    "bhim",
    "tez",
    "phonepe",
    "paytm",
    "paytmmp",
    "gpay",
    "googlepay",
    "amazonpay",
    "credpay",
    "mobikwik",
    "freecharge",
    "bharatpe",
    "slice",
    "navipay",
    "supermoney",
)
_APP_SCHEME_RE = re.compile(r"^(?:" + "|".join(_APP_SCHEMES) + r")(?:upi)?:", re.IGNORECASE)
_PA_PARAM_RE = re.compile(r"[?&;]pa=[a-z0-9._-]{2,64}(?:@|%40)[a-z][a-z0-9.]{1,40}", re.IGNORECASE)
_BARE_VPA_RE = re.compile(r"^[a-z0-9._-]{2,64}@([a-z][a-z0-9]{1,31})$", re.IGNORECASE)

# payment-link hosts (suffix match on the URL host)
_PAY_HOSTS = (
    "razorpay.me",
    "rzp.io",
    "pages.razorpay.com",
    "paytm.me",
    "p.paytm.me",
    "phon.pe",
    "pay.google.com",
    "gpay.app.goo.gl",
    "pay.cred.club",
    "bharatpe.in",
    "upayi.in",
    "imjo.in",
    "paypal.me",
    "buy.stripe.com",
    "checkout.stripe.com",
    "payu.in",
    "pmny.in",
    "cashfree.com",
    "pay.amazon.in",
    "mobikwik.com",
    "pine.pe",
)

# known bank handles (same list the text detector uses for UPI IDs in OCR text)
_KNOWN_HANDLES = frozenset(
    {
        "oksbi",
        "okhdfcbank",
        "okicici",
        "okaxis",
        "ybl",
        "ibl",
        "axl",
        "paytm",
        "apl",
        "upi",
        "sbi",
        "hdfcbank",
        "icici",
        "axisbank",
        "pnb",
        "ptyes",
        "pthdfc",
        "ptsbi",
        "ptaxis",
        "okbizaxis",
        "freecharge",
        "idfcbank",
        "kotak",
        "yesbank",
        "indus",
        "barodampay",
        "cnrb",
        "unionbank",
        "aubank",
        "jupiteraxis",
        "fbl",
        "airtel",
        "specimen",  # the SPECIMEN test handle (a payment test payload must be masked like a real one)
    }
)


def _variants(text: str) -> list[str]:
    """The text as read plus one and two rounds of percent-decoding (payment URLs get wrapped and encoded)."""
    out = [text]
    cur = text
    for _ in range(2):
        nxt = unquote(cur)
        if nxt == cur:
            break
        out.append(nxt)
        cur = nxt
    return out


def classify_payload(text: str | None) -> str | None:
    """Reason code if ``text`` is a payment payload, else ``None``. Case-insensitive."""
    if not text:
        return None
    for raw in _variants(text.strip()):
        s = raw.strip().lower()
        if s.startswith(("upi://", "upi:")):
            return REASON_UPI_SCHEME
        if "upi://" in s:
            return REASON_UPI_SCHEME
        if _APP_SCHEME_RE.match(s):
            return REASON_APP_LINK
        if "pay?pa=" in s or _PA_PARAM_RE.search(s):
            return REASON_PAY_LINK
        if "://" in s or s.startswith("www."):
            try:
                host = (urlsplit(s if "://" in s else "//" + s).hostname or "").lower()
            except ValueError:
                host = ""
            if host and any(host == h or host.endswith("." + h) for h in _PAY_HOSTS):
                return REASON_PAY_LINK
        m = _BARE_VPA_RE.match(s)
        if m and m.group(1) in _KNOWN_HANDLES:
            return REASON_UPI_ID
    return None
