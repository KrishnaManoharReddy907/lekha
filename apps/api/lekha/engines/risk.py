"""ITC risk ledger (M9): every rupee of input tax credit (or income-tax deduction) at risk, with a deadline.

Pure functions over plain inputs; the service layer turns results into RiskItem rows. All
thresholds and dates come from the versioned rule book, evaluated on the relevant date.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from lekha.engines.dates import fy_start_year
from lekha.engines.money import ZERO
from lekha.engines.rules import RuleBook

PAISA = Decimal("0.01")


@dataclass
class RiskInvoice:
    id: object
    supplier_gstin: str | None
    supplier_name: str | None
    number: str
    invoice_date: date | None
    total: Decimal
    itc: Decimal
    blocked_amount: Decimal = ZERO
    paid: Decimal = ZERO
    status: str = "approved"
    goods_received: bool = True
    claimed_period: str | None = None
    reverse_charge: bool = False
    match_status: str | None = None  # matched | mismatch | missing_in_2b | None (no 2B uploaded yet)
    mismatch_types: list[str] = field(default_factory=list)
    itc_2b: Decimal = ZERO
    supplier_msme: str | None = None  # micro | small | medium
    supplier_has_agreement: bool = True
    supplier_3b_filed: bool | None = None  # None = unknown
    latest_2b_period_covered: bool = False  # a 2B for this invoice's period (or later) has been imported


@dataclass
class Risk:
    kind: str
    severity: str
    amount: Decimal
    due_date: date | None
    title: str
    detail: str
    rule: str
    interest: Decimal = ZERO
    invoice_id: object = None
    supplier_gstin: str | None = None

    @property
    def fingerprint(self) -> str:
        return f"{self.kind}:{self.invoice_id}"


def _sev(days_left: int | None, amount: Decimal) -> str:
    if days_left is not None and days_left < 0:
        return "critical"
    if days_left is not None and days_left <= 15:
        return "high"
    if amount >= Decimal(50000) or (days_left is not None and days_left <= 45):
        return "medium"
    return "low"


def assess(inv: RiskInvoice, book: RuleBook, today: date) -> list[Risk]:
    out: list[Risk] = []
    if inv.status in ("rejected", "duplicate") or not inv.invoice_date:
        return out
    on = inv.invoice_date
    ref = f"{inv.supplier_name or inv.supplier_gstin or 'Supplier'} · {inv.number}"
    claimable = max(ZERO, inv.itc - inv.blocked_amount)

    # Sec 17(5): permanently blocked credit.
    if inv.blocked_amount > 0:
        r = book.get_version("BLOCKED_CREDITS_17_5", on)
        out.append(Risk("S17_5_BLOCKED", "medium", inv.blocked_amount, None, f"Blocked credit — {ref}",
                        "Part of this invoice is a blocked item under Sec 17(5). It cannot be claimed; if it was "
                        "claimed, reverse it in GSTR-3B Table 4B(1).", f"BLOCKED_CREDITS_17_5@{r.version}",
                        invoice_id=inv.id, supplier_gstin=inv.supplier_gstin))

    # Not in GSTR-2B / value mismatch / tax head (only once a 2B for its period has been imported).
    if inv.latest_2b_period_covered and claimable > 0:
        if inv.match_status == "missing_in_2b":
            out.append(Risk("NOT_IN_2B", _sev(None, claimable), claimable, None, f"Not in GSTR-2B — {ref}",
                            "The supplier has not reported this invoice. Under Sec 16(2)(aa) the ITC cannot be claimed "
                            "until it appears in GSTR-2B. Ask the supplier to file / correct GSTR-1.",
                            "SEC_16_2_AA", invoice_id=inv.id, supplier_gstin=inv.supplier_gstin))
        elif inv.match_status == "mismatch":
            if "TAX_HEAD_DIFFERENT" in inv.mismatch_types:
                out.append(Risk("WRONG_TAX_HEAD", "high", claimable, None, f"Wrong tax head reported — {ref}",
                                "Supplier reported IGST instead of CGST+SGST (or the reverse). ITC under the wrong head "
                                "is at risk until the supplier amends GSTR-1.", "SEC_16_2_AA", invoice_id=inv.id,
                                supplier_gstin=inv.supplier_gstin))
            elif inv.itc_2b + Decimal(1) < claimable:
                gap = (claimable - inv.itc_2b).quantize(PAISA)
                out.append(Risk("VALUE_MISMATCH", _sev(None, gap), gap, None, f"Supplier under-reported ₹{gap:,.2f} — {ref}",
                                f"GSTR-2B shows ITC of ₹{inv.itc_2b:,.2f} against ₹{claimable:,.2f} on your invoice. "
                                "Only the 2B amount can be claimed until the supplier amends.", "SEC_16_2_AA",
                                invoice_id=inv.id, supplier_gstin=inv.supplier_gstin))

    # Rule 37: pay within 180 days of the invoice date or reverse proportionate ITC with interest.
    unpaid = max(ZERO, inv.total - inv.paid)
    if unpaid > Decimal(1) and claimable > 0 and not inv.reverse_charge:
        p = book.get("ITC_PAYMENT_DAYS", on)
        due = on + timedelta(days=int(p["value"]))
        left = (due - today).days
        share = (claimable * unpaid / inv.total).quantize(PAISA, ROUND_HALF_UP) if inv.total else claimable
        interest = ZERO
        if left < 0:
            interest = (share * Decimal(str(p["interest_pct"])) / 100 * Decimal(-left) / 365).quantize(PAISA)
        if left <= 60:
            out.append(Risk("R37_PAYMENT", _sev(left, share), share, due,
                            f"Pay supplier by {due:%d %b %Y} — {ref}" if left >= 0 else f"180 days passed unpaid — {ref}",
                            (f"₹{unpaid:,.2f} is unpaid. If not paid by {due:%d %b %Y}, ₹{share:,.2f} ITC must be reversed "
                             "with 18% interest (Rule 37). It can be re-claimed after payment.") if left >= 0 else
                            (f"₹{unpaid:,.2f} remains unpaid after 180 days. Reverse ₹{share:,.2f} ITC in GSTR-3B "
                             f"Table 4B(2) with interest (≈₹{interest:,.2f} so far); re-claim in 4D(1) after paying."),
                            f"ITC_PAYMENT_DAYS@{book.get_version('ITC_PAYMENT_DAYS', on).version}", interest,
                            inv.id, inv.supplier_gstin))

    # Sec 43B(h): pay micro/small enterprises within 45 days (15 without agreement) — income-tax deduction.
    if unpaid > Decimal(1) and inv.supplier_msme in ("micro", "small"):
        p = book.get("MSME_PAYMENT_DAYS", on)
        days = int(p["with_agreement"] if inv.supplier_has_agreement else p["without_agreement"])
        due = on + timedelta(days=days)
        left = (due - today).days
        if left <= 30:
            taxable_unpaid = unpaid
            out.append(Risk("MSME_43BH", _sev(left, taxable_unpaid), taxable_unpaid, due,
                            f"Pay MSME supplier by {due:%d %b %Y} — {ref}" if left >= 0 else f"MSME payment overdue — {ref}",
                            f"Supplier is a {inv.supplier_msme} enterprise. Unpaid ₹{unpaid:,.2f} after {days} days "
                            "cannot be deducted as an expense in income tax for the year (Sec 43B(h)) until paid.",
                            f"MSME_PAYMENT_DAYS@{book.get_version('MSME_PAYMENT_DAYS', on).version}", ZERO, inv.id,
                            inv.supplier_gstin))

    # Sec 16(4): claim by 30 November of the next financial year.
    if not inv.claimed_period and claimable > 0:
        p = book.get("SEC_16_4_DEADLINE", on)
        deadline = date(fy_start_year(on) + 1, p["month"], p["day"])
        left = (deadline - today).days
        if left <= 60:
            out.append(Risk("S16_4_DEADLINE", _sev(left, claimable), claimable, deadline,
                            f"Claim ITC by {deadline:%d %b %Y} — {ref}" if left >= 0 else f"ITC time-barred — {ref}",
                            "Not yet claimed in any GSTR-3B. After this date the credit is lost permanently (Sec 16(4))."
                            if left >= 0 else "The Sec 16(4) deadline has passed; this ITC can no longer be claimed.",
                            f"SEC_16_4_DEADLINE@{book.get_version('SEC_16_4_DEADLINE', on).version}", ZERO, inv.id,
                            inv.supplier_gstin))

    # Rule 37A: supplier did not file GSTR-3B (paid no tax) by 30 Sep of the next FY.
    if inv.supplier_3b_filed is False and claimable > 0:
        p = book.get("RULE_37A_DEADLINE", on)
        deadline = date(fy_start_year(on) + 1, p["month"], p["day"])
        left = (deadline - today).days
        out.append(Risk("R37A_SUPPLIER_3B", _sev(left, claimable), claimable, deadline,
                        f"Supplier hasn't paid the tax — {ref}",
                        "The supplier reported the invoice but has not filed GSTR-3B. If still unfiled by "
                        f"{deadline:%d %b %Y}, the ITC must be reversed (Rule 37A).",
                        f"RULE_37A_DEADLINE@{book.get_version('RULE_37A_DEADLINE', on).version}", ZERO, inv.id,
                        inv.supplier_gstin))

    if not inv.goods_received and claimable > 0:
        out.append(Risk("NOT_RECEIVED", "medium", claimable, None, f"Goods not received — {ref}",
                        "ITC is allowed only after receipt of goods or services (Sec 16(2)(b)). Keep it pending in IMS.",
                        "SEC_16_2_B", invoice_id=inv.id, supplier_gstin=inv.supplier_gstin))
    return out


def ledger_totals(risks: list[Risk]) -> dict[str, str]:
    by: dict[str, Decimal] = {}
    for r in risks:
        by[r.kind] = by.get(r.kind, ZERO) + r.amount
    itc_kinds = {"NOT_IN_2B", "VALUE_MISMATCH", "WRONG_TAX_HEAD", "R37_PAYMENT", "S16_4_DEADLINE", "R37A_SUPPLIER_3B",
                 "NOT_RECEIVED", "S17_5_BLOCKED"}
    itc = sum((v for k, v in by.items() if k in itc_kinds), ZERO)
    return {"itc_at_risk": str(itc), "deduction_at_risk": str(by.get("MSME_43BH", ZERO)),
            **{k: str(v) for k, v in by.items()}}
