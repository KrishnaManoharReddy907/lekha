"""Builds and maintains the risk ledger (RiskItem rows) for a client."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.engines import risk as R
from lekha.engines.money import ZERO
from lekha.models import Gstr2bImport, Invoice, Match, Payment, RiskItem, Supplier
from lekha.services.rulebook import rulebook_for


def _pk(p: str) -> tuple[int, int]:
    return int(p[2:]), int(p[:2])


async def recompute_client(db: AsyncSession, firm_id: uuid.UUID, client_id: uuid.UUID,
                           today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    book = await rulebook_for(db, firm_id)
    invoices = list((await db.execute(select(Invoice).where(Invoice.client_id == client_id,
                                                            Invoice.status.in_(("approved", "review"))))).scalars())
    paid = dict((await db.execute(select(Payment.invoice_id, func.sum(Payment.amount)).where(
        Payment.invoice_id.in_([i.id for i in invoices])).group_by(Payment.invoice_id))).all())
    latest_match: dict[uuid.UUID, Match] = {}
    for m in (await db.execute(select(Match).where(Match.client_id == client_id, Match.invoice_id.is_not(None))
                               .order_by(Match.created_at))).scalars():
        prev = latest_match.get(m.invoice_id)
        # A real match in any period beats a later "missing" row.
        if prev is None or m.status != "missing_in_2b" or prev.status == "missing_in_2b":
            latest_match[m.invoice_id] = m
    itc_2b_by_inv: dict[uuid.UUID, Any] = defaultdict(lambda: ZERO)
    for m in latest_match.values():
        itc_2b_by_inv[m.invoice_id] = m.itc_2b
    periods_2b: dict[str, list[str]] = defaultdict(list)
    for imp in (await db.execute(select(Gstr2bImport).where(Gstr2bImport.client_id == client_id,
                                                            Gstr2bImport.superseded.is_(False)))).scalars():
        periods_2b[imp.client_gstin].append(imp.period)
    sup = {s.gstin: s for s in (await db.execute(select(Supplier).where(
        Supplier.gstin.in_({i.supplier_gstin for i in invoices if i.supplier_gstin})))).scalars()}

    produced: list[R.Risk] = []
    for i in invoices:
        s = sup.get(i.supplier_gstin or "")
        m = latest_match.get(i.id)
        covered = bool(i.period) and any(_pk(p) >= _pk(i.period) for p in periods_2b.get(i.client_gstin or "", []))
        ri = R.RiskInvoice(
            id=i.id, supplier_gstin=i.supplier_gstin, supplier_name=i.supplier_name, number=i.number,
            invoice_date=i.invoice_date, total=i.total, itc=i.itc, blocked_amount=i.blocked_amount or ZERO,
            paid=paid.get(i.id) or ZERO, status=i.status, goods_received=i.goods_received,
            claimed_period=i.claimed_period, reverse_charge=i.reverse_charge,
            match_status=m.status if m else ("missing_in_2b" if covered else None),
            mismatch_types=list(m.mismatch_types or []) if m else [], itc_2b=itc_2b_by_inv[i.id] if m else ZERO,
            supplier_msme=s.msme_category if s else None,
            supplier_has_agreement=s.has_payment_agreement if s else True,
            supplier_3b_filed=(s.score_detail or {}).get("gstr3b_filed") if s else None,
            latest_2b_period_covered=covered,
        )
        produced.extend(R.assess(ri, book, today))

    existing = {r.fingerprint: r for r in (await db.execute(select(RiskItem).where(RiskItem.client_id == client_id)))
                .scalars()}
    inv_gstin = {i.id: i.client_gstin for i in invoices}
    seen = set()
    created = updated = 0
    for r in produced:
        seen.add(r.fingerprint)
        row = existing.get(r.fingerprint)
        if row is None:
            db.add(RiskItem(firm_id=firm_id, client_id=client_id, client_gstin=inv_gstin.get(r.invoice_id),
                            invoice_id=r.invoice_id, supplier_gstin=r.supplier_gstin, kind=r.kind, severity=r.severity,
                            amount=r.amount, interest=r.interest, due_date=r.due_date, title=r.title, detail=r.detail,
                            rule=r.rule, fingerprint=r.fingerprint))
            created += 1
        else:
            row.severity, row.amount, row.interest, row.due_date = r.severity, r.amount, r.interest, r.due_date
            row.title, row.detail, row.rule = r.title, r.detail, r.rule
            if row.status == "resolved":
                row.status, row.resolved_at = "open", None
            updated += 1
    resolved = 0
    for fp, row in existing.items():
        if fp in seen:
            continue
        if row.status in ("open", "accepted"):
            row.status = "resolved"
            row.resolved_at = datetime.now(UTC)
            resolved += 1
        elif row.status == "reversed":
            # The condition cleared after the ITC was reversed in 3B: it can now be re-claimed in Table 4D(1).
            row.status = "reclaimable"
            resolved += 1
    totals = R.ledger_totals(produced)
    return {"created": created, "updated": updated, "resolved": resolved, **totals}


async def ledger(db: AsyncSession, client_id: uuid.UUID, include_resolved: bool = False) -> dict[str, Any]:
    q = select(RiskItem).where(RiskItem.client_id == client_id)
    if not include_resolved:
        q = q.where(RiskItem.status.in_(("open", "accepted", "reversed")))
    items = list((await db.execute(q.order_by(RiskItem.due_date.asc().nullslast(), RiskItem.amount.desc()))).scalars())
    by_kind: dict[str, Any] = defaultdict(lambda: ZERO)
    for r in items:
        if r.status != "resolved":
            by_kind[r.kind] += r.amount
    itc_kinds = {"NOT_IN_2B", "VALUE_MISMATCH", "WRONG_TAX_HEAD", "R37_PAYMENT", "S16_4_DEADLINE", "R37A_SUPPLIER_3B",
                 "NOT_RECEIVED", "S17_5_BLOCKED"}
    return {
        "items": items,
        "totals": {"itc_at_risk": str(sum((v for k, v in by_kind.items() if k in itc_kinds), ZERO)),
                   "deduction_at_risk": str(by_kind.get("MSME_43BH", ZERO)),
                   "by_kind": {k: str(v) for k, v in by_kind.items()}},
    }
