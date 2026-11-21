"""Indian financial-year and GST period helpers."""

from __future__ import annotations

import re
from datetime import date, datetime

from dateutil import parser as _dp


def fy_start_year(d: date) -> int:
    """FY runs 1 April → 31 March. 14 Oct 2025 → 2025 (FY 2025-26)."""
    return d.year if d.month >= 4 else d.year - 1


def fy_label(d: date) -> str:
    y = fy_start_year(d)
    return f"{y}-{str(y + 1)[-2:]}"


def fy_bounds(start_year: int) -> tuple[date, date]:
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


def period_of(d: date) -> str:
    """GST return period string MMYYYY used by the portal, e.g. '102025'."""
    return f"{d.month:02d}{d.year}"


def period_to_date(period: str) -> date:
    """'102025' or '2025-10' → first day of that month."""
    p = period.strip()
    if re.fullmatch(r"\d{6}", p):
        return date(int(p[2:]), int(p[:2]), 1)
    m = re.fullmatch(r"(\d{4})-(\d{2})", p)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)
    raise ValueError(f"Unrecognised period {period!r}")


def next_month(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def months_between(a: date, b: date) -> int:
    """Whole calendar months from a's month to b's month."""
    return (b.year - a.year) * 12 + (b.month - a.month)


def parse_date(value: str | date | datetime | None) -> date | None:
    """Parse Indian-style dates. Day comes first: 03/04/2025 is 3 April."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return date.fromisoformat(s)
    try:
        return _dp.parse(s, dayfirst=True).date()
    except (ValueError, OverflowError):
        return None
