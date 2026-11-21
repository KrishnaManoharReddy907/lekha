"""GSTR-2B imports, reconciliation, IMS, risk ledger, forecasts, payments, Table 4, register import."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from lekha.core.audit import audit
from lekha.core.deps import Ctx, get_ctx
from lekha.core.errors import AppError, NotFound
from lekha.engines.ims import ims_deadline
from lekha.engines.money import d
from lekha.jobs.queue import enqueue
from lekha.models import (
    Forecast,
    Gstr2bImport,
    Gstr2bRecord,
    ImsDecision,
    Invoice,
    Match,
    Payment,
    RiskItem,
    Table4Snapshot,
)
from lekha.schemas.serialize import row, rows
from lekha.services import reconcile as REC
from lekha.services import risk as RISK
from lekha.services import table4 as T4
from lekha.services.register_import import import_register

router = APIRouter(prefix="/api", tags=["gst"])


# --- Periods & GSTR-2B --------------------------------------------------------------------------

@router.get("/clients/{client_id}/periods")
async def periods(client_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> list[dict]:
    await ctx.client(client_id)
    return await REC.periods_for_client(ctx.db, client_id)


@router.post("/clients/{client_id}/gstr2b")
async def upload_2b(client_id: uuid.UUID, file: UploadFile = File(...), period: str | None = Form(None),
                    gstin: str | None = Form(None), ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    await ctx.client(client_id)
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise AppError("GSTR-2B file is larger than 25 MB.")
    imp = await REC.import_gstr2b(ctx.db, firm_id=ctx.firm_id, client_id=client_id, filename=file.filename or "2b.json",
                                  data=data, period=period, gstin=gstin, user_id=ctx.user_id)
    return row(imp)


@router.get("/clients/{client_id}/gstr2b")
async def list_2b(client_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> list[dict]:
    await ctx.client(client_id)
    q = select(Gstr2bImport).where(Gstr2bImport.client_id == client_id).order_by(Gstr2bImport.created_at.desc())
    return rows((await ctx.db.execute(q)).scalars())


class ReconIn(BaseModel):
    period: str = Field(pattern=r"^\d{6}$")


@router.post("/clients/{client_id}/reconcile")
async def reconcile_now(client_id: uuid.UUID, body: ReconIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    await ctx.client(client_id)
    return await REC.run_reconciliation(ctx.db, ctx.firm_id, client_id, body.period, ctx.user_id)


@router.get("/reconciliation")
async def reconciliation(client_id: uuid.UUID, period: str, status: str | None = None,
                         ctx: Ctx = Depends(get_ctx)) -> dict:
    await ctx.client(client_id)
    q = select(Match).where(Match.client_id == client_id, Match.period == period)
    all_rows = list((await ctx.db.execute(q)).scalars())
    # Invoices still missing that were evaluated against this period live on with that period's run.
    inv_ids = [m.invoice_id for m in all_rows if m.invoice_id]
    rec_ids = [m.record_id for m in all_rows if m.record_id]
    invs = {i.id: i for i in (await ctx.db.execute(select(Invoice).where(Invoice.id.in_(inv_ids)))).scalars()}
    recs = {r.id: r for r in (await ctx.db.execute(select(Gstr2bRecord).where(Gstr2bRecord.id.in_(rec_ids)))).scalars()}
    decs = {x.record_id: x for x in (await ctx.db.execute(select(ImsDecision).where(
        ImsDecision.record_id.in_(rec_ids)))).scalars()}
    fc = {f.invoice_id: f for f in (await ctx.db.execute(select(Forecast).where(Forecast.invoice_id.in_(inv_ids)))).scalars()}
    counts: dict[str, int] = {}
    itc = {"books": Decimal(0), "gstr2b": Decimal(0), "matched": Decimal(0)}
    out = []
    for m in all_rows:
        counts[m.status] = counts.get(m.status, 0) + 1
        itc["books"] += m.itc_books
        itc["gstr2b"] += m.itc_2b
        if m.status == "matched":
            itc["matched"] += m.itc_books
        if status and m.status != status:
            continue
        i, r = invs.get(m.invoice_id), recs.get(m.record_id)
        out.append(row(m, extra={
            "invoice": {"id": str(i.id), "number": i.number, "date": i.invoice_date, "supplier_name": i.supplier_name,
                        "supplier_gstin": i.supplier_gstin, "taxable_value": i.taxable_value, "itc": i.itc,
                        "status": i.status} if i else None,
            "record": {"id": str(r.id), "number": r.number, "date": r.invoice_date, "supplier_name": r.supplier_name,
                       "supplier_gstin": r.supplier_gstin, "taxable_value": r.taxable_value, "itc": r.itc,
                       "itc_available": r.itc_available, "reason": r.itc_unavailable_reason,
                       "doc_type": r.doc_type} if r else None,
            "ims": row(decs[m.record_id]) if m.record_id in decs else None,
            "forecast": row(fc[m.invoice_id]) if m.invoice_id in fc else None,
        }))
    out.sort(key=lambda x: (["missing_in_2b", "mismatch", "missing_in_books", "matched"].index(x["status"])
                            if x["status"] in ("missing_in_2b", "mismatch", "missing_in_books", "matched") else 9,
                            -(float(x["itc_books"]) + float(x["itc_2b"]))))
    imp = (await ctx.db.execute(select(Gstr2bImport).where(Gstr2bImport.client_id == client_id,
                                                           Gstr2bImport.period == period,
                                                           Gstr2bImport.superseded.is_(False)))).scalars().first()
    return {"period": period, "counts": counts, "itc": {k: str(v) for k, v in itc.items()}, "items": out,
            "import": row(imp) if imp else None, "ims_deadline": ims_deadline(period).isoformat()}


class ManualMatchIn(BaseModel):
    invoice_id: uuid.UUID
    record_id: uuid.UUID


@router.post("/matches/manual")
async def manual_match(body: ManualMatchIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    inv = await ctx.db.get(Invoice, body.invoice_id)
    rec = await ctx.db.get(Gstr2bRecord, body.record_id)
    if not inv or not rec or inv.client_id != rec.client_id:
        raise NotFound("Invoice or record not found.")
    await ctx.client(inv.client_id)
    await ctx.db.execute(delete(Match).where(Match.client_id == inv.client_id, Match.period == rec.period,
                                             (Match.invoice_id == inv.id) | (Match.record_id == rec.id)))
    await ctx.db.execute(delete(Match).where(Match.invoice_id == inv.id, Match.status == "missing_in_2b"))
    from lekha.engines.matching import _classify, explain, features, score
    from lekha.services.reconcile import _inv_doc, _rec_doc

    f = features(_inv_doc(inv), _rec_doc(rec), Decimal("1.00"))
    _kind, types, status = _classify(_inv_doc(inv), _rec_doc(rec), f, Decimal("1.00"))
    m = Match(firm_id=ctx.firm_id, client_id=inv.client_id, client_gstin=rec.client_gstin, period=rec.period,
              invoice_id=inv.id, record_id=rec.id, kind="manual", status=status, mismatch_types=types, score=score(f),
              features=f, explanation=explain(_inv_doc(inv), _rec_doc(rec), f, types, Decimal("1.00")) |
              {"manual": True}, itc_books=inv.itc, itc_2b=rec.itc if rec.itc_available else Decimal(0),
              confirmed_by=ctx.user_id)
    ctx.db.add(m)
    await ctx.db.flush()
    await audit(ctx.db, ctx.firm_id, "match.manual", "match", m.id, actor_id=ctx.user_id, invoice=str(inv.id),
                record=str(rec.id))
    await enqueue(ctx.db, "client.refresh", {"client_id": str(inv.client_id)}, firm_id=ctx.firm_id,
                  dedupe_key=f"refresh:{inv.client_id}")
    return row(m)


# --- IMS ---------------------------------------------------------------------------------------

@router.get("/ims")
async def ims_list(client_id: uuid.UUID, period: str, ctx: Ctx = Depends(get_ctx)) -> dict:
    await ctx.client(client_id)
    pairs = (await ctx.db.execute(select(Gstr2bRecord, ImsDecision).join(
        ImsDecision, ImsDecision.record_id == Gstr2bRecord.id, isouter=True).where(
        Gstr2bRecord.client_id == client_id, Gstr2bRecord.period == period))).all()
    items = [row(r, exclude=("raw",), extra={"itc": r.itc, "decision": row(dd) if dd else None}) for r, dd in pairs]
    summary: dict[str, dict[str, Any]] = {}
    for r, dd in pairs:
        eff = (dd.decided_action if dd and dd.decided_action else None) or "no_action"
        s = summary.setdefault(eff, {"count": 0, "itc": Decimal(0)})
        s["count"] += 1
        s["itc"] += r.itc
    rec_summary: dict[str, int] = {}
    for _r, dd in pairs:
        if dd:
            rec_summary[dd.recommendation] = rec_summary.get(dd.recommendation, 0) + 1
    return {"items": items, "deadline": ims_deadline(period).isoformat(),
            "summary": {k: {"count": v["count"], "itc": str(v["itc"])} for k, v in summary.items()},
            "recommended": rec_summary}


class DecideIn(BaseModel):
    record_ids: list[uuid.UUID]
    action: str | None
    remark: str | None = None


@router.post("/ims/decide")
async def ims_decide(body: DecideIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_write()
    n = await REC.decide_ims(ctx.db, ctx.firm_id, body.record_ids, body.action, ctx.user_id, body.remark)
    return {"updated": n}


@router.get("/ims/export")
async def ims_export(client_id: uuid.UUID, period: str, ctx: Ctx = Depends(get_ctx)) -> Response:
    """CSV of IMS actions (decided, else recommended) to apply on the GST portal or its offline tool."""
    await ctx.client(client_id)
    pairs = (await ctx.db.execute(select(Gstr2bRecord, ImsDecision).join(
        ImsDecision, ImsDecision.record_id == Gstr2bRecord.id, isouter=True).where(
        Gstr2bRecord.client_id == client_id, Gstr2bRecord.period == period))).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Supplier GSTIN", "Supplier name", "Document type", "Document number", "Document date",
                "Taxable value", "Total tax", "Action", "Action source", "Reason"])
    for r, dd in pairs:
        action = (dd.decided_action if dd and dd.decided_action else (dd.recommendation if dd else "pending"))
        w.writerow([r.supplier_gstin, r.supplier_name or "", r.doc_type, r.number,
                    r.invoice_date.strftime("%d-%m-%Y") if r.invoice_date else "", f"{r.taxable_value:.2f}",
                    f"{r.itc:.2f}", action.upper(), "decided" if dd and dd.decided_action else "recommended",
                    (dd.remark or dd.reason_text) if dd else ""])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="ims-actions-{period}.csv"'})


# --- Risk ledger & forecasts -------------------------------------------------------------------

@router.get("/risk")
async def risk_ledger(client_id: uuid.UUID, include_resolved: bool = False, ctx: Ctx = Depends(get_ctx)) -> dict:
    await ctx.client(client_id)
    led = await RISK.ledger(ctx.db, client_id, include_resolved)
    return {"items": rows(led["items"]), "totals": led["totals"]}


class RiskStatusIn(BaseModel):
    status: str = Field(pattern="^(open|accepted|resolved)$")


@router.post("/risk/{risk_id}/status")
async def risk_status(risk_id: uuid.UUID, body: RiskStatusIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_write()
    r = await ctx.db.get(RiskItem, risk_id)
    if not r:
        raise NotFound("Risk item not found.")
    await ctx.client(r.client_id)
    r.status = body.status
    await audit(ctx.db, ctx.firm_id, "risk.status", "risk_item", r.id, actor_id=ctx.user_id, status=body.status)
    return row(r)


@router.post("/clients/{client_id}/refresh")
async def refresh(client_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_write()
    await ctx.client(client_id)
    from lekha.services.refresh import refresh_client

    return await refresh_client(ctx.db, ctx.firm_id, client_id)


@router.get("/forecasts")
async def forecasts(client_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> list[dict]:
    await ctx.client(client_id)
    pairs = (await ctx.db.execute(select(Forecast, Invoice).join(Invoice, Invoice.id == Forecast.invoice_id).where(
        Invoice.client_id == client_id).order_by(Forecast.p_by_deadline))).all()
    return [row(f, extra={"invoice": {"id": str(i.id), "number": i.number, "supplier_name": i.supplier_name,
                                      "itc": i.itc, "period": i.period, "date": i.invoice_date}}) for f, i in pairs]


# --- Payments ----------------------------------------------------------------------------------

class PaymentIn(BaseModel):
    amount: Decimal = Field(gt=0)
    paid_on: date
    mode: str | None = None
    reference: str | None = None


@router.post("/invoices/{invoice_id}/payments")
async def add_payment(invoice_id: uuid.UUID, body: PaymentIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_write()
    inv = await ctx.db.get(Invoice, invoice_id)
    if not inv:
        raise NotFound("Invoice not found.")
    await ctx.client(inv.client_id)
    p = Payment(firm_id=ctx.firm_id, invoice_id=inv.id, amount=d(body.amount), paid_on=body.paid_on, mode=body.mode,
                reference=body.reference, created_by=ctx.user_id)
    ctx.db.add(p)
    await ctx.db.flush()
    await audit(ctx.db, ctx.firm_id, "payment.added", "invoice", inv.id, actor_id=ctx.user_id, amount=str(p.amount))
    await enqueue(ctx.db, "client.refresh", {"client_id": str(inv.client_id)}, firm_id=ctx.firm_id,
                  dedupe_key=f"refresh:{inv.client_id}")
    return row(p)


@router.delete("/payments/{payment_id}")
async def delete_payment(payment_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_write()
    p = await ctx.db.get(Payment, payment_id)
    if not p:
        raise NotFound("Payment not found.")
    inv = await ctx.db.get(Invoice, p.invoice_id)
    await ctx.client(inv.client_id)
    await ctx.db.delete(p)
    await enqueue(ctx.db, "client.refresh", {"client_id": str(inv.client_id)}, firm_id=ctx.firm_id,
                  dedupe_key=f"refresh:{inv.client_id}")
    return {"ok": True}


@router.get("/payables")
async def payables(client_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> list[dict]:
    """Unpaid approved invoices with aging — what to pay first to protect ITC and the income-tax deduction."""
    await ctx.client(client_id)
    invs = list((await ctx.db.execute(select(Invoice).where(Invoice.client_id == client_id,
                                                            Invoice.status == "approved"))).scalars())
    paid = dict((await ctx.db.execute(select(Payment.invoice_id, func.sum(Payment.amount)).where(
        Payment.invoice_id.in_([i.id for i in invs])).group_by(Payment.invoice_id))).all())
    today = date.today()
    out = []
    for i in invs:
        due = i.total - (paid.get(i.id) or 0)
        if due <= 1 or not i.invoice_date:
            continue
        out.append({"invoice_id": str(i.id), "number": i.number, "supplier_name": i.supplier_name,
                    "supplier_gstin": i.supplier_gstin, "date": i.invoice_date.isoformat(), "total": str(i.total),
                    "unpaid": str(due), "age_days": (today - i.invoice_date).days,
                    "days_to_180": 180 - (today - i.invoice_date).days, "itc": str(i.itc)})
    out.sort(key=lambda x: x["days_to_180"])
    return out


# --- Table 4 -----------------------------------------------------------------------------------

@router.get("/table4")
async def table4(client_id: uuid.UUID, gstin: str, period: str, ctx: Ctx = Depends(get_ctx)) -> dict:
    await ctx.client(client_id)
    data = await T4.compute(ctx.db, client_id, gstin, period)
    snap = (await ctx.db.execute(select(Table4Snapshot).where(
        Table4Snapshot.client_id == client_id, Table4Snapshot.client_gstin == gstin, Table4Snapshot.period == period,
        Table4Snapshot.status == "final").order_by(Table4Snapshot.created_at.desc()))).scalars().first()
    data["finalised"] = row(snap) if snap else None
    return data


class FinaliseIn(BaseModel):
    gstin: str
    period: str


@router.post("/clients/{client_id}/table4/finalise")
async def table4_finalise(client_id: uuid.UUID, body: FinaliseIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    await ctx.client(client_id)
    snap = await T4.finalise(ctx.db, ctx.firm_id, client_id, body.gstin, body.period, ctx.user_id)
    return row(snap)


# --- Purchase register import ------------------------------------------------------------------

@router.post("/clients/{client_id}/register-import")
async def register_import(client_id: uuid.UUID, file: UploadFile = File(...), ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    await ctx.client(client_id)
    return await import_register(ctx.db, ctx.firm_id, client_id, file.filename or "register.csv", await file.read(),
                                 ctx.user_id)


