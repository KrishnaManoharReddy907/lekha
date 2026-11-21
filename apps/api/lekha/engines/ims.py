"""IMS decision assistant (M8): recommend Accept / Reject / Pending for each GSTR-2B record.

Under the Invoice Management System a buyer acts on every supplier record before GSTR-2B is
generated on the 14th: accepted records flow into 2B as eligible ITC, rejected ones are excluded
(and signal the supplier to correct), pending ones are carried forward. No action = deemed accepted.
Recommendations are deterministic, explainable and conservative: when in doubt, keep pending.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from lekha.engines.dates import next_month, period_to_date


@dataclass
class ImsInput:
    record_doc_type: str  # invoice | credit_note | debit_note
    itc_available: bool
    match_status: str | None  # matched | mismatch | missing_in_books | None
    mismatch_types: list[str] = field(default_factory=list)
    itc_books: Decimal = Decimal(0)
    itc_2b: Decimal = Decimal(0)
    invoice_status: str | None = None  # review | approved | rejected | duplicate
    goods_received: bool = True
    record_age_days: int = 0
    supplier_risk: float = 0.0  # 0..1 from fraud graph / anomalies
    blocked_17_5: bool = False


@dataclass
class ImsRecommendation:
    action: str  # accept | reject | pending
    reasons: list[str]
    text: str
    confidence: float


REASON_TEXT = {
    "MATCHED": "Matches an approved invoice in your books.",
    "MATCHED_UNDER_REPORTED": "Matches, but the supplier reported less tax than your invoice — accept and ask for an amendment.",
    "OVER_REPORTED": "Supplier reported more tax than your invoice shows. Accepting would claim credit you are not entitled to.",
    "WRONG_TAX_HEAD": "IGST vs CGST/SGST differs from your invoice; the supplier must correct the place of supply.",
    "DOC_TYPE": "Document type differs from your records.",
    "NOT_IN_BOOKS_RECENT": "Not in your books yet — the bill may still be on its way. Keep pending and check.",
    "NOT_IN_BOOKS_OLD": "Not in your books and over 60 days old. If you never received it, reject it.",
    "INVOICE_UNDER_REVIEW": "The matching invoice is still under review in Lekha.",
    "INVOICE_REJECTED": "You rejected the matching invoice in Lekha.",
    "GOODS_NOT_RECEIVED": "Goods or services not yet received: ITC is not allowed until receipt (Sec 16(2)(b)).",
    "ITC_NOT_AVAILABLE": "GSTR-2B already marks this ITC as not available; it will not count either way.",
    "SUPPLIER_HIGH_RISK": "Supplier shows fake-invoice risk signals. Verify before accepting.",
    "BLOCKED_17_5": "Genuine invoice, but ITC is blocked under Sec 17(5) — accept here and reverse in GSTR-3B Table 4B(1).",
    "CREDIT_NOTE": "Credit note from the supplier reduces your ITC; accept genuine credit notes.",
}


def recommend(x: ImsInput) -> ImsRecommendation:
    r: list[str] = []
    if not x.itc_available:
        return _rec("pending", ["ITC_NOT_AVAILABLE"], 0.6)
    if x.record_doc_type == "credit_note":
        if x.match_status in ("matched", "mismatch"):
            return _rec("accept", ["CREDIT_NOTE"], 0.9)
        return _rec("pending", ["NOT_IN_BOOKS_RECENT"], 0.6)
    if x.supplier_risk >= 0.7:
        return _rec("pending", ["SUPPLIER_HIGH_RISK"], 0.7)
    if x.match_status == "missing_in_books" or x.match_status is None:
        return _rec("reject", ["NOT_IN_BOOKS_OLD"], 0.65) if x.record_age_days > 60 else _rec(
            "pending", ["NOT_IN_BOOKS_RECENT"], 0.8)
    if x.invoice_status == "rejected":
        return _rec("reject", ["INVOICE_REJECTED"], 0.8)
    if not x.goods_received:
        return _rec("pending", ["GOODS_NOT_RECEIVED"], 0.9)
    if "TAX_HEAD_DIFFERENT" in x.mismatch_types:
        return _rec("reject", ["WRONG_TAX_HEAD"], 0.8)
    if "DOC_TYPE_DIFFERENT" in x.mismatch_types:
        return _rec("pending", ["DOC_TYPE"], 0.6)
    if x.itc_2b > x.itc_books + Decimal(1):
        return _rec("reject", ["OVER_REPORTED"], 0.85)
    if x.invoice_status == "review":
        return _rec("pending", ["INVOICE_UNDER_REVIEW"], 0.7)
    if x.itc_2b + Decimal(1) < x.itc_books:
        r.append("MATCHED_UNDER_REPORTED")
    else:
        r.append("MATCHED")
    if x.blocked_17_5:
        r.append("BLOCKED_17_5")
    return _rec("accept", r, 0.95 if r == ["MATCHED"] else 0.85)


def _rec(action: str, reasons: list[str], conf: float) -> ImsRecommendation:
    return ImsRecommendation(action, reasons, " ".join(REASON_TEXT[c] for c in reasons), conf)


def ims_deadline(period: str, generation_day: int = 14) -> date:
    """Last day to act in IMS for a return period: before GSTR-2B is generated in the next month."""
    return next_month(period_to_date(period)).replace(day=generation_day - 1)
