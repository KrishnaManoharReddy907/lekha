"""Computes GSTR-3B Table 4 for a client GSTIN and period from 2B + IMS + books + risk ledger (N6)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.core.audit import audit
from lekha.core.errors import AppError
from lekha.engines.dates import next_month, period_to_date
from lekha.engines.table4 import Amount, Item, build
from lekha.models import Gstr2bRecord, ImsDecision, Invoice, Match, RiskItem, Table4Snapshot

TEMPORARY = ("R37_PAYMENT", "R37A_SUPPLIER_3B", "NOT_RECEIVED")


def _amt(x: Any) -> Amount:
    return Amount(x.igst or Decimal(0), x.cgst or Decimal(0), x.sgst or Decimal(0), x.cess or Decimal(0))


async def compute(db: AsyncSession, client_id: uuid.UUID, gstin: str, period: str) -> dict[str, Any]:
    records = list((await db.execute(select(Gstr2bRecord).where(Gstr2bRecord.client_id == client_id,
                                                                 Gstr2bRecord.client_gstin == gstin,
                                                                 Gstr2bRecord.period == period))).scalars())
    if not records:
        raise AppError("Import the GSTR-2B for this period first.", code="no_2b")
    decisions = {d.record_id: d for d in (await db.execute(select(ImsDecision).where(
        ImsDecision.record_id.in_([r.id for r in records])))).scalars()}
    matches = {m.record_id: m for m in (await db.execute(select(Match).where(
        Match.record_id.in_([r.id for r in records])))).scalars()}
    inv_ids = [m.invoice_id for m in matches.values() if m.invoice_id]
    invoices = {i.id: i for i in (await db.execute(select(Invoice).where(Invoice.id.in_(inv_ids)))).scalars()}
    period_end = next_month(period_to_date(period))
    risks: dict[uuid.UUID, list[RiskItem]] = {}
    for r in (await db.execute(select(RiskItem).where(RiskItem.client_id == client_id,
                                                      RiskItem.status.in_(("open", "accepted", "reversed",
                                                                           "reclaimable"))))).scalars():
        if r.invoice_id:
            risks.setdefault(r.invoice_id, []).append(r)

    items: list[Item] = []
    for rec in records:
        dec = decisions.get(rec.id)
        action = dec.decided_action if dec and dec.decided_action else "accept"  # no action = deemed accepted
        label = f"{rec.supplier_name or rec.supplier_gstin} · {rec.number}"
        ref = f"rec:{rec.id}"
        if not rec.itc_available:
            items.append(Item(ref, label, _amt(rec), "none", ineligible_reason=rec.itc_unavailable_reason or
                              "ITC not available as per GSTR-2B"))
            continue
        if action in ("reject", "pending"):
            continue
        row = {"impg": "4A1", "isd": "4A4"}.get(rec.section, "4A5")
        sign = -1 if rec.doc_type == "credit_note" else 1
        it = Item(ref, label, _amt(rec), row, sign)
        m = matches.get(rec.id)
        inv = invoices.get(m.invoice_id) if m and m.invoice_id else None
        if sign > 0:
            if not inv:
                it.reversals.append(("4B2", Decimal(1), "Not in books — reverse until the invoice is received"))
            else:
                itc = rec.igst + rec.cgst + rec.sgst + rec.cess
                if inv.blocked_amount and itc:
                    it.reversals.append(("4B1", min(Decimal(1), inv.blocked_amount / itc), "Blocked under Sec 17(5)"))
                if inv.itc < itc - Decimal(1) and itc:
                    it.reversals.append(("4B2", (itc - inv.itc) / itc, "Supplier reported more tax than the invoice"))
                for rk in risks.get(inv.id, []):
                    if rk.kind in TEMPORARY and rk.status in ("open", "accepted", "reversed"):
                        if rk.kind == "R37_PAYMENT" and (not rk.due_date or rk.due_date >= period_end):
                            continue
                        frac = min(Decimal(1), rk.amount / itc) if itc else Decimal(1)
                        it.reversals.append(("4B2", frac, rk.title))
                    elif rk.status == "reclaimable" and itc:
                        it.reclaim = min(Decimal(1), rk.amount / itc)
        items.append(it)

    # Reverse-charge purchases come from the books, not 2B: the buyer pays this tax and claims it.
    for i in (await db.execute(select(Invoice).where(Invoice.client_id == client_id, Invoice.client_gstin == gstin,
                                                     Invoice.period == period, Invoice.reverse_charge.is_(True),
                                                     Invoice.status == "approved"))).scalars():
        items.append(Item(f"inv:{i.id}", f"{i.supplier_name or i.supplier_gstin} · {i.number} (RCM)", _amt(i), "4A3"))

    t = build(items)
    out = t.to_dict()
    out.update({"period": period, "gstin": gstin, "records": len(records),
                "included": sum(1 for it in items if it.row != "none"),
                "excluded_by_ims": sum(1 for r in records if (decisions.get(r.id) and
                                                               decisions[r.id].decided_action in ("reject", "pending")))})
    return out


async def finalise(db: AsyncSession, firm_id: uuid.UUID, client_id: uuid.UUID, gstin: str, period: str,
                   user_id: uuid.UUID | None) -> Table4Snapshot:
    data = await compute(db, client_id, gstin, period)
    snap = Table4Snapshot(firm_id=firm_id, client_id=client_id, client_gstin=gstin, period=period,
                          cells=data["cells"], sources=data["sources"], status="final", finalised_by=user_id)
    db.add(snap)
    # Record what was claimed (Sec 16(4) tracking) and which temporary reversals were made.
    claimed_records = {s["ref"][4:] for s in data["sources"].get("4A5", []) if s["ref"].startswith("rec:")}
    for m in (await db.execute(select(Match).where(Match.record_id.in_([uuid.UUID(x) for x in claimed_records]))
                               )).scalars():
        if m.invoice_id:
            inv = await db.get(Invoice, m.invoice_id)
            if inv and not inv.claimed_period:
                inv.claimed_period = period
    reversed_reasons = {s["reason"] for s in data["sources"].get("4B2", [])}
    for rk in (await db.execute(select(RiskItem).where(RiskItem.client_id == client_id,
                                                       RiskItem.kind.in_(TEMPORARY)))).scalars():
        if rk.title in reversed_reasons and rk.status in ("open", "accepted"):
            rk.status = "reversed"
        elif rk.status == "reclaimable":
            rk.status = "resolved"
            rk.resolved_at = datetime.now(UTC)
    await audit(db, firm_id, "table4.finalised", "table4", snap.id, actor_id=user_id, period=period, gstin=gstin,
                net=data["cells"]["4C"]["total"])
    return snap
