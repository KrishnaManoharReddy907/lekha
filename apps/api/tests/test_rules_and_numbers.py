from datetime import date

import pytest

from lekha.engines.dates import fy_label, fy_start_year, parse_date, period_of, period_to_date
from lekha.engines.invoice import core_number, normalise_number
from lekha.engines.money import d, inr
from lekha.engines.rules import RuleBook, RuleNotFound, RuleVersion, default_rulebook


def test_rate_slabs_change_on_gst2_date():
    book = default_rulebook()
    before = book.param("GST_RATE_SLABS", date(2025, 9, 21))
    after = book.param("GST_RATE_SLABS", date(2025, 9, 22))
    assert 12 in before and 28 in before and 40 not in before
    assert 12 not in after and 28 not in after and 40 in after


def test_hsn_rate_time_travel():
    book = default_rulebook()
    assert book.param("HSN_RATES", date(2025, 8, 1))["3401"] == 18
    assert book.param("HSN_RATES", date(2025, 10, 1))["3401"] == 5


def test_missing_rule_raises():
    with pytest.raises(RuleNotFound):
        RuleBook([]).get("X", date.today())


def test_draft_overlay_wins_without_mutating_published():
    book = default_rulebook()
    draft = RuleVersion("ITC_PAYMENT_DAYS", 1, date(2026, 1, 1), None, {"value": 120, "interest_pct": 18},
                        status="draft")
    sim = book.with_overlay([draft])
    assert sim.param("ITC_PAYMENT_DAYS", date(2026, 2, 1)) == 120
    assert sim.param("ITC_PAYMENT_DAYS", date(2025, 2, 1)) == 180
    assert book.param("ITC_PAYMENT_DAYS", date(2026, 2, 1)) == 180


def test_einvoice_threshold_history():
    book = default_rulebook()
    assert book.param("EINVOICE_AATO_THRESHOLD", date(2023, 5, 1)) == 100_000_000
    assert book.param("EINVOICE_AATO_THRESHOLD", date(2024, 5, 1)) == 50_000_000


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("HH-0098", "HH98"),
        ("HH/98", "HH98"),
        ("SBT/25-26/0412", "SBT412"),
        ("SBT/2025-26/412", "SBT412"),
        ("FY25-26/INV/9", "INV9"),
        ("inv 00045", "INV45"),
        ("A-12-13", "A1213"),  # not a financial year → kept
        ("", ""),
    ],
)
def test_normalise_number(raw, expected):
    assert normalise_number(raw) == expected


def test_core_number():
    assert core_number("SBT/25-26/0412") == "412"
    assert core_number("ABC") == "ABC"


def test_fy_and_periods():
    assert fy_start_year(date(2026, 3, 31)) == 2025
    assert fy_start_year(date(2026, 4, 1)) == 2026
    assert fy_label(date(2025, 10, 14)) == "2025-26"
    assert period_of(date(2025, 10, 14)) == "102025"
    assert period_to_date("102025") == date(2025, 10, 1)
    assert period_to_date("2025-10") == date(2025, 10, 1)


def test_parse_indian_dates():
    assert parse_date("03/04/2025") == date(2025, 4, 3)
    assert parse_date("14-Oct-2025") == date(2025, 10, 14)
    assert parse_date("2025-10-14") == date(2025, 10, 14)
    assert parse_date("not a date") is None


def test_money():
    assert d("1,18,000.00") == d(118000)
    assert d("₹ 1,180") == d("1180")
    assert d("(100)") == d(-100)
    assert d(0.1) == d("0.10")
    assert inr(d("118000")) == "₹1,18,000.00"
    assert inr(d("12345678.5")) == "₹1,23,45,678.50"
    assert inr(d("-999")) == "-₹999.00"
