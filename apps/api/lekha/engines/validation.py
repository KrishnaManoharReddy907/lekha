"""Deterministic GST invoice validation (M4).

Each check produces Findings. A finding may carry a `fix` — a proposed correction that the
arithmetic or the rules *prove* (e.g. the only valid GSTIN one character away that is a known
supplier). Fixes are never applied silently; the review screen shows them.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from dataclasses import field as dc_field
from datetime import date
from decimal import Decimal
from typing import Any

from lekha.engines import gstin as G
from lekha.engines.dates import fy_start_year
from lekha.engines.invoice import InvoiceData
from lekha.engines.money import ZERO, d, pct_of
from lekha.engines.rules import RuleBook, RuleNotFound
from lekha.engines.states import STATES

ERROR, WARNING, INFO = "error", "warning", "info"


@dataclass
class Finding:
    code: str
    severity: str
    message: str
    field: str | None = None
    fix: dict[str, Any] | None = None
    rule: str | None = None  # "CODE@version"
    data: dict[str, Any] = dc_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationContext:
    client_gstins: set[str] = dc_field(default_factory=set)
    known_suppliers: set[str] = dc_field(default_factory=set)
    today: date = dc_field(default_factory=date.today)
    composition_supplier: bool = False


def _rule(book: RuleBook, code: str, on: date) -> tuple[dict[str, Any], str]:
    v = book.get_version(code, on)
    return v.params, f"{code}@{v.version}"


def validate_invoice(inv: InvoiceData, book: RuleBook, ctx: ValidationContext | None = None) -> list[Finding]:
    ctx = ctx or ValidationContext()
    out: list[Finding] = []
    on = inv.date or ctx.today
    tol_params, tol_rule = _rule(book, "AMOUNT_TOLERANCE", on)
    tol = Decimal(str(tol_params["value"]))

    out += _check_gstins(inv, ctx)
    out += _check_number(inv, book, on)
    out += _check_dates(inv, book, ctx)
    out += _check_place_of_supply(inv)
    out += _check_tax_heads(inv, tol)
    out += _check_rates(inv, book, on)
    out += _check_arithmetic(inv, tol, tol_rule)
    out += _check_blocked(inv, book, on)
    if inv.reverse_charge:
        out.append(Finding("RCM_INVOICE", INFO, "Reverse charge applies: the buyer pays this tax directly and "
                           "claims it as ITC after payment.", "reverse_charge"))
    return out


# --- individual checks ----------------------------------------------------------------------

def _check_gstins(inv: InvoiceData, ctx: ValidationContext) -> list[Finding]:
    out: list[Finding] = []
    if not inv.supplier_gstin:
        sev = WARNING if inv.doc_type == "bill_of_supply" else ERROR
        out.append(Finding("SUPPLIER_GSTIN_MISSING", sev, "Supplier GSTIN not found. Without it, no ITC can be claimed.",
                           "supplier_gstin"))
    else:
        chk = G.validate(inv.supplier_gstin)
        if not chk.valid:
            fixes = G.repair_candidates(inv.supplier_gstin, ctx.known_suppliers)
            known_fix = [f for f in fixes if f in ctx.known_suppliers]
            out.append(Finding(
                "SUPPLIER_GSTIN_INVALID", ERROR,
                "Supplier GSTIN is invalid: " + "; ".join(chk.problems),
                "supplier_gstin",
                fix={"supplier_gstin": known_fix[0], "reason": "Only known supplier one character away"}
                if len(known_fix) == 1 else None,
                data={"candidates": fixes},
            ))
    if inv.buyer_gstin:
        bchk = G.validate(inv.buyer_gstin)
        if not bchk.valid:
            fixes = G.repair_candidates(inv.buyer_gstin, ctx.client_gstins)
            own = [f for f in fixes if f in ctx.client_gstins]
            out.append(Finding("BUYER_GSTIN_INVALID", ERROR, "Buyer GSTIN is invalid: " + "; ".join(bchk.problems),
                               "buyer_gstin",
                               fix={"buyer_gstin": own[0], "reason": "Matches your own GSTIN with one character corrected"}
                               if len(own) == 1 else None))
        elif ctx.client_gstins and G.normalise(inv.buyer_gstin) not in ctx.client_gstins:
            out.append(Finding("BUYER_GSTIN_NOT_CLIENT", ERROR,
                               "Invoice is addressed to a different GSTIN than this client's. The supplier will report "
                               "it against that GSTIN, so it will not appear in this client's GSTR-2B.", "buyer_gstin"))
    elif inv.doc_type != "bill_of_supply":
        out.append(Finding("BUYER_GSTIN_MISSING", ERROR,
                           "Your GSTIN is not printed on the invoice. A B2B tax invoice must show it for ITC.",
                           "buyer_gstin"))
    if inv.supplier_gstin and inv.buyer_gstin and G.normalise(inv.supplier_gstin) == G.normalise(inv.buyer_gstin):
        out.append(Finding("SELF_INVOICE", ERROR, "Supplier and buyer GSTIN are the same.", "supplier_gstin"))
    return out


def _check_number(inv: InvoiceData, book: RuleBook, on: date) -> list[Finding]:
    out: list[Finding] = []
    p, r = _rule(book, "INVOICE_NUMBER_MAX_LEN", on)
    n = inv.number or ""
    if not n:
        out.append(Finding("INVOICE_NUMBER_MISSING", ERROR, "Invoice number not found.", "number", rule=r))
        return out
    if len(n) > int(p["value"]):
        out.append(Finding("INVOICE_NUMBER_TOO_LONG", WARNING,
                           f"Invoice number has {len(n)} characters; GST allows at most {p['value']}.", "number", rule=r))
    if re.search(r"[^0-9A-Za-z/\-]", n):
        out.append(Finding("INVOICE_NUMBER_CHARS", INFO,
                           "Invoice number contains characters other than letters, digits, '/' and '-'. "
                           "Suppliers often report it differently in GSTR-1.", "number", rule=r))
    return out


def _check_dates(inv: InvoiceData, book: RuleBook, ctx: ValidationContext) -> list[Finding]:
    out: list[Finding] = []
    if not inv.date:
        out.append(Finding("DATE_MISSING", ERROR, "Invoice date not found.", "date"))
        return out
    if inv.date > ctx.today:
        out.append(Finding("DATE_IN_FUTURE", ERROR, "Invoice date is in the future.", "date"))
    if inv.date < date(2017, 7, 1):
        out.append(Finding("DATE_PRE_GST", ERROR, "Invoice date is before GST started (1 July 2017).", "date"))
        return out
    p, r = _rule(book, "SEC_16_4_DEADLINE", inv.date)
    deadline = date(fy_start_year(inv.date) + 1, p["month"], p["day"])
    if ctx.today > deadline:
        out.append(Finding("ITC_TIME_BARRED", ERROR,
                           f"The deadline to claim ITC on this invoice was {deadline:%d %b %Y} (Sec 16(4)).", "date",
                           rule=r, data={"deadline": deadline.isoformat()}))
    elif (deadline - ctx.today).days <= 60:
        out.append(Finding("ITC_DEADLINE_NEAR", WARNING,
                           f"ITC must be claimed by {deadline:%d %b %Y} (Sec 16(4)).", "date",
                           rule=r, data={"deadline": deadline.isoformat()}))
    return out


def _check_place_of_supply(inv: InvoiceData) -> list[Finding]:
    if not inv.place_of_supply:
        return [Finding("POS_MISSING", WARNING,
                        "Place of supply not shown. Assumed from the buyer GSTIN state.", "place_of_supply")]
    if inv.place_of_supply not in STATES:
        return [Finding("POS_INVALID", ERROR, f"Place of supply code {inv.place_of_supply} is not a valid state.",
                        "place_of_supply")]
    if inv.buyer_gstin and len(inv.buyer_gstin) >= 2 and inv.place_of_supply != inv.buyer_gstin[:2]:
        return [Finding("POS_DIFFERS_FROM_BUYER", INFO,
                        "Place of supply differs from the buyer's registered state. This is valid for some supplies "
                        "(e.g. goods delivered elsewhere) but ITC may be restricted.", "place_of_supply")]
    return []


def _check_tax_heads(inv: InvoiceData, tol: Decimal) -> list[Finding]:
    out: list[Finding] = []
    pos = inv.place_of_supply or (inv.buyer_gstin[:2] if inv.buyer_gstin else None)
    sup = inv.supplier_state
    has_igst = inv.igst > ZERO
    has_cs = inv.cgst > ZERO or inv.sgst > ZERO
    if has_igst and has_cs:
        out.append(Finding("MIXED_TAX_HEADS", ERROR, "Invoice charges both IGST and CGST/SGST.", "igst"))
    if sup and pos and sup in STATES:
        inter = sup != pos
        if inter and has_cs and not has_igst:
            out.append(Finding(
                "WRONG_TAX_TYPE_INTER", ERROR,
                f"Supplier is in {STATES[sup].name} and place of supply is {STATES.get(pos, sup).name if pos in STATES else pos}: "
                "this is an inter-state supply and must carry IGST, not CGST+SGST. The supplier must issue a corrected "
                "invoice; ITC on the wrong tax head is at risk.", "cgst",
                fix=None, data={"expected": "IGST", "found": "CGST+SGST"}))
        if not inter and has_igst and not has_cs:
            out.append(Finding(
                "WRONG_TAX_TYPE_INTRA", ERROR,
                "Supplier and place of supply are in the same state: this is an intra-state supply and must carry "
                "CGST+SGST, not IGST.", "igst", data={"expected": "CGST+SGST", "found": "IGST"}))
    if has_cs and abs(inv.cgst - inv.sgst) > tol:
        out.append(Finding("CGST_SGST_UNEQUAL", ERROR,
                           f"CGST ({inv.cgst}) and SGST ({inv.sgst}) must be equal.", "sgst",
                           fix=_equal_split_fix(inv)))
    return out


def _equal_split_fix(inv: InvoiceData) -> dict[str, Any] | None:
    # If one of the two halves equals taxable × rate/2, propose that value for both.
    for ln_rate in {ln.tax_rate for ln in inv.lines if ln.tax_rate}:
        half = pct_of(inv.taxable_value, ln_rate / 2)
        if abs(inv.cgst - half) <= Decimal("1"):
            return {"sgst": str(inv.cgst), "reason": f"CGST matches taxable × {ln_rate / 2}%"}
        if abs(inv.sgst - half) <= Decimal("1"):
            return {"cgst": str(inv.sgst), "reason": f"SGST matches taxable × {ln_rate / 2}%"}
    return None


def _check_rates(inv: InvoiceData, book: RuleBook, on: date) -> list[Finding]:
    out: list[Finding] = []
    try:
        slabs_p, slabs_r = _rule(book, "GST_RATE_SLABS", on)
        hsn_p, hsn_r = _rule(book, "HSN_RATES", on)
    except RuleNotFound:
        return out
    slabs = {Decimal(str(s)) for s in slabs_p["value"]}
    hsn_rates: dict[str, Any] = hsn_p["value"]
    for i, ln in enumerate(inv.lines):
        if ln.hsn and not re.fullmatch(r"\d{4}|\d{6}|\d{8}", ln.hsn.replace(" ", "")):
            out.append(Finding("HSN_FORMAT", WARNING, f"Line {i + 1}: HSN/SAC '{ln.hsn}' should be 4, 6 or 8 digits.",
                               f"lines[{i}].hsn"))
        r = ln.tax_rate
        if r is None:
            continue
        if r not in slabs:
            out.append(Finding("RATE_NOT_A_SLAB", ERROR,
                               f"Line {i + 1}: {r}% is not a valid GST rate on {on:%d %b %Y}.", f"lines[{i}].tax_rate",
                               rule=slabs_r, data={"valid": sorted(float(s) for s in slabs)}))
        if ln.hsn:
            code = ln.hsn.replace(" ", "")
            for plen in (8, 6, 4):
                expected = hsn_rates.get(code[:plen])
                if expected is not None:
                    if Decimal(str(expected)) != r:
                        changed = on >= date(2025, 9, 22) and plen == 4
                        out.append(Finding(
                            "RATE_HSN_MISMATCH", WARNING,
                            f"Line {i + 1}: HSN {code[:plen]} is normally taxed at {expected}% on {on:%d %b %Y}, "
                            f"invoice shows {r}%."
                            + (" Rates for many items changed on 22 Sep 2025 (GST 2.0)." if changed else ""),
                            f"lines[{i}].tax_rate", rule=hsn_r, data={"expected": expected}))
                    break
    return out


def _check_arithmetic(inv: InvoiceData, tol: Decimal, rule: str) -> list[Finding]:
    out: list[Finding] = []
    line_tolerance = tol
    for i, ln in enumerate(inv.lines):
        if ln.quantity is not None and ln.unit_price is not None and ln.taxable_value:
            expected = (ln.quantity * ln.unit_price).quantize(Decimal("0.01"))
            if abs(expected - ln.taxable_value) > max(line_tolerance, ln.taxable_value * Decimal("0.001")):
                out.append(Finding("LINE_MATH", WARNING,
                                   f"Line {i + 1}: quantity × rate = {expected}, but taxable value is {ln.taxable_value} "
                                   "(a discount may apply).", f"lines[{i}].taxable_value", rule=rule))
        if ln.tax_rate is not None and ln.taxable_value:
            expected_tax = pct_of(ln.taxable_value, ln.tax_rate)
            line_tax = ln.igst + ln.cgst + ln.sgst
            if line_tax and abs(expected_tax - line_tax) > line_tolerance:
                out.append(Finding("LINE_TAX_MATH", ERROR,
                                   f"Line {i + 1}: tax should be {expected_tax} at {ln.tax_rate}% but is {line_tax}.",
                                   f"lines[{i}].tax_rate", rule=rule))

    if inv.lines:
        sum_taxable = sum((ln.taxable_value for ln in inv.lines), ZERO)
        if sum_taxable and abs(sum_taxable - inv.taxable_value) > tol:
            out.append(Finding("TAXABLE_SUM", ERROR,
                               f"Line items add up to {sum_taxable}, but total taxable value is {inv.taxable_value}.",
                               "taxable_value", rule=rule,
                               fix={"taxable_value": str(sum_taxable), "reason": "Sum of line items"}))
        rated = [ln for ln in inv.lines if ln.tax_rate is not None]
        if rated and len(rated) == len(inv.lines) and not any(ln.igst or ln.cgst or ln.sgst for ln in inv.lines):
            expected_tax = sum((pct_of(ln.taxable_value, ln.tax_rate or Decimal(0)) for ln in rated), ZERO)
            header_tax = inv.igst + inv.cgst + inv.sgst
            if abs(expected_tax - header_tax) > tol * max(1, len(rated)):
                out.append(Finding("TAX_RATE_MATH", ERROR,
                                   f"At the line rates, tax should be {expected_tax}, but the invoice shows {header_tax}.",
                                   "igst" if inv.igst else "cgst", rule=rule, data={"expected_tax": str(expected_tax)}))

    computed_total = inv.taxable_value + inv.total_tax + inv.round_off
    if inv.total and abs(computed_total - inv.total) > tol:
        # Rounding to whole rupees is common and legitimate.
        if abs(computed_total - inv.total) < Decimal("1.00") + tol:
            out.append(Finding("TOTAL_ROUNDED", INFO,
                               f"Total {inv.total} is the rounded value of {computed_total}.", "total", rule=rule))
        else:
            out.append(Finding("TOTAL_MATH", ERROR,
                               f"Taxable value + tax = {computed_total}, but the invoice total is {inv.total}.",
                               "total", rule=rule, fix={"total": str(computed_total), "reason": "Taxable + tax"}
                               if not inv.round_off else None))
    if inv.total == ZERO and inv.taxable_value == ZERO:
        out.append(Finding("AMOUNTS_MISSING", ERROR, "No amounts were found on the invoice.", "total"))
    if inv.doc_type != "bill_of_supply" and inv.total_tax == ZERO and inv.taxable_value > ZERO:
        out.append(Finding("NO_TAX", WARNING,
                           "No GST charged. If the supplier is under composition or the goods are exempt, this should "
                           "be a bill of supply and carries no ITC.", "igst"))
    return out


def _check_blocked(inv: InvoiceData, book: RuleBook, on: date) -> list[Finding]:
    p, r = _rule(book, "BLOCKED_CREDITS_17_5", on)
    out: list[Finding] = []
    for i, ln in enumerate(inv.lines):
        reason = blocked_reason(ln.hsn, ln.description, p)
        if reason:
            out.append(Finding("BLOCKED_CREDIT_17_5", WARNING,
                               f"Line {i + 1}: ITC is generally blocked — {reason}. Confirm the business use.",
                               f"lines[{i}].hsn", rule=r, data={"reason": reason, "line": i}))
    return out


def blocked_reason(hsn: str | None, description: str | None, params: dict[str, Any]) -> str | None:
    if hsn:
        code = hsn.replace(" ", "")
        for prefix, why in params["hsn_prefixes"].items():
            if code.startswith(prefix):
                return why
    text = f" {(description or '').lower()} "
    for kw, section in params["keywords"].items():
        if re.search(rf"\b{re.escape(kw)}\b", text):
            return f"'{kw}' suggests a blocked item — {section}"
    return None


def has_errors(findings: list[Finding]) -> bool:
    return any(f.severity == ERROR for f in findings)


def coerce_money(v: object) -> Decimal:
    return d(v)
