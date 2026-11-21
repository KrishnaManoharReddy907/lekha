"""Fraud and supplier-network signals, notifications."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from lekha.core.audit import audit
from lekha.core.deps import Ctx, get_ctx
from lekha.core.errors import NotFound
from lekha.models import GraphSignal, Notification
from lekha.schemas.serialize import row, rows
from lekha.services.intelligence import analyse_firm

router = APIRouter(prefix="/api", tags=["intelligence"])


@router.get("/signals")
async def signals(ctx: Ctx = Depends(get_ctx), status: str = "open") -> list[dict]:
    q = select(GraphSignal).order_by(GraphSignal.score.desc())
    if status != "all":
        q = q.where(GraphSignal.status == status)
    out = list((await ctx.db.execute(q)).scalars())
    if ctx.client_scope is not None:
        allowed = {str(c) for c in ctx.client_scope}
        out = [s for s in out if set(s.affected_clients or []) & allowed]
    return rows(out)


@router.post("/intelligence/run")
async def run(ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    return await analyse_firm(ctx.db, ctx.firm_id)


class SignalStatus(BaseModel):
    status: str = Field(pattern="^(open|dismissed|confirmed)$")


@router.post("/signals/{signal_id}/status")
async def signal_status(signal_id: uuid.UUID, body: SignalStatus, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    s = await ctx.db.get(GraphSignal, signal_id)
    if not s:
        raise NotFound("Signal not found.")
    s.status = body.status
    await audit(ctx.db, ctx.firm_id, "signal.status", "graph_signal", s.id, actor_id=ctx.user_id, status=body.status)
    return row(s)


@router.get("/notifications")
async def notifications(ctx: Ctx = Depends(get_ctx), unread: bool = False) -> list[dict]:
    q = select(Notification).where((Notification.user_id.is_(None)) | (Notification.user_id == ctx.user_id))
    if unread:
        q = q.where(Notification.read_at.is_(None))
    return rows((await ctx.db.execute(q.order_by(Notification.created_at.desc()).limit(50))).scalars())


@router.post("/notifications/read")
async def mark_read(ctx: Ctx = Depends(get_ctx)) -> dict:
    for n in (await ctx.db.execute(select(Notification).where(Notification.read_at.is_(None)))).scalars():
        n.read_at = datetime.now(UTC)
    return {"ok": True}
