"""Pure-text pattern helpers shared by the vision detectors (no models, no I/O)."""

from .aadhaar import (
    AadhaarHit,
    digit_run_hits,
    find_aadhaar,
    find_masked_aadhaar_hint,
    is_aadhaar_number,
)
from .email import EMAIL_RE, EmailHit, find_emails
from .pan import PAN_RE, PanHit, find_pan, repair_pan
from .phone import PHONE_RE, PhoneHit, find_phones
from .pin import PIN_RE, PinHit, find_pins, has_pin_cue, is_postal_circle
from .upi import KNOWN_HANDLES, UPI_RE, UpiHit, find_upi_ids
from .verhoeff import check_digit, checksum, is_valid

__all__ = [
    "EMAIL_RE",
    "KNOWN_HANDLES",
    "PAN_RE",
    "PHONE_RE",
    "PIN_RE",
    "UPI_RE",
    "AadhaarHit",
    "EmailHit",
    "PanHit",
    "PhoneHit",
    "PinHit",
    "UpiHit",
    "check_digit",
    "checksum",
    "digit_run_hits",
    "find_aadhaar",
    "find_emails",
    "find_masked_aadhaar_hint",
    "find_pan",
    "find_phones",
    "find_pins",
    "find_upi_ids",
    "has_pin_cue",
    "is_postal_circle",
    "is_aadhaar_number",
    "is_valid",
    "repair_pan",
]
