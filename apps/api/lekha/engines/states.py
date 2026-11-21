"""Indian GST state codes (first two digits of a GSTIN) and union-territory status."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class State:
    code: str
    name: str
    # Union territories without a legislature levy UTGST instead of SGST.
    utgst: bool = False


_STATES = [
    State("01", "Jammu and Kashmir"),
    State("02", "Himachal Pradesh"),
    State("03", "Punjab"),
    State("04", "Chandigarh", utgst=True),
    State("05", "Uttarakhand"),
    State("06", "Haryana"),
    State("07", "Delhi"),
    State("08", "Rajasthan"),
    State("09", "Uttar Pradesh"),
    State("10", "Bihar"),
    State("11", "Sikkim"),
    State("12", "Arunachal Pradesh"),
    State("13", "Nagaland"),
    State("14", "Manipur"),
    State("15", "Mizoram"),
    State("16", "Tripura"),
    State("17", "Meghalaya"),
    State("18", "Assam"),
    State("19", "West Bengal"),
    State("20", "Jharkhand"),
    State("21", "Odisha"),
    State("22", "Chhattisgarh"),
    State("23", "Madhya Pradesh"),
    State("24", "Gujarat"),
    State("25", "Daman and Diu", utgst=True),
    State("26", "Dadra and Nagar Haveli and Daman and Diu", utgst=True),
    State("27", "Maharashtra"),
    State("28", "Andhra Pradesh (old)"),
    State("29", "Karnataka"),
    State("30", "Goa"),
    State("31", "Lakshadweep", utgst=True),
    State("32", "Kerala"),
    State("33", "Tamil Nadu"),
    State("34", "Puducherry"),
    State("35", "Andaman and Nicobar Islands", utgst=True),
    State("36", "Telangana"),
    State("37", "Andhra Pradesh"),
    State("38", "Ladakh", utgst=True),
    State("97", "Other Territory", utgst=True),
    State("99", "Centre Jurisdiction"),
]

STATES: dict[str, State] = {s.code: s for s in _STATES}

_NAME_INDEX = {s.name.lower(): s.code for s in _STATES}
_NAME_INDEX.update(
    {
        "ap": "37",
        "andhra pradesh": "37",
        "ts": "36",
        "tg": "36",
        "tn": "33",
        "ka": "29",
        "mh": "27",
        "dl": "07",
        "new delhi": "07",
        "j&k": "01",
        "jammu & kashmir": "01",
        "pondicherry": "34",
        "orissa": "21",
        "up": "09",
        "mp": "23",
        "wb": "19",
    }
)


def is_valid_state_code(code: str | None) -> bool:
    return bool(code) and code in STATES


def state_name(code: str | None) -> str | None:
    s = STATES.get(code or "")
    return s.name if s else None


def parse_place_of_supply(value: str | None) -> str | None:
    """Accepts '36', '36-Telangana', 'Telangana', 'TS' and returns the 2-digit code."""
    if not value:
        return None
    v = value.strip()
    if len(v) >= 2 and v[:2].isdigit():
        code = v[:2]
        return code if code in STATES else None
    return _NAME_INDEX.get(v.lower().replace(".", "").strip())
