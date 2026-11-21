"""One-click private demo workspace for visitors."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.core.deps import get_db
from lekha.core.ratelimit import limiter
from lekha.routers.auth import _me, _start_session
from lekha.services.demo import create_demo

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.post("/start")
@limiter.limit("6/hour")
async def start_demo(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    user, firm = await create_demo(db)
    from lekha.core.db import set_tenant

    await set_tenant(db, None, bypass=True)
    sess = await _start_session(db, response, request, user, firm.id)
    return await _me(db, user, sess)
