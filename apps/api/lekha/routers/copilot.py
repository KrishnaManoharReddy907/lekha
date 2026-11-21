from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from lekha.core.deps import Ctx, get_ctx
from lekha.core.ratelimit import limiter
from lekha.models import CopilotMessage
from lekha.schemas.serialize import rows
from lekha.services.copilot import ask

router = APIRouter(prefix="/api/copilot", tags=["copilot"])


class AskIn(BaseModel):
    client_id: uuid.UUID
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = None
    language: str = Field("en", pattern="^(en|hi|te)$")


@router.post("")
@limiter.limit("30/minute")
async def ask_copilot(request: Request, body: AskIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    await ctx.client(body.client_id)
    return await ask(ctx.db, ctx.firm_id, body.client_id, ctx.user_id, body.message, body.conversation_id, body.language)


@router.get("/{conversation_id}")
async def history(conversation_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> list[dict]:
    q = select(CopilotMessage).where(CopilotMessage.conversation_id == conversation_id).order_by(CopilotMessage.created_at)
    msgs = list((await ctx.db.execute(q)).scalars())
    if msgs and msgs[0].client_id and not ctx.allows_client(msgs[0].client_id):
        return []
    return rows(msgs)
