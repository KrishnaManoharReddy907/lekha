"""Supplier hub: master, scorecards, follow-ups, and the supplier's own self-service page."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.core.audit import audit
from lekha.core.db import set_tenant
from lekha.core.deps import Ctx, get_ctx, get_db
from lekha.core.errors import AppError, NotFound
from lekha.core.ratelimit import limiter
from lekha.core.security import hash_token
from lekha.models import Client, Firm, FollowUp, Invoice, PublicLink, RiskItem, Supplier, SupplierResponse
from lekha.schemas.serialize import row, rows
from lekha.services import suppliers as S

router = APIRouter(prefix="/api", tags=["suppliers"])


@router.get("/suppliers")
async def list_suppliers(ctx: Ctx = Depends(get_ctx), client_id: uuid.UUID | None = None, q: str | None = None) -> list[dict]:
    sups = list((await ctx.db.execute(select(Supplier).order_by(Supplier.score.asc().nullslast()))).scalars())
    stats = {g: (n, t) for g, n, t in (await ctx.db.execute(select(
        Invoice.supplier_gstin, func.count(), func.sum(Invoice.igst + Invoice.cgst + Invoice.sgst + Invoice.cess))
        .where(*( [Invoice.client_id == client_id] if client_id else []), Invoice.status.in_(("approved", "review")))
        .group_by(Invoice.supplier_gstin))).all()}
    risk = {g: a for g, a in (await ctx.db.execute(select(RiskItem.supplier_gstin, func.sum(RiskItem.amount)).where(
        RiskItem.status == "open", *([RiskItem.client_id == client_id] if client_id else []),
        RiskItem.kind.in_(S.CHASE_KINDS)).group_by(RiskItem.supplier_gstin))).all()}
    out = []
    for s in sups:
        if client_id and s.gstin not in stats:
            continue
        if q and q.lower() not in f"{s.gstin} {s.trade_name or ''} {s.legal_name or ''}".lower():
            continue
        n, itc = stats.get(s.gstin, (0, 0))
        out.append(row(s, extra={"invoices": n, "itc": itc or 0, "at_risk": risk.get(s.gstin) or 0}))
    return out


@router.get("/suppliers/{supplier_id}")
async def supplier_detail(supplier_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    s = await ctx.db.get(Supplier, supplier_id)
    if not s:
        raise NotFound("Supplier not found.")
    invs = list((await ctx.db.execute(select(Invoice).where(Invoice.supplier_gstin == s.gstin)
                                      .order_by(Invoice.invoice_date.desc()).limit(200))).scalars())
    if ctx.client_scope is not None:
        invs = [i for i in invs if i.client_id in ctx.client_scope]
    clients = {c.id: c.name for c in (await ctx.db.execute(select(Client))).scalars()}
    issues = await S.open_issues(ctx.db, s.gstin)
    fus = (await ctx.db.execute(select(FollowUp).where(FollowUp.supplier_id == s.id)
                                .order_by(FollowUp.created_at.desc()))).scalars()
    resp = (await ctx.db.execute(select(SupplierResponse).where(SupplierResponse.supplier_id == s.id)
                                 .order_by(SupplierResponse.created_at.desc()))).scalars()
    return {"supplier": row(s),
            "invoices": [row(i, extra={"itc": i.itc, "client_name": clients.get(i.client_id)}) for i in invs],
            "clients": sorted({clients.get(i.client_id) for i in invs if clients.get(i.client_id)}),
            "issues": issues, "followups": rows(fus), "responses": rows(resp)}


class SupplierPatch(BaseModel):
    email: EmailStr | None = None
    phone: str | None = None
    language: str | None = Field(None, pattern="^(en|hi|te)$")
    msme_category: str | None = Field(None, pattern="^(micro|small|medium|none)$")
    udyam_number: str | None = None
    has_payment_agreement: bool | None = None
    gstr3b_filed: bool | None = None
    notes: str | None = None


@router.patch("/suppliers/{supplier_id}")
async def patch_supplier(supplier_id: uuid.UUID, body: SupplierPatch, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    s = await ctx.db.get(Supplier, supplier_id)
    if not s:
        raise NotFound("Supplier not found.")
    data = body.model_dump(exclude_unset=True)
    if "gstr3b_filed" in data:
        s.score_detail = {**(s.score_detail or {}), "gstr3b_filed": data.pop("gstr3b_filed")}
    if data.get("msme_category") == "none":
        data["msme_category"] = None
    for k, v in data.items():
        setattr(s, k, v)
    await audit(ctx.db, ctx.firm_id, "supplier.updated", "supplier", s.id, actor_id=ctx.user_id, fields=list(body.model_fields_set))
    from lekha.jobs.queue import enqueue

    for cid in set((await ctx.db.execute(select(Invoice.client_id).where(Invoice.supplier_gstin == s.gstin))).scalars()):
        await enqueue(ctx.db, "client.refresh", {"client_id": str(cid)}, firm_id=ctx.firm_id, dedupe_key=f"refresh:{cid}")
    return row(s)


class FollowUpIn(BaseModel):
    client_id: uuid.UUID
    channel: str = Field("email", pattern="^(email|whatsapp|phone)$")
    language: str | None = Field(None, pattern="^(en|hi|te)$")


@router.post("/suppliers/{supplier_id}/followups")
async def create_followup(supplier_id: uuid.UUID, body: FollowUpIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    await ctx.client(body.client_id)
    await set_tenant(ctx.db, None, bypass=True)
    firm = await ctx.db.get(Firm, ctx.firm_id)
    await set_tenant(ctx.db, ctx.firm_id)
    f = await S.draft_followup(ctx.db, ctx.firm_id, supplier_id, body.client_id, firm.name if firm else "", ctx.user_id,
                               body.channel, body.language)
    return row(f)


class FollowUpPatch(BaseModel):
    subject: str | None = None
    body: str | None = None
    status: str | None = Field(None, pattern="^(sent|responded|resolved)$")


@router.patch("/followups/{followup_id}")
async def patch_followup(followup_id: uuid.UUID, body: FollowUpPatch, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    f = await ctx.db.get(FollowUp, followup_id)
    if not f:
        raise NotFound("Follow-up not found.")
    if body.subject is not None:
        f.subject = body.subject
    if body.body is not None:
        f.body = body.body
    if body.status == "sent":
        await S.mark_sent(ctx.db, f, ctx.user_id)
    elif body.status:
        f.status = body.status
    return row(f)


@router.get("/followups")
async def list_followups(ctx: Ctx = Depends(get_ctx), client_id: uuid.UUID | None = None) -> list[dict]:
    q = select(FollowUp, Supplier).join(Supplier, Supplier.id == FollowUp.supplier_id)
    if client_id:
        q = q.where(FollowUp.client_id == client_id)
    return [row(f, extra={"supplier_name": s.trade_name or s.legal_name or s.gstin, "supplier_gstin": s.gstin})
            for f, s in (await ctx.db.execute(q.order_by(FollowUp.created_at.desc()))).all()]


# --- Supplier self-service page (public, token) ------------------------------------------------

async def _supplier_link(db: AsyncSession, token: str) -> PublicLink:
    await set_tenant(db, None, bypass=True)
    link = (await db.execute(select(PublicLink).where(PublicLink.token_hash == hash_token(token),
                                                      PublicLink.kind == "supplier"))).scalar_one_or_none()
    if not link or link.revoked_at or (link.expires_at and link.expires_at < datetime.now(UTC)):
        raise NotFound("This link is invalid or has expired.")
    await set_tenant(db, link.firm_id)
    return link


@router.get("/public/supplier/{token}")
async def public_supplier(token: str, db: AsyncSession = Depends(get_db)) -> dict:
    link = await _supplier_link(db, token)
    s = await db.get(Supplier, link.supplier_id)
    issues = await S.open_issues(db, s.gstin)
    clients = {c.id: c for c in (await db.execute(select(Client))).scalars()}
    from lekha.models import ClientGstin

    cg = {}
    for g in (await db.execute(select(ClientGstin))).scalars():
        cg.setdefault(g.client_id, g.gstin)
    resp = (await db.execute(select(SupplierResponse).where(SupplierResponse.supplier_id == s.id))).scalars()
    await set_tenant(db, None, bypass=True)
    firm = await db.get(Firm, link.firm_id)
    link.use_count += 1
    return {
        "firm_name": firm.name if firm else "", "supplier_name": s.trade_name or s.legal_name or s.gstin,
        "supplier_gstin": s.gstin,
        "issues": [{**x, "buyer_name": clients[uuid.UUID(x["client_id"])].name if uuid.UUID(x["client_id"]) in clients
                    else "", "buyer_gstin": cg.get(uuid.UUID(x["client_id"]))} for x in issues],
        "responses": [{"item_ref": r.item_ref, "action": r.action, "message": r.message,
                       "expected_by": r.expected_by.isoformat() if r.expected_by else None} for r in resp],
    }


class SupplierReply(BaseModel):
    item_ref: str
    action: str = Field(pattern="^(will_file|filed|amended|disputed)$")
    message: str = Field("", max_length=2000)
    expected_by: date | None = None


@router.post("/public/supplier/{token}")
@limiter.limit("60/hour")
async def public_supplier_reply(request: Request, token: str, body: SupplierReply,
                                db: AsyncSession = Depends(get_db)) -> dict:
    link = await _supplier_link(db, token)
    s = await db.get(Supplier, link.supplier_id)
    issues = {x["risk_id"]: x for x in await S.open_issues(db, s.gstin)}
    if body.item_ref not in issues:
        raise AppError("This item is no longer open.")
    db.add(SupplierResponse(firm_id=link.firm_id, supplier_id=s.id, item_ref=body.item_ref, action=body.action,
                            message=body.message, expected_by=body.expected_by))
    for f in (await db.execute(select(FollowUp).where(FollowUp.supplier_id == s.id,
                                                      FollowUp.status.in_(("sent", "escalated"))))).scalars():
        f.status = "responded"
        f.response = {"action": body.action, "at": datetime.now(UTC).isoformat()}
    from lekha.models import Notification

    db.add(Notification(firm_id=link.firm_id, kind="supplier_response",
                        title=f"{s.trade_name or s.gstin} replied: {body.action.replace('_', ' ')}",
                        body=body.message[:300], link=f"/suppliers/{s.id}", severity="info"))
    await audit(db, link.firm_id, "supplier.responded", "supplier", s.id, actor_kind="public", response=body.action)
    return {"ok": True}
