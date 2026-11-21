from datetime import date

from lekha.engines import gstin as G
from lekha.engines.invoice import InvoiceData
from lekha.engines.rules import default_rulebook
from lekha.engines.validation import ValidationContext, has_errors, validate_invoice

BOOK = default_rulebook()
KERALA_SUPPLIER = G.make("32", "AAKFS4421M")
TS_BUYER = G.make("36", "AAGCD7781Q")
TS_SUPPLIER = G.make("36", "AAKFH1234K")
CTX = ValidationContext(client_gstins={TS_BUYER}, known_suppliers={KERALA_SUPPLIER, TS_SUPPLIER},
                        today=date(2025, 11, 15))


def base(**over):
    data = {
        "number": "SBT/25-26/0412",
        "date": "2025-10-14",
        "supplier_gstin": KERALA_SUPPLIER,
        "buyer_gstin": TS_BUYER,
        "place_of_supply": "36",
        "taxable_value": "100000",
        "igst": "18000",
        "total": "118000",
        "lines": [
            {"description": "Teak wood planks", "hsn": "4407", "quantity": "20", "unit_price": "3500",
             "taxable_value": "70000", "tax_rate": "18"},
            {"description": "Rosewood blocks", "hsn": "4407", "quantity": "6", "unit_price": "5000",
             "taxable_value": "30000", "tax_rate": "18"},
        ],
    }
    data.update(over)
    return InvoiceData.from_dict(data)


def codes(inv, ctx=CTX):
    return {f.code for f in validate_invoice(inv, BOOK, ctx)}


def test_clean_inter_state_invoice_passes():
    findings = validate_invoice(base(), BOOK, CTX)
    assert not has_errors(findings), [f.message for f in findings]


def test_cgst_sgst_on_inter_state_is_error():
    inv = base(igst="0", cgst="9000", sgst="9000")
    assert "WRONG_TAX_TYPE_INTER" in codes(inv)


def test_igst_on_intra_state_is_error():
    inv = base(supplier_gstin=TS_SUPPLIER)
    assert "WRONG_TAX_TYPE_INTRA" in codes(inv)


def test_supplier_gstin_typo_gets_known_fix():
    typo = KERALA_SUPPLIER[:8] + ("1" if KERALA_SUPPLIER[8] != "1" else "2") + KERALA_SUPPLIER[9:]
    findings = validate_invoice(base(supplier_gstin=typo), BOOK, CTX)
    f = next(f for f in findings if f.code == "SUPPLIER_GSTIN_INVALID")
    assert f.fix and f.fix["supplier_gstin"] == KERALA_SUPPLIER


def test_invoice_to_other_gstin():
    other = G.make("36", "ZZZZZ9999Z")
    assert "BUYER_GSTIN_NOT_CLIENT" in codes(base(buyer_gstin=other))


def test_rate_28_invalid_after_gst2():
    inv = base(lines=[{"description": "Cement", "hsn": "2523", "taxable_value": "100000", "tax_rate": "28"}],
               igst="28000", total="128000")
    c = codes(inv)
    assert "RATE_NOT_A_SLAB" in c and "RATE_HSN_MISMATCH" in c


def test_rate_28_valid_before_gst2():
    inv = base(date="2025-09-10",
               lines=[{"description": "Cement", "hsn": "2523", "taxable_value": "100000", "tax_rate": "28"}],
               igst="28000", total="128000")
    c = codes(inv)
    assert "RATE_NOT_A_SLAB" not in c and "RATE_HSN_MISMATCH" not in c


def test_total_mismatch_gets_fix():
    findings = validate_invoice(base(total="128000"), BOOK, CTX)
    f = next(f for f in findings if f.code == "TOTAL_MATH")
    assert f.fix["total"] == "118000.00"


def test_rounded_total_is_info_only():
    inv = base(taxable_value="100000.40", total="118000", igst="18000.07",
               lines=[{"description": "x", "hsn": "4407", "taxable_value": "100000.40", "tax_rate": "18"}])
    findings = validate_invoice(inv, BOOK, CTX)
    # 0.47 rupee difference is inside the ₹1 tolerance: nothing to report.
    assert not any(f.code in ("TOTAL_MATH", "TOTAL_ROUNDED") for f in findings)
    # 1.47 rupee difference: legitimate round-off, reported as info only.
    inv2 = base(taxable_value="100001.40", total="118000", igst="18000.07",
                lines=[{"description": "x", "hsn": "4407", "taxable_value": "100001.40", "tax_rate": "18"}])
    codes2 = {f.code for f in validate_invoice(inv2, BOOK, CTX)}
    assert "TOTAL_ROUNDED" in codes2 and "TOTAL_MATH" not in codes2


def test_line_sum_mismatch():
    assert "TAXABLE_SUM" in codes(base(taxable_value="90000", igst="16200", total="106200"))


def test_tax_rate_math():
    assert "TAX_RATE_MATH" in codes(base(igst="15000", total="115000"))


def test_blocked_food():
    inv = base(lines=[{"description": "Lunch for staff", "hsn": "996331", "taxable_value": "100000", "tax_rate": "5"}],
               igst="5000", total="105000", supplier_gstin=KERALA_SUPPLIER)
    assert "BLOCKED_CREDIT_17_5" in codes(inv)


def test_time_barred():
    ctx = ValidationContext(client_gstins={TS_BUYER}, today=date(2026, 12, 1))
    inv = base(date="2024-10-14")
    assert "ITC_TIME_BARRED" in codes(inv, ctx)


def test_future_and_missing_fields():
    c = codes(InvoiceData.from_dict({"date": "2030-01-01"}))
    assert {"DATE_IN_FUTURE", "INVOICE_NUMBER_MISSING", "SUPPLIER_GSTIN_MISSING", "AMOUNTS_MISSING"} <= c


def test_cgst_sgst_unequal():
    inv = base(supplier_gstin=TS_SUPPLIER, igst="0", cgst="9000", sgst="8000", total="117000")
    findings = validate_invoice(inv, BOOK, CTX)
    f = next(f for f in findings if f.code == "CGST_SGST_UNEQUAL")
    assert f.fix == {"sgst": "9000.00", "reason": "CGST matches taxable × 9%"}
