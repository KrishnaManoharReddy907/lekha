"""Versioned GST rules — the "time-travel" rules engine (N4).

Every rate, deadline and threshold is a RuleVersion with an effective date range.
Evaluation always asks "what was the rule on date X?", so an invoice from August 2025
is checked against pre-GST-2.0 rates even when evaluated today.

Draft versions can be overlaid on the published book to simulate the impact of a rule
change before publishing it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

GST2_DATE = date(2025, 9, 22)


@dataclass(frozen=True)
class RuleVersion:
    code: str
    version: int
    effective_from: date
    effective_to: date | None  # inclusive; None = open-ended
    params: dict[str, Any]
    title: str = ""
    source: str = ""
    status: str = "published"  # draft | published | retired

    def applies_on(self, on: date) -> bool:
        return self.effective_from <= on and (self.effective_to is None or on <= self.effective_to)


class RuleNotFound(KeyError):
    pass


@dataclass
class RuleBook:
    versions: list[RuleVersion] = field(default_factory=list)

    def get_version(self, code: str, on: date) -> RuleVersion:
        candidates = [v for v in self.versions if v.code == code and v.applies_on(on)]
        if not candidates:
            raise RuleNotFound(f"No rule {code} effective on {on.isoformat()}")
        # Latest effective_from wins, then highest version number.
        return max(candidates, key=lambda v: (v.effective_from, v.version))

    def get(self, code: str, on: date) -> dict[str, Any]:
        return self.get_version(code, on).params

    def param(self, code: str, on: date, key: str = "value") -> Any:
        return self.get(code, on)[key]

    def codes(self) -> list[str]:
        return sorted({v.code for v in self.versions})

    def with_overlay(self, drafts: Iterable[RuleVersion]) -> RuleBook:
        """Return a new book where draft versions take precedence over published ones."""
        drafts = list(drafts)
        top = max((v.version for v in self.versions), default=0)
        boosted = [replace(dv, version=top + 1 + i) for i, dv in enumerate(drafts)]
        return RuleBook([*self.versions, *boosted])


# --- Built-in seed rules ------------------------------------------------------------------
# Sources are recorded so users can see why a rule says what it says. Values must be
# kept current by an administrator through the Rules screen.

_OPEN = None

SEED: list[RuleVersion] = [
    RuleVersion(
        "GST_RATE_SLABS", 1, date(2017, 7, 1), date(2025, 9, 21),
        {"value": [0, 0.1, 0.25, 1, 1.5, 3, 5, 6, 7.5, 12, 18, 28]},
        "Valid GST rates (pre GST 2.0)", "CBIC rate notifications 2017-2025",
    ),
    RuleVersion(
        "GST_RATE_SLABS", 2, GST2_DATE, _OPEN,
        {"value": [0, 0.1, 0.25, 1, 1.5, 3, 5, 6, 7.5, 18, 40]},
        "Valid GST rates (GST 2.0: 5% merit, 18% standard, 40% demerit)",
        "56th GST Council meeting; effective 22 Sep 2025",
    ),
    RuleVersion(
        "HSN_RATES", 1, date(2017, 7, 1), date(2025, 9, 21),
        {"value": {
            "3401": 18, "3305": 18, "3306": 18, "2523": 28, "8415": 28, "8528": 28,
            "8418": 18, "8471": 18, "8517": 18, "0401": 0, "0702": 0, "2202": 28,
            "2402": 28, "8703": 28, "9983": 18, "9973": 18, "9985": 18, "9954": 18,
        }},
        "Known HSN/SAC rates (subset, pre GST 2.0)", "CBIC schedules (seeded subset — verify)",
    ),
    RuleVersion(
        "HSN_RATES", 2, GST2_DATE, _OPEN,
        {"value": {
            "3401": 5, "3305": 5, "3306": 5, "2523": 18, "8415": 18, "8528": 18,
            "8418": 18, "8471": 18, "8517": 18, "0401": 0, "0702": 0, "2202": 40,
            "2402": 40, "9983": 18, "9973": 18, "9985": 18, "9954": 18,
        }},
        "Known HSN/SAC rates (subset, GST 2.0)", "CBIC notifications Sep 2025 (seeded subset — verify)",
    ),
    RuleVersion(
        "ITC_PAYMENT_DAYS", 1, date(2017, 7, 1), _OPEN, {"value": 180, "interest_pct": 18},
        "Pay supplier within 180 days or reverse ITC (Rule 37)", "CGST Rule 37 read with Sec 16(2)",
    ),
    RuleVersion(
        "RULE_37A_DEADLINE", 1, date(2023, 10, 1), _OPEN, {"month": 9, "day": 30},
        "Reverse ITC if supplier has not filed GSTR-3B by 30 Sep of next FY", "CGST Rule 37A",
    ),
    RuleVersion(
        "SEC_16_4_DEADLINE", 1, date(2017, 7, 1), _OPEN, {"month": 11, "day": 30},
        "Last date to claim ITC: 30 Nov of next FY (or annual return, if earlier)", "CGST Sec 16(4)",
    ),
    RuleVersion(
        "MSME_PAYMENT_DAYS", 1, date(2023, 4, 1), _OPEN, {"with_agreement": 45, "without_agreement": 15},
        "Pay micro/small enterprises in time or lose the income-tax deduction",
        "Income-tax Act Sec 43B(h) / MSMED Act Sec 15",
    ),
    RuleVersion(
        "GSTR2B_GENERATION_DAY", 1, date(2020, 8, 1), _OPEN, {"value": 14},
        "GSTR-2B is generated on the 14th of the following month", "GSTN",
    ),
    RuleVersion(
        "GSTR3B_DUE_DAY", 1, date(2017, 7, 1), _OPEN, {"value": 20},
        "GSTR-3B due date for monthly filers", "CGST Rule 61",
    ),
    RuleVersion(
        "IMS_AVAILABLE", 1, date(2024, 10, 1), _OPEN, {"value": True, "deemed_action": "accept"},
        "Invoice Management System: accept / reject / pending; no action = deemed accepted", "GSTN IMS advisory",
    ),
    RuleVersion(
        "EINVOICE_AATO_THRESHOLD", 1, date(2022, 10, 1), date(2023, 7, 31), {"value": 100_000_000},
        "E-invoice mandatory above ₹10 crore turnover", "Notification 17/2022-CT",
    ),
    RuleVersion(
        "EINVOICE_AATO_THRESHOLD", 2, date(2023, 8, 1), _OPEN, {"value": 50_000_000},
        "E-invoice mandatory above ₹5 crore turnover", "Notification 10/2023-CT",
    ),
    RuleVersion(
        "EINVOICE_30DAY_AATO", 1, date(2023, 11, 1), date(2025, 3, 31), {"value": 1_000_000_000},
        "IRN must be generated within 30 days (turnover ≥ ₹100 crore)", "GSTN advisory",
    ),
    RuleVersion(
        "EINVOICE_30DAY_AATO", 2, date(2025, 4, 1), _OPEN, {"value": 100_000_000},
        "IRN must be generated within 30 days (turnover ≥ ₹10 crore)", "GSTN advisory 5 Nov 2024",
    ),
    RuleVersion(
        "INVOICE_NUMBER_MAX_LEN", 1, date(2017, 7, 1), _OPEN, {"value": 16},
        "Invoice number: max 16 characters, unique per FY, only letters, digits, '/' and '-'", "CGST Rule 46(b)",
    ),
    RuleVersion(
        "AMOUNT_TOLERANCE", 1, date(2017, 7, 1), _OPEN, {"value": "1.00"},
        "Rounding tolerance in rupees for arithmetic checks and matching", "Lekha default",
    ),
    RuleVersion(
        "BLOCKED_CREDITS_17_5", 1, date(2017, 7, 1), _OPEN,
        {
            "hsn_prefixes": {
                "8703": "Motor vehicles for passengers (≤13 seats) — 17(5)(a)",
                "8711": "Motorcycles — 17(5)(a)",
                "9963": "Food, beverages, catering, accommodation services — 17(5)(b)(i)",
                "9972": "Rental of immovable property (check use) — 17(5)(c)/(d)",
                "9971": "Life / health insurance — 17(5)(b)(i)",
                "9993": "Health services — 17(5)(b)(i)",
                "9997": "Beauty / fitness / club services — 17(5)(b)(i)-(ii)",
                "9954": "Construction of immovable property (works contract) — 17(5)(c)/(d)",
                "2106": "Food preparations — 17(5)(b)(i)",
                "2202": "Beverages — 17(5)(b)(i)",
            },
            "keywords": {
                "food": "17(5)(b)(i)", "meal": "17(5)(b)(i)", "lunch": "17(5)(b)(i)",
                "dinner": "17(5)(b)(i)", "restaurant": "17(5)(b)(i)", "catering": "17(5)(b)(i)",
                "beverage": "17(5)(b)(i)", "hotel stay": "17(5)(b)(i)", "gym": "17(5)(b)(ii)",
                "club membership": "17(5)(b)(ii)", "health insurance": "17(5)(b)(i)",
                "life insurance": "17(5)(b)(i)", "beauty": "17(5)(b)(i)", "car": "17(5)(a)",
                "gift": "17(5)(h)", "personal": "17(5)(g)",
            },
        },
        "Blocked input tax credit", "CGST Sec 17(5)",
    ),
]


def default_rulebook() -> RuleBook:
    return RuleBook(list(SEED))
