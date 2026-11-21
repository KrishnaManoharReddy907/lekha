"""Per-client anomaly and fraud signals (M11). Results are stored on Invoice.flags."""

from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.engines.dates import fy_start_year
from lekha.models import Document, Invoice, Supplier

LAKH = Decimal("100000")


def _hamming(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 64


def detect(invoices: list[Invoice], docs: dict[uuid.UUID, Document], suppliers: dict[str, Supplier],
           today: date | None = None) -> dict[uuid.UUID, list[dict[str, Any]]]:
    today = today or date.today()
    flags: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    live = [i for i in invoices if i.status != "rejected"]

    # 1. Same supplier + same normalised number in the same financial year = duplicate.
    seen: dict[tuple, Invoice] = {}
    for inv in sorted(live, key=lambda i: i.created_at):
        if not inv.supplier_gstin or not inv.number_norm or not inv.invoice_date:
            continue
        key = (inv.supplier_gstin, inv.number_norm, fy_start_year(inv.invoice_date), inv.doc_type)
        if key in seen:
            first = seen[key]
            flags[inv.id].append({"code": "DUPLICATE_INVOICE", "severity": "high",
                                  "message": f"Same supplier and invoice number as an invoice added earlier "
                                             f"({first.number}). Claiming both would double the ITC.",
                                  "related": str(first.id)})
        else:
            seen[key] = inv

    # 2. The same bill photographed twice (different file, near-identical image).
    with_hash = [(i, docs[i.document_id]) for i in live if i.document_id in docs and docs[i.document_id].phash]
    for a_idx, (ia, da) in enumerate(with_hash):
        for ib, db_ in with_hash[a_idx + 1:]:
            if da.sha256 != db_.sha256 and _hamming(da.phash, db_.phash) <= 4:
                later = ib if ib.created_at >= ia.created_at else ia
                other = ia if later is ib else ib
                flags[later.id].append({"code": "NEAR_DUPLICATE_IMAGE", "severity": "medium",
                                        "message": "This document looks almost identical to another upload "
                                                   "(re-photographed or re-scanned).", "related": str(other.id)})

    totals = [i.total for i in live if i.total]
    median_total = Decimal(str(statistics.median(totals))) if totals else Decimal(0)
    by_supplier: dict[str, list[Invoice]] = defaultdict(list)
    for inv in live:
        if inv.supplier_gstin:
            by_supplier[inv.supplier_gstin].append(inv)

    history_start = min((i.invoice_date for i in live if i.invoice_date), default=None)
    for inv in live:
        sup = suppliers.get(inv.supplier_gstin or "")
        # 3. Brand-new supplier with an unusually large invoice. "New" is relative to this client's history:
        # when a year of past invoices is imported at once, every supplier would otherwise look new.
        is_new = (sup and sup.first_seen and inv.invoice_date and history_start
                  and (sup.first_seen - history_start).days >= 60
                  and (inv.invoice_date - sup.first_seen).days <= 60)
        if is_new:
            if inv.total >= 2 * LAKH and median_total and inv.total >= 3 * median_total:
                flags[inv.id].append({"code": "NEW_SUPPLIER_LARGE", "severity": "medium",
                                      "message": f"First seen {sup.first_seen:%d %b %Y}: a new supplier with an "
                                                 f"invoice {float(inv.total / median_total):.1f}× your usual size."})
        # 4. Suspiciously round taxable value on a large invoice.
        if inv.taxable_value >= LAKH and inv.taxable_value % 10000 == 0:
            flags[inv.id].append({"code": "ROUND_AMOUNT", "severity": "low",
                                  "message": "Large, perfectly round taxable value. Common in fake invoices; "
                                             "confirm goods/services were actually received."})

    # 5. Low invoice-sequence velocity: a supplier issuing very few invoices overall but large ones to you.
    for invs in by_supplier.values():
        numbered = [(i, _seq(i.number)) for i in invs if i.invoice_date and _seq(i.number) is not None]
        if len(numbered) < 2:
            continue
        numbered.sort(key=lambda t: t[0].invoice_date)
        first, last = numbered[0], numbered[-1]
        months = max(1, (last[0].invoice_date - first[0].invoice_date).days / 30)
        velocity = (last[1] - first[1]) / months
        if 0 <= velocity < 3 and months >= 2:
            for inv, _n in numbered:
                if inv.total >= 5 * LAKH:
                    flags[inv.id].append({"code": "LOW_SEQUENCE_VELOCITY", "severity": "medium",
                                          "message": f"Supplier's invoice numbers advance only ~{velocity:.1f} per month "
                                                     "— it seems to issue very few invoices, yet this one is large. "
                                                     "Typical of shell suppliers."})

    # 6. Invoice dated long before it was received/uploaded.
    for inv in live:
        if inv.invoice_date and (today - inv.invoice_date) > timedelta(days=300) and inv.status == "review":
            flags[inv.id].append({"code": "STALE_INVOICE", "severity": "low",
                                  "message": "Invoice is more than 10 months old; check the Sec 16(4) deadline."})
    return flags


def _seq(number: str | None) -> int | None:
    from lekha.engines.invoice import core_number

    c = core_number(number)
    return int(c) if c and c.isdigit() and len(c) <= 7 else None


async def scan_client(db: AsyncSession, firm_id: uuid.UUID, client_id: uuid.UUID) -> dict[str, int]:
    invoices = list((await db.execute(select(Invoice).where(Invoice.client_id == client_id))).scalars())
    doc_ids = [i.document_id for i in invoices if i.document_id]
    docs = {d.id: d for d in (await db.execute(select(Document).where(Document.id.in_(doc_ids)))).scalars()}
    gstins = {i.supplier_gstin for i in invoices if i.supplier_gstin}
    suppliers = {s.gstin: s for s in (await db.execute(select(Supplier).where(Supplier.gstin.in_(gstins)))).scalars()}
    flags = detect(invoices, docs, suppliers)
    changed = 0
    for inv in invoices:
        new = flags.get(inv.id, [])
        if [f["code"] for f in new] != [f.get("code") for f in (inv.flags or []) if isinstance(f, dict)]:
            inv.flags = new
            changed += 1
        if any(f["code"] == "DUPLICATE_INVOICE" for f in new) and inv.status == "review":
            inv.status = "duplicate"
    return {"flagged": sum(1 for v in flags.values() if v), "changed": changed}

