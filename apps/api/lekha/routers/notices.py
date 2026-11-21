from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from lekha.core.audit import audit
from lekha.core.deps import Ctx, get_ctx
from lekha.core.errors import NotFound
from lekha.core.storage import get_storage
from lekha.models import ClientGstin, Notice, NoticePack
from lekha.schemas.serialize import row, rows
from lekha.jobs.queue import enqueue
from lekha.services.notices import form_options

router = APIRouter(prefix="/api", tags=["notices"])


@router.get("/notices/forms")
async def forms() -> list[dict]:
    return form_options()


class NoticeIn(BaseModel):
    client_id: uuid.UUID
    gstin: str | None = None
    form: str = Field(pattern="^(DRC-01C|ASMT-10|DRC-01B|OTHER)$")
    reference: str | None = None
    period_from: str = Field(pattern=r"^\d{6}$")
    period_to: str = Field(pattern=r"^\d{6}$")
    amount: Decimal = Field(ge=0)
    received_on: date | None = None
    description: str = ""


@router.post("/notices")
async def create_notice(body: NoticeIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    await ctx.client(body.client_id)
    gstin = body.gstin or (await ctx.db.execute(select(ClientGstin.gstin).where(
        ClientGstin.client_id == body.client_id))).scalars().first()
    received = body.received_on or date.today()
    n = Notice(firm_id=ctx.firm_id, client_id=body.client_id, client_gstin=gstin, form=body.form,
               reference=body.reference, period_from=body.period_from, period_to=body.period_to, amount=body.amount,
               received_on=received, reply_due=received + timedelta(days=7 if body.form == "DRC-01C" else 30),
               description=body.description)
    ctx.db.add(n)
    await ctx.db.flush()
    await audit(ctx.db, ctx.firm_id, "notice.created", "notice", n.id, actor_id=ctx.user_id, form=n.form)
    # The pack (analysis + AI-polished reply + ZIP) is built by the worker; the page polls until it appears.
    await enqueue(ctx.db, "notice.pack", {"notice_id": str(n.id)}, firm_id=ctx.firm_id, max_attempts=2)
    return row(n)


@router.get("/notices")
async def list_notices(client_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> list[dict]:
    await ctx.client(client_id)
    return rows((await ctx.db.execute(select(Notice).where(Notice.client_id == client_id)
                                      .order_by(Notice.created_at.desc()))).scalars())


@router.get("/notices/{notice_id}")
async def get_notice(notice_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    n = await ctx.db.get(Notice, notice_id)
    if not n:
        raise NotFound("Notice not found.")
    await ctx.client(n.client_id)
    packs = list((await ctx.db.execute(select(NoticePack).where(NoticePack.notice_id == n.id)
                                       .order_by(NoticePack.version.desc()))).scalars())
    st = get_storage()
    return row(n, extra={"packs": [row(p, extra={"zip_url": st.signed_url(p.zip_key, 900) if p.zip_key else None})
                                   for p in packs]})


@router.post("/notices/{notice_id}/rebuild")
async def rebuild(notice_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    n = await ctx.db.get(Notice, notice_id)
    if not n:
        raise NotFound("Notice not found.")
    await ctx.client(n.client_id)
    await enqueue(ctx.db, "notice.pack", {"notice_id": str(n.id)}, firm_id=ctx.firm_id, max_attempts=2)
    return {"queued": True}


class PackPatch(BaseModel):
    reply_text: str


@router.patch("/notice-packs/{pack_id}")
async def edit_pack(pack_id: uuid.UUID, body: PackPatch, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    p = await ctx.db.get(NoticePack, pack_id)
    if not p:
        raise NotFound("Pack not found.")
    p.reply_text = body.reply_text
    return row(p)


