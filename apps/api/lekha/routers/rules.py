from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from lekha.core.deps import Ctx, get_ctx
from lekha.core.errors import NotFound
from lekha.models import RuleImpactReport, RuleVersionRow
from lekha.schemas.serialize import row
from lekha.services import rules_admin as R

router = APIRouter(prefix="/api/rules", tags=["rules"])


@router.get("")
async def list_rules(ctx: Ctx = Depends(get_ctx)) -> list[dict]:
    return await R.list_rules(ctx.db, ctx.firm_id)


class DraftIn(BaseModel):
    code: str
    params: dict[str, Any]
    effective_from: date
    effective_to: date | None = None
    title: str = ""
    source: str = Field("", max_length=300)


@router.post("/drafts")
async def create_draft(body: DraftIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_admin()
    d = await R.create_draft(ctx.db, ctx.firm_id, body.code, body.params, body.effective_from, body.effective_to,
                             body.title, body.source, ctx.user_id)
    return row(d)


@router.delete("/drafts/{draft_id}")
async def delete_draft(draft_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_admin()
    d = await ctx.db.get(RuleVersionRow, draft_id)
    if not d or d.status != "draft" or d.firm_id != ctx.firm_id:
        raise NotFound("Draft not found.")
    await ctx.db.delete(d)
    return {"ok": True}


class ImpactIn(BaseModel):
    draft_ids: list[uuid.UUID]


@router.post("/impact")
async def impact(body: ImpactIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_admin()
    rep = await R.start_impact(ctx.db, ctx.firm_id, body.draft_ids)
    return row(rep)


@router.get("/impact/{report_id}")
async def get_impact(report_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    rep = await ctx.db.get(RuleImpactReport, report_id)
    if not rep:
        raise NotFound("Report not found.")
    return row(rep)


@router.post("/drafts/{draft_id}/publish")
async def publish(draft_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_admin()
    return row(await R.publish(ctx.db, ctx.firm_id, draft_id, ctx.user_id))
