from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from lekha.core.deps import Ctx, get_ctx
from lekha.core.errors import NotFound
from lekha.models import CloseRun, CloseStep
from lekha.schemas.serialize import row, rows
from lekha.services import close as C

router = APIRouter(prefix="/api", tags=["close"])


class StartIn(BaseModel):
    period: str = Field(pattern=r"^\d{6}$")
    gstin: str | None = None


@router.post("/clients/{client_id}/close")
async def start(client_id: uuid.UUID, body: StartIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    await ctx.client(client_id)
    run = await C.start_close(ctx.db, ctx.firm_id, client_id, body.period, body.gstin, "manual", ctx.user_id)
    return row(run)


@router.get("/close-runs")
async def list_runs(client_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> list[dict]:
    await ctx.client(client_id)
    q = select(CloseRun).where(CloseRun.client_id == client_id).order_by(CloseRun.created_at.desc()).limit(24)
    return rows((await ctx.db.execute(q)).scalars())


@router.get("/close-runs/{run_id}")
async def get_run(run_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    run = await ctx.db.get(CloseRun, run_id)
    if not run:
        raise NotFound("Close run not found.")
    await ctx.client(run.client_id)
    steps = (await ctx.db.execute(select(CloseStep).where(CloseStep.run_id == run.id).order_by(CloseStep.position))).scalars()
    return row(run, extra={"steps": rows(steps)})


@router.post("/close-runs/{run_id}/steps/{code}/approve")
async def approve(run_id: uuid.UUID, code: str, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    run = await ctx.db.get(CloseRun, run_id)
    if not run:
        raise NotFound("Close run not found.")
    await ctx.client(run.client_id)
    run = await C.approve_step(ctx.db, ctx.firm_id, run_id, code, ctx.user_id)
    return await get_run(run.id, ctx)
