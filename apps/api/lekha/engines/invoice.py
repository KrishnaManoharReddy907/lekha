"""Engine-level invoice representation, independent of the database and the AI layer."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from lekha.engines.money import ZERO, d

DOC_TYPES = ("invoice", "credit_note", "debit_note", "bill_of_supply")


@dataclass
class LineData:
    description: str = ""
    hsn: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    taxable_value: Decimal = ZERO
    tax_rate: Decimal | None = None  # percent
    igst: Decimal = ZERO
    cgst: Decimal = ZERO
    sgst: Decimal = ZERO
    cess: Decimal = ZERO


@dataclass
class InvoiceData:
    doc_type: str = "invoice"
    number: str = ""
    date: date | None = None
    supplier_gstin: str | None = None
    supplier_name: str | None = None
    buyer_gstin: str | None = None
    buyer_name: str | None = None
    place_of_supply: str | None = None  # 2-digit state code
    reverse_charge: bool = False
    taxable_value: Decimal = ZERO
    igst: Decimal = ZERO
    cgst: Decimal = ZERO
    sgst: Decimal = ZERO
    cess: Decimal = ZERO
    round_off: Decimal = ZERO
    total: Decimal = ZERO
    irn: str | None = None
    lines: list[LineData] = field(default_factory=list)

    @property
    def total_tax(self) -> Decimal:
        return self.igst + self.cgst + self.sgst + self.cess

    @property
    def supplier_state(self) -> str | None:
        return self.supplier_gstin[:2] if self.supplier_gstin and len(self.supplier_gstin) >= 2 else None

    @property
    def is_inter_state(self) -> bool | None:
        if not self.supplier_state or not self.place_of_supply:
            return None
        return self.supplier_state != self.place_of_supply

    @classmethod
    def from_dict(cls, data: dict) -> InvoiceData:
        from lekha.engines.dates import parse_date

        lines = [
            LineData(
                description=str(ln.get("description") or ""),
                hsn=(str(ln["hsn"]).strip() if ln.get("hsn") else None),
                quantity=d(ln["quantity"]) if ln.get("quantity") not in (None, "") else None,
                unit=ln.get("unit"),
                unit_price=d(ln["unit_price"]) if ln.get("unit_price") not in (None, "") else None,
                taxable_value=d(ln.get("taxable_value")),
                tax_rate=Decimal(str(ln["tax_rate"])) if ln.get("tax_rate") not in (None, "") else None,
                igst=d(ln.get("igst")),
                cgst=d(ln.get("cgst")),
                sgst=d(ln.get("sgst")),
                cess=d(ln.get("cess")),
            )
            for ln in data.get("lines") or []
        ]
        return cls(
            doc_type=data.get("doc_type") or "invoice",
            number=str(data.get("number") or "").strip(),
            date=parse_date(data.get("date")),
            supplier_gstin=(data.get("supplier_gstin") or None),
            supplier_name=data.get("supplier_name"),
            buyer_gstin=(data.get("buyer_gstin") or None),
            buyer_name=data.get("buyer_name"),
            place_of_supply=data.get("place_of_supply"),
            reverse_charge=bool(data.get("reverse_charge")),
            taxable_value=d(data.get("taxable_value")),
            igst=d(data.get("igst")),
            cgst=d(data.get("cgst")),
            sgst=d(data.get("sgst")),
            cess=d(data.get("cess")),
            round_off=d(data.get("round_off")),
            total=d(data.get("total")),
            irn=data.get("irn") or None,
            lines=lines,
        )


_FY_TOKEN = re.compile(r"^(?:FY)?(?:20)?\d{2}(?:20)?\d{2}$|^FY\d{2,4}$")


def normalise_number(number: str | None) -> str:
    """Canonical invoice number for matching.

    'HH-0098' → 'HH98', 'SBT/25-26/0412' → 'SBT412', 'inv 00045' → 'INV45'.
    Separators are dropped, financial-year tokens removed, leading zeros stripped from digit runs.
    """
    if not number:
        return ""
    s = number.upper().strip()
    s = re.sub(r"(?<!\d)(20)?(\d{2})\s*[-/]\s*(20)?(\d{2})(?!\d)", _fy_or_keep, s)
    tokens = [t for t in re.split(r"[^0-9A-Z]+", s) if t]
    out = []
    for t in tokens:
        if t == "FY" or _FY_TOKEN.match(t) and _looks_like_fy(t):
            continue
        out.append(re.sub(r"\d+", lambda m: str(int(m.group(0))), t))
    return "".join(out)


def _looks_like_fy(token: str) -> bool:
    digits = re.sub(r"\D", "", token)
    if len(digits) == 4:
        a, b = int(digits[:2]), int(digits[2:])
        return b == (a + 1) % 100 and 15 <= a <= 40
    if len(digits) == 8:
        a, b = int(digits[:4]), int(digits[4:])
        return b == a + 1 and 2015 <= a <= 2040
    return False


def _fy_or_keep(m: re.Match[str]) -> str:
    a, b = int(m.group(2)), int(m.group(4))
    if b == (a + 1) % 100 and 15 <= a <= 40:
        return " "
    return m.group(0)


def core_number(number: str | None) -> str:
    """The last run of digits without leading zeros: 'SBT/25-26/0412' → '412'."""
    n = normalise_number(number)
    runs = re.findall(r"\d+", n)
    return str(int(runs[-1])) if runs else n
