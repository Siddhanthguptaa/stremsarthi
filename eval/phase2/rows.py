"""The Gate 2 table: one row per category with its dataset truth names, responsible detector and target.

Targets are the plan's initial targets (docs/PLAN.md section 5, G2). Changing one needs a docs/DECISIONS.md entry.
``kind`` says how a visible target frame is scored:

* ``masked``        success = the union of the masks covers >= ``cover_min`` of the target quad (default 0.90)
* ``face_masked``   same, for non-allowlisted faces only
* ``face_allowed``  success = an allowlisted face is left visible (masks cover < 0.10 of its quad)

``truth`` names are the ``category`` of the targets in the T2.1 ground truth (eval/data_gen/video/suite.py).
``mask_categories`` are the ``contracts.Category`` values a mask of that row is expected to carry (used for the
category-attributed recall and for the precision of each mask category).
"""

from __future__ import annotations

from dataclasses import dataclass

from contracts import Category

COVER_MIN = 0.90  # spec 5.5 (faces): >= 90 % of the quad covered by a mask
ALLOWED_MAX_COVER = 0.10  # spec 5.5: an allowlisted face frame is "visible" when < 10 % is masked
FLICKER_MAX = 0.02
GATE_FPS_MIN = 29.0
GATE_DELAY_P95_S = 5.0
GATE_DELAY_MAX_S = 5.5
GATE_CPU_CORES_MAX = 5.0
GATE_VRAM_CAP_MB = 3500.0  # dev profile cap (docs/PLAN.md 2.5); the demo profile may use 7000
GATE_DELAY_REGRESSION_S = 0.2
LANE_BUDGET_MS = {"fast": 25.0, "accurate": 450.0}  # docs/PLAN.md 2.5, CPU. Informational in the offline leg.


@dataclass(frozen=True)
class Row:
    key: str
    title: str
    truth: tuple[str, ...]
    detector: str
    target: float | None  # None = informational row (no gate)
    kind: str = "masked"
    mask_categories: tuple[Category, ...] = ()

    @property
    def gated(self) -> bool:
        return self.target is not None


ROWS: tuple[Row, ...] = (
    Row(
        "face_masked",
        "Faces masked (non-allowlisted)",
        ("face",),
        "faces",
        0.95,
        "face_masked",
        (Category.FACE,),
    ),
    Row(
        "face_allowed",
        "Allowlisted faces left visible",
        ("face",),
        "faces",
        0.95,
        "face_allowed",
        (Category.FACE,),
    ),
    Row("aadhaar", "Aadhaar card", ("aadhaar",), "cards", 0.95, mask_categories=(Category.AADHAAR,)),
    Row("pan", "PAN card", ("pan",), "cards", 0.95, mask_categories=(Category.PAN,)),
    Row("upi_qr", "UPI QR", ("upi_qr",), "upi_qr", 0.98, mask_categories=(Category.UPI_QR,)),
    Row(
        "signboard",
        "Signboard text",
        ("signboard_text",),
        "text",
        0.85,
        mask_categories=(Category.SIGNBOARD,),
    ),
    Row(
        "phone",
        "Phone number (on-screen)",
        ("text_phone",),
        "text",
        0.90,
        mask_categories=(Category.PHONE_NUMBER,),
    ),
    Row("pin", "PIN code (on-screen)", ("text_pin",), "text", 0.90, mask_categories=(Category.PIN_CODE,)),
    Row("upi_id", "UPI ID (on-screen)", ("text_upi_id",), "text", 0.90, mask_categories=(Category.UPI_ID,)),
    Row("email", "Email (on-screen)", ("text_email",), "text", 0.90, mask_categories=(Category.EMAIL,)),
    Row("otp_screen", "OTP screen", ("otp_banner",), "screens", 0.85, mask_categories=(Category.OTP_SCREEN,)),
    Row(
        "game_ids",
        "Game IDs (player ID, room ID, room password)",
        ("game_player_id", "game_room_id", "game_room_password"),
        "screens",
        0.90,
        mask_categories=(Category.GAME_ID, Category.ROOM_CREDENTIAL),
    ),
    Row("plate", "Number plate", ("plate",), "plates", 0.85, mask_categories=(Category.PLATE,)),
)

# informational rows (no target): sub-fields of the gated rows above
INFO_ROWS: tuple[Row, ...] = (
    Row("otp_code", "OTP code digits (inside the banner)", ("otp_code",), "screens", None, mask_categories=(Category.OTP_SCREEN,)),
    Row("game_player_id", "Game player ID", ("game_player_id",), "screens", None, mask_categories=(Category.GAME_ID,)),
    Row("game_room_id", "Game room ID", ("game_room_id",), "screens", None, mask_categories=(Category.ROOM_CREDENTIAL,)),
    Row("game_room_password", "Game room password", ("game_room_password",), "screens", None, mask_categories=(Category.ROOM_CREDENTIAL,)),
)  # fmt: skip

ALL_ROWS: tuple[Row, ...] = ROWS + INFO_ROWS
ROW_BY_KEY = {r.key: r for r in ALL_ROWS}

#: every detector id the gate expects the registry to supply (each row names one)
EXPECTED_DETECTORS: tuple[str, ...] = tuple(sorted({r.detector for r in ALL_ROWS}))

#: which truth categories a mask of a given contracts.Category is justified by (precision), plus the whole-object
#: context a mask-all rule may legitimately cover (signboard board, phone screen, room panel)
MASK_JUSTIFIED_BY: dict[Category, tuple[str, ...]] = {
    Category.FACE: ("face",),
    Category.AADHAAR: ("aadhaar",),
    Category.PAN: ("pan",),
    Category.UPI_QR: ("upi_qr",),
    Category.PLATE: ("plate",),
    Category.SIGNBOARD: ("signboard_text", "signboard_board"),
    Category.PHONE_NUMBER: ("text_phone",),
    Category.PIN_CODE: ("text_pin",),
    Category.UPI_ID: ("text_upi_id",),
    Category.EMAIL: ("text_email",),
    Category.OTP_SCREEN: ("otp_banner", "otp_code", "phone_screen"),
    Category.GAME_ID: ("game_player_id",),
    Category.ROOM_CREDENTIAL: ("game_room_id", "game_room_password", "room_panel"),
}

#: dataset buckets that must produce no mask at all (hard negatives): bucket -> rows whose detector it tests
NEGATIVE_BUCKETS: dict[str, str] = {
    "neg_card": "cards",
    "neg_plate": "plates",
    "neg_text": "text",
    "neg_screen": "screens",
    "neg_game": "screens",
    "faces_neg": "faces",
    "qr_negative": "upi_qr",
    "neg_scene": "text",
}

#: negative buckets read on a subset of the detector's mask categories only. ``neg_text`` holds text screens with
#: no PII, and the text detector's ``mask_all_text`` rule masks every signboard-like text box there BY DESIGN, so
#: its false masks are the PII categories only. ``neg_scene`` (natural scenes and text-free UI) is the signboard
#: negative: there every ``signboard`` mask is a false mask (informational, not gated).
NEGATIVE_COUNT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "neg_text": ("phone_number", "pin_code", "upi_id", "email", "pan"),
}
#: informational second reading of a negative bucket: masks of these categories only
NEGATIVE_INFO_CATEGORIES: dict[str, tuple[str, ...]] = {
    "neg_text": ("signboard",),
    "neg_scene": ("signboard",),
}

LEVELS = ("clean", "light", "heavy")
#: levels the recall rows are gated on (docs/DECISIONS.md D-48). Heavy is a stress level: reported, not gated.
GATE_LEVELS = ("clean", "light")
