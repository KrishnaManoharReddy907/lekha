"""Money helpers. All amounts are Decimal rupees with 2 decimal places; floats never touch money."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

ZERO = Decimal("0.00")
PAISA = Decimal("0.01")


def d(value: object) -> Decimal:
    """Convert anything sensible ('1,18,000.00', 118000, '₹ 1,180') to a 2-dp Decimal."""
    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        return value.quantize(PAISA, ROUND_HALF_UP)
    if isinstance(value, int):
        return Decimal(value).quantize(PAISA)
    if isinstance(value, float):
        return Decimal(repr(value)).quantize(PAISA, ROUND_HALF_UP)
    s = str(value).replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()").strip()
    try:
        out = Decimal(s).quantize(PAISA, ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError(f"Not a money value: {value!r}") from exc
    return -out if negative else out


def rate(value: object) -> Decimal:
    """Tax rate in percent, e.g. '18%' -> Decimal('18')."""
    if value is None or value == "":
        return Decimal("0")
    s = str(value).replace("%", "").strip()
    return Decimal(s).normalize() if s else Decimal("0")


def pct_of(amount: Decimal, pct: Decimal) -> Decimal:
    return (amount * pct / Decimal(100)).quantize(PAISA, ROUND_HALF_UP)


def close(a: Decimal, b: Decimal, tolerance: Decimal = Decimal("1.00")) -> bool:
    return abs(a - b) <= tolerance


def inr(amount: Decimal | int | float | None) -> str:
    """Format in the Indian digit grouping: 1,18,000.00"""
    if amount is None:
        return "—"
    v = d(amount)
    sign = "-" if v < 0 else ""
    whole, frac = f"{abs(v):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{sign}₹{whole}.{frac}"
