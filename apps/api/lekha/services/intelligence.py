"""Cross-client supplier intelligence and fraud signals (N3)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.engines import graph as GR
from lekha.models import ClientGstin, Document, GraphSignal, Invoice, Notification, RiskItem, Supplier


async def analyse_firm(db: AsyncSession, firm_id: uuid.UUID, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    client_gstins = {g.gstin: g.client_id for g in (await db.execute(select(ClientGstin))).scalars()}
    invoices = list((await db.execute(select(Invoice).where(Invoice.status.in_(("approved", "review")),
                                                            Invoice.supplier_gstin.is_not(None),
                                                            Invoice.client_gstin.is_not(None)))).scalars())
    layout = {d.id: d.layout_hash for d in (await db.execute(select(Document))).scalars()}
    edges = [GR.Edge(i.supplier_gstin, i.client_gstin, i.taxable_value, i.invoice_date, i.number,
                     layout.get(i.document_id), i.taxable_value >= 100000 and i.taxable_value % 10000 == 0)
             for i in invoices]
    first_seen = {s.gstin: s.first_seen for s in (await db.execute(select(Supplier))).scalars() if s.first_seen}
    signals = GR.find_cycles(edges) + GR.shell_scores(edges, first_seen, today, set(client_gstins)) + \
        GR.template_reuse(edges)

    # Cross-client: a supplier with open GST problems for one client puts every client buying from it at risk.
    problem: dict[str, set[uuid.UUID]] = defaultdict(set)
    amount: dict[str, Decimal] = defaultdict(Decimal)
    for r in (await db.execute(select(RiskItem).where(RiskItem.status == "open", RiskItem.kind.in_(
            ("NOT_IN_2B", "VALUE_MISMATCH", "WRONG_TAX_HEAD", "R37A_SUPPLIER_3B"))))).scalars():
        if r.supplier_gstin:
            problem[r.supplier_gstin].add(r.client_id)
            amount[r.supplier_gstin] += r.amount
    buyers: dict[str, set[uuid.UUID]] = defaultdict(set)
    for i in invoices:
        buyers[i.supplier_gstin].add(i.client_id)
    names = {s.gstin: s.trade_name or s.legal_name or s.gstin for s in (await db.execute(select(Supplier))).scalars()}
    for sup, bad_clients in problem.items():
        others = buyers.get(sup, set()) - bad_clients
        # Only material warnings: avoid alert fatigue from small, single-client issues.
        if others and (len(bad_clients) >= 2 or amount[sup] >= Decimal(25000)):
            signals.append(GR.Signal(
                "cross_client", min(1.0, 0.4 + 0.15 * len(bad_clients)),
                f"{names.get(sup, sup)} has GST problems with {len(bad_clients)} client(s)",
                f"Open issues worth ₹{amount[sup]:,.0f} with {len(bad_clients)} of your clients. "
                f"{len(others)} other client(s) also buy from this supplier — check their invoices before claiming.",
                [sup], amount[sup], {"problem_clients": [str(c) for c in bad_clients],
                                     "other_clients": [str(c) for c in others]}))

    existing = {s.fingerprint: s for s in (await db.execute(select(GraphSignal))).scalars()}
    seen = set()
    created = 0
    for s in signals:
        seen.add(s.fingerprint)
        affected = sorted({str(client_gstins[m]) for m in s.members if m in client_gstins} |
                          {str(c) for m in s.members for c in buyers.get(m, set())})
        row = existing.get(s.fingerprint)
        if row is None:
            row = GraphSignal(firm_id=firm_id, kind=s.kind, score=s.score, title=s.title, detail=s.detail,
                              members=s.members, evidence=_j(s.evidence), affected_clients=affected, amount=s.amount,
                              fingerprint=s.fingerprint)
            db.add(row)
            created += 1
            if s.score >= 0.6:
                db.add(Notification(firm_id=firm_id, kind="fraud_signal", title=s.title, body=s.detail[:500],
                                    link="/intelligence", severity="warning"))
        elif row.status != "dismissed":
            row.score, row.title, row.detail, row.evidence, row.affected_clients, row.amount = (
                s.score, s.title, s.detail, _j(s.evidence), affected, s.amount)
    for fp, row in existing.items():
        if fp not in seen and row.status == "open":
            row.status = "cleared"
    return {"signals": len(signals), "created": created}


def _j(v: Any) -> Any:
    from lekha.core.audit import jsonable

    return jsonable(v)
