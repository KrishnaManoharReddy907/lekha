"""GSTIN parsing, checksum validation and typo repair.

A regular GSTIN is 15 characters:  SS PPPPPPPPPP E Z C
  SS  state code            PPPPPPPPPP  PAN of the business
  E   entity number (1-9, A-Z)          Z  default letter
  C   check character computed from the first 14 characters (Luhn mod-36 variant).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lekha.engines.states import STATES

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_VALUE = {c: i for i, c in enumerate(ALPHABET)}

REGULAR_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
# Special registrations (TDS/TCS deductors, UN bodies, non-resident OIDAR) use other layouts
# but still carry the same check character.
GENERIC_RE = re.compile(r"^\d{2}[0-9A-Z]{13}$")
PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")

PAN_ENTITY_TYPES = {
    "P": "Individual",
    "C": "Company",
    "F": "Firm / LLP",
    "H": "Hindu Undivided Family",
    "A": "Association of Persons",
    "T": "Trust",
    "B": "Body of Individuals",
    "L": "Local Authority",
    "J": "Artificial Juridical Person",
    "G": "Government",
}

# Characters commonly confused by OCR and by people typing from paper.
CONFUSABLE: dict[str, str] = {
    "0": "OD",
    "O": "0D",
    "D": "0O",
    "1": "IL7",
    "I": "1L",
    "L": "1I",
    "7": "1",
    "5": "S",
    "S": "5",
    "8": "B",
    "B": "8",
    "2": "Z",
    "Z": "2",
    "6": "G",
    "G": "6",
}


def normalise(raw: str | None) -> str:
    """Uppercase and strip everything that cannot be part of a GSTIN."""
    if not raw:
        return ""
    return re.sub(r"[^0-9A-Za-z]", "", raw).upper()


def check_char(first14: str) -> str:
    if len(first14) != 14 or any(c not in _VALUE for c in first14):
        raise ValueError("check_char needs 14 alphanumeric characters")
    total = 0
    for i, c in enumerate(first14):
        product = _VALUE[c] * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return ALPHABET[(36 - total % 36) % 36]


@dataclass(frozen=True)
class GstinCheck:
    value: str
    valid: bool
    format_ok: bool
    checksum_ok: bool
    state_ok: bool
    state_code: str | None
    pan: str | None
    entity_type: str | None
    problems: tuple[str, ...]


def validate(raw: str | None) -> GstinCheck:
    g = normalise(raw)
    problems: list[str] = []
    if len(g) != 15:
        problems.append(f"GSTIN must be 15 characters, found {len(g)}")
        return GstinCheck(g, False, False, False, False, None, None, None, tuple(problems))

    format_ok = bool(REGULAR_RE.match(g)) or bool(GENERIC_RE.match(g))
    if not format_ok:
        problems.append("Characters are not in the GSTIN layout (2 digits, PAN, entity, Z, check)")
    state_code = g[:2]
    state_ok = state_code in STATES
    if not state_ok:
        problems.append(f"State code {state_code} does not exist")
    try:
        checksum_ok = check_char(g[:14]) == g[14]
    except ValueError:
        checksum_ok = False
    if not checksum_ok:
        problems.append("Check character does not match: at least one character is wrong")
    pan = g[2:12] if PAN_RE.match(g[2:12]) else None
    entity_type = PAN_ENTITY_TYPES.get(g[5]) if pan else None
    return GstinCheck(
        value=g,
        valid=format_ok and checksum_ok and state_ok,
        format_ok=format_ok,
        checksum_ok=checksum_ok,
        state_ok=state_ok,
        state_code=state_code if state_ok else None,
        pan=pan,
        entity_type=entity_type,
        problems=tuple(problems),
    )


def is_valid(raw: str | None) -> bool:
    return validate(raw).valid


def make(state_code: str, pan: str, entity: str = "1") -> str:
    """Build a valid GSTIN (used by generators and tests)."""
    first14 = f"{state_code}{pan}{entity}Z"
    return first14 + check_char(first14)


def repair_candidates(raw: str | None, known: set[str] | None = None, max_results: int = 5) -> list[str]:
    """Return valid GSTINs one edit away from `raw`.

    Tries confusable-character substitutions, then any substitution, then adjacent swaps.
    Candidates found in `known` (e.g. the firm's supplier master) are returned first.
    """
    g = normalise(raw)
    if len(g) != 15:
        return []
    found: list[str] = []
    seen: set[str] = set()

    def consider(candidate: str) -> None:
        if candidate != g and candidate not in seen and is_valid(candidate):
            seen.add(candidate)
            found.append(candidate)

    for i, c in enumerate(g):
        for alt in CONFUSABLE.get(c, ""):
            consider(g[:i] + alt + g[i + 1 :])
    for i in range(len(g) - 1):
        consider(g[:i] + g[i + 1] + g[i] + g[i + 2 :])
    if not found or known:
        for i in range(15):
            for alt in ALPHABET:
                consider(g[:i] + alt + g[i + 1 :])

    if known:
        found.sort(key=lambda x: (x not in known,))
        in_known = [x for x in found if x in known]
        if in_known:
            return in_known[:max_results]
    # Without a known match, many single substitutions are valid; only confusable ones are meaningful.
    return found[:max_results]
