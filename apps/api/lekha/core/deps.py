"""Request context: who is calling, for which firm, with what role — inside a tenant-scoped transaction."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.core.db import sessionmaker, set_tenant
from lekha.core.errors import Forbidden, NotFound, Unauthorized
from lekha.core.security import constant_eq, hash_token
from lekha.models import ApiKey, Client, Membership, Session, User

SESSION_COOKIE = "lekha_session"
WRITE_ROLES = {"owner", "partner", "staff", "client"}
ADMIN_ROLES = {"owner", "partner"}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class Ctx:
    db: AsyncSession
    firm_id: uuid.UUID
    role: str
    user_id: uuid.UUID | None = None
    user: User | None = None
    via: str = "session"  # session | api_key
    client_scope: list[uuid.UUID] | None = None  # None = all clients
    extras: dict = field(default_factory=dict)

    @property
    def actor_kind(self) -> str:
        return "api" if self.via == "api_key" else "user"

    def require(self, *roles: str) -> None:
        if self.role not in roles:
            raise Forbidden("Your role does not allow this action.")

    def require_write(self) -> None:
        if self.role not in WRITE_ROLES:
            raise Forbidden("Viewers cannot make changes.")

    def require_admin(self) -> None:
        if self.role not in ADMIN_ROLES and self.role != "owner":
            raise Forbidden("Only owners and partners can do this.")

    def allows_client(self, client_id: uuid.UUID) -> bool:
        return self.client_scope is None or client_id in self.client_scope

    async def client(self, client_id: uuid.UUID) -> Client:
        if not self.allows_client(client_id):
            raise NotFound("Client not found.")
        c = await self.db.get(Client, client_id)
        if not c or c.firm_id != self.firm_id:
            raise NotFound("Client not found.")
        return c


async def _resolve(request: Request, db: AsyncSession) -> Ctx:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer lk_"):
        key = auth.split(" ", 1)[1].strip()
        await set_tenant(db, None, bypass=True)
        row = (await db.execute(select(ApiKey).where(ApiKey.key_hash == hash_token(key)))).scalar_one_or_none()
        if not row or row.revoked_at:
            raise Unauthorized("Invalid API key.")
        row.last_used_at = datetime.now(UTC)
        await set_tenant(db, row.firm_id)
        return Ctx(db=db, firm_id=row.firm_id, role=row.role, user_id=row.created_by, via="api_key")

    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise Unauthorized("Please sign in.")
    await set_tenant(db, None, bypass=True)
    sess = (await db.execute(select(Session).where(Session.token_hash == hash_token(token)))).scalar_one_or_none()
    if not sess or sess.expires_at < datetime.now(UTC):
        raise Unauthorized("Your session has expired. Please sign in again.")
    if request.method in UNSAFE:
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not constant_eq(sent, sess.csrf_token):
            raise Forbidden("Missing or invalid CSRF token.", code="csrf")
    user = await db.get(User, sess.user_id)
    if not user:
        raise Unauthorized("Please sign in.")
    if not sess.firm_id:
        raise Forbidden("Create or join a workspace first.", code="no_firm")
    m = (await db.execute(select(Membership).where(Membership.firm_id == sess.firm_id,
                                                   Membership.user_id == user.id))).scalar_one_or_none()
    if not m:
        raise Forbidden("You are no longer a member of this workspace.")
    await set_tenant(db, sess.firm_id)
    scope = [uuid.UUID(str(c)) for c in m.client_ids] if m.role == "client" else None
    return Ctx(db=db, firm_id=sess.firm_id, role=m.role, user_id=user.id, user=user, client_scope=scope,
               extras={"session": sess})


async def get_db() -> AsyncIterator[AsyncSession]:
    async with sessionmaker()() as db, db.begin():
        yield db


async def get_ctx(request: Request, db: AsyncSession = Depends(get_db)) -> Ctx:
    ctx = await _resolve(request, db)
    request.state.firm_id = ctx.firm_id
    return ctx


async def get_user_session(request: Request, db: AsyncSession = Depends(get_db)) -> tuple[User, Session]:
    """For endpoints that need a signed-in user but not (yet) a workspace."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise Unauthorized("Please sign in.")
    await set_tenant(db, None, bypass=True)
    sess = (await db.execute(select(Session).where(Session.token_hash == hash_token(token)))).scalar_one_or_none()
    if not sess or sess.expires_at < datetime.now(UTC):
        raise Unauthorized("Your session has expired. Please sign in again.")
    if request.method in UNSAFE:
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not constant_eq(sent, sess.csrf_token):
            raise Forbidden("Missing or invalid CSRF token.", code="csrf")
    user = await db.get(User, sess.user_id)
    if not user:
        raise Unauthorized("Please sign in.")
    return user, sess
