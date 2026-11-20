"""E-mail addresses in OCR text. A dotted domain with an alphabetic TLD is required (else it may be a UPI ID)."""

from __future__ import annotations

import re
from dataclasses import dataclass

EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
)

CONF_EMAIL = 0.9


@dataclass(frozen=True, slots=True)
class EmailHit:
    start: int
    end: int
    confidence: float


def find_emails(text: str) -> list[EmailHit]:
    return [EmailHit(m.start(), m.end(), CONF_EMAIL) for m in EMAIL_RE.finditer(text)]


__all__ = ["CONF_EMAIL", "EMAIL_RE", "EmailHit", "find_emails"]
