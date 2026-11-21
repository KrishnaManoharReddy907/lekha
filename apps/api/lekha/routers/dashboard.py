"""Firm and client dashboards (M14)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from lekha.core.deps import Ctx, get_ctx
from lekha.engines.dates import add_months, fy_start_year, period_of, period_to_date
from lekha.engines.money import ZERO
from lekha.models import Client, CloseRun, Gstr2bImport, ImsDecision, Invoice, Match, RiskItem

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
ITC_KINDS = ("NOT_IN_2B", "VALUE_MISMATCH", "WRONG_TAX_HEAD", "R37_PAYMENT", "S16_4_DEADLINE", "R37A_SUPPLIER_3B",
             "NOT_RECEIVED", "S17_5_BLOCKED")


def _itc():
    return Invoice.igst + Invoice.cgst + Invoice.sgst + Invoice.cess


@router.get("/firm")
async def firm(ctx: Ctx = Depends(get_ctx)) -> dict:
    today = date.today()
    q = select(Client).where(Client.archived.is_(False))
    if ctx.client_scope is not None:
        q = q.where(Client.id.in_(ctx.client_scope))
    clients = list((await ctx.db.execute(q.order_by(Client.name))).scalars())
    fy_from = date(fy_start_year(today), 4, 1)
    approved = dict((await ctx.db.execute(select(Invoice.client_id, func.sum(_itc())).where(
        Invoice.status == "approved", Invoice.invoice_date >= fy_from).group_by(Invoice.client_id))).all())
    review = dict((await ctx.db.execute(select(Invoice.client_id, func.count()).where(Invoice.status == "review")
                                        .group_by(Invoice.client_id))).all())
    risk = dict((await ctx.db.execute(select(RiskItem.client_id, func.sum(RiskItem.amount)).where(
        RiskItem.status == "open", RiskItem.kind.in_(ITC_KINDS)).group_by(RiskItem.client_id))).all())
    latest: dict[uuid.UUID, str] = {}
    for cid, p in (await ctx.db.execute(select(Gstr2bImport.client_id, Gstr2bImport.period).where(
            Gstr2bImport.superseded.is_(False)))).all():
        if cid not in latest or (p[2:], p[:2]) > (latest[cid][2:], latest[cid][:2]):
            latest[cid] = p
    undecided = defaultdict(int)
    for cid, p, n in (await ctx.db.execute(select(ImsDecision.client_id, ImsDecision.period, func.count()).where(
            ImsDecision.decided_action.is_(None)).group_by(ImsDecision.client_id, ImsDecision.period))).all():
        if latest.get(cid) == p:
            undecided[cid] += n
    close = {}
    for r in (await ctx.db.execute(select(CloseRun).order_by(CloseRun.created_at))).scalars():
        close[r.client_id] = {"id": str(r.id), "status": r.status, "period": r.period}
    nextdue = dict((await ctx.db.execute(select(RiskItem.client_id, func.min(RiskItem.due_date)).where(
        RiskItem.status == "open", RiskItem.due_date >= today).group_by(RiskItem.client_id))).all())
    rows = []
    for c in clients:
        cur_period = period_of(add_months(today.replace(day=1), -1))
        stage = ("ready" if close.get(c.id, {}).get("status") == "approved" and close[c.id]["period"] == cur_period else
                 "awaiting_approval" if close.get(c.id, {}).get("status") == "awaiting_approval" else
                 "no_2b" if latest.get(c.id) != cur_period else "in_progress")
        rows.append({"client_id": str(c.id), "name": c.name, "itc_fy": str(approved.get(c.id) or ZERO),
                     "at_risk": str(risk.get(c.id) or ZERO), "in_review": review.get(c.id, 0),
                     "latest_2b": latest.get(c.id), "ims_undecided": undecided.get(c.id, 0), "close": close.get(c.id),
                     "next_due": nextdue.get(c.id).isoformat() if nextdue.get(c.id) else None, "stage": stage})
    # GST calendar for the current month.
    m = today.replace(day=1)
    prev = add_months(m, -1)
    cal = [{"date": m.replace(day=11).isoformat(), "what": f"GSTR-1 due for {prev:%b %Y} (suppliers report sales)"},
           {"date": m.replace(day=13).isoformat(), "what": f"Last day for IMS actions for {prev:%b %Y}"},
           {"date": m.replace(day=14).isoformat(), "what": f"GSTR-2B generated for {prev:%b %Y}"},
           {"date": m.replace(day=20).isoformat(), "what": f"GSTR-3B due for {prev:%b %Y}"}]
    if m.month == 11:
        cal.append({"date": m.replace(day=30).isoformat(), "what": f"Sec 16(4): last date to claim ITC of FY {m.year - 1}-{str(m.year)[2:]}"})
    return {"clients": rows, "calendar": cal, "totals": {
        "itc_fy": str(sum((Decimal(r["itc_fy"]) for r in rows), ZERO)),
        "at_risk": str(sum((Decimal(r["at_risk"]) for r in rows), ZERO)),
        "in_review": sum(r["in_review"] for r in rows)}}


@router.get("/client")
async def client_trend(client_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    await ctx.client(client_id)
    today = date.today()
    months = [add_months(today.replace(day=1), -k) for k in range(11, -1, -1)]
    periods = [period_of(m) for m in months]
    # One meaning per bar: the ITC of invoices dated in that month, split by whether a GSTR-2B record
    # (of any period) has been matched to the invoice yet.
    matched_ids = set((await ctx.db.execute(select(Match.invoice_id).where(
        Match.client_id == client_id, Match.status.in_(("matched", "mismatch")), Match.invoice_id.is_not(None))))
        .scalars())
    matched: dict[str, Decimal] = defaultdict(lambda: ZERO)
    missing: dict[str, Decimal] = defaultdict(lambda: ZERO)
    itc: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for inv_id, p, amount in (await ctx.db.execute(select(Invoice.id, Invoice.period, _itc()).where(
            Invoice.client_id == client_id, Invoice.status.in_(("approved", "review")), Invoice.period.in_(periods)))).all():
        itc[p] += amount
        if inv_id in matched_ids:
            matched[p] += amount
        else:
            missing[p] += amount
    trapped = (await ctx.db.execute(select(func.sum(RiskItem.amount)).where(
        RiskItem.client_id == client_id, RiskItem.status == "open", RiskItem.kind.in_(ITC_KINDS)))).scalar() or ZERO
    soon = list((await ctx.db.execute(select(RiskItem).where(
        RiskItem.client_id == client_id, RiskItem.status == "open", RiskItem.due_date.is_not(None),
        RiskItem.due_date <= today + timedelta(days=30)).order_by(RiskItem.due_date).limit(6))).scalars())
    return {"months": [{"period": p, "label": period_to_date(p).strftime("%b %y"), "itc": str(itc[p]),
                        "matched": str(matched[p]), "missing": str(missing[p])} for p in periods],
            "working_capital_trapped": str(trapped),
            "deadlines": [{"id": str(r.id), "title": r.title, "amount": str(r.amount), "due": r.due_date.isoformat(),
                           "kind": r.kind, "invoice_id": str(r.invoice_id) if r.invoice_id else None} for r in soon]}
