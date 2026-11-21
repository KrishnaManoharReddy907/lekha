"""One-click private demo workspace for visitors.

Starting a demo is instant (user, firm and session); the 12 months of data are built by the
background worker so no proxy or hosting timeout can interrupt it. The page polls /status.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.core.db import set_tenant
from lekha.core.deps import Ctx, get_ctx, get_db
from lekha.core.ratelimit import limiter
from lekha.jobs.queue import enqueue
from lekha.models import Firm, Job
from lekha.routers.auth import _me, _start_session
from lekha.services.demo import create_demo_shell

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.post("/start")
@limiter.limit("6/hour")
async def start_demo(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    user, firm = await create_demo_shell(db)
    await enqueue(db, "demo.populate", {"user_id": str(user.id)}, firm_id=firm.id, max_attempts=2)
    await set_tenant(db, None, bypass=True)
    sess = await _start_session(db, response, request, user, firm.id)
    return await _me(db, user, sess)


@router.get("/status")
async def demo_status(ctx: Ctx = Depends(get_ctx)) -> dict:
    await set_tenant(ctx.db, None, bypass=True)
    firm = await ctx.db.get(Firm, ctx.firm_id)
    status = (firm.settings or {}).get("demo_status", "ready") if firm and firm.is_demo else "ready"
    from sqlalchemy import select

    failed = (await ctx.db.execute(select(Job).where(Job.firm_id == ctx.firm_id, Job.kind == "demo.populate",
                                                     Job.status == "dead"))).scalars().first()
    return {"status": "failed" if failed else status}
