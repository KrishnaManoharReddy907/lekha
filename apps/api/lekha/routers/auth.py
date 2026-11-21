from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.core.audit import audit
from lekha.core.config import get_settings
from lekha.core.db import set_tenant
from lekha.core.deps import SESSION_COOKIE, get_db, get_user_session
from lekha.core.errors import AppError, Conflict, NotFound, Unauthorized
from lekha.core.ratelimit import limiter
from lekha.core.security import hash_password, hash_token, new_token, password_problems, verify_password
from lekha.models import Firm, Invite, Membership, Session, User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SignupIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)
    firm_name: str | None = Field(None, max_length=200)
    firm_kind: str = Field("ca_firm", pattern="^(ca_firm|business)$")
    invite_token: str | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


async def _start_session(db: AsyncSession, response: Response, request: Request, user: User,
                         firm_id) -> Session:
    s = get_settings()
    token = new_token()
    sess = Session(token_hash=hash_token(token), user_id=user.id, firm_id=firm_id, csrf_token=new_token(24),
                   expires_at=datetime.now(UTC) + timedelta(days=s.session_days),
                   user_agent=(request.headers.get("user-agent") or "")[:400])
    db.add(sess)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=s.is_prod, samesite="lax",
                        max_age=s.session_days * 86400, path="/")
    user.last_login_at = datetime.now(UTC)
    return sess


async def _me(db: AsyncSession, user: User, sess: Session) -> dict:
    await set_tenant(db, None, bypass=True)
    rows = (await db.execute(select(Membership, Firm).join(Firm, Firm.id == Membership.firm_id)
                             .where(Membership.user_id == user.id))).all()
    firms = [{"id": str(f.id), "name": f.name, "kind": f.kind, "role": m.role, "is_demo": f.is_demo}
             for m, f in rows]
    current = next((f for f in firms if f["id"] == str(sess.firm_id)), None)
    return {"user": {"id": str(user.id), "name": user.name, "email": user.email, "language": user.language},
            "firm": current, "firms": firms, "csrf_token": sess.csrf_token}


@router.post("/signup")
@limiter.limit("10/hour")
async def signup(request: Request, body: SignupIn, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    await set_tenant(db, None, bypass=True)
    problems = password_problems(body.password)
    if problems:
        raise AppError(" ".join(problems), code="weak_password")
    email = body.email.lower()
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise Conflict("An account with this email already exists. Sign in instead.")
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password))
    db.add(user)
    await db.flush()
    firm_id = None
    if body.invite_token:
        inv = (await db.execute(select(Invite).where(Invite.token_hash == hash_token(body.invite_token)))).scalar_one_or_none()
        if not inv or inv.accepted_at or inv.expires_at < datetime.now(UTC):
            raise AppError("This invitation is invalid or has expired.", code="bad_invite")
        if inv.email.lower() != email:
            raise AppError("This invitation was sent to a different email address.", code="bad_invite")
        db.add(Membership(firm_id=inv.firm_id, user_id=user.id, role=inv.role, client_ids=inv.client_ids))
        inv.accepted_at = datetime.now(UTC)
        firm_id = inv.firm_id
    elif body.firm_name:
        firm = Firm(name=body.firm_name.strip(), kind=body.firm_kind)
        db.add(firm)
        await db.flush()
        db.add(Membership(firm_id=firm.id, user_id=user.id, role="owner"))
        firm_id = firm.id
    await db.flush()
    sess = await _start_session(db, response, request, user, firm_id)
    if firm_id:
        await set_tenant(db, firm_id)
        await audit(db, firm_id, "user.signup", "user", user.id, actor_id=user.id)
        await set_tenant(db, None, bypass=True)
    return await _me(db, user, sess)


@router.post("/login")
@limiter.limit("20/15minutes")
async def login(request: Request, body: LoginIn, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    await set_tenant(db, None, bypass=True)
    user = (await db.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise Unauthorized("Email or password is incorrect.")
    m = (await db.execute(select(Membership).where(Membership.user_id == user.id)
                          .order_by(Membership.created_at))).scalars().first()
    sess = await _start_session(db, response, request, user, m.firm_id if m else None)
    return await _me(db, user, sess)


@router.post("/logout")
async def logout(response: Response, us=Depends(get_user_session), db: AsyncSession = Depends(get_db)) -> dict:
    _user, sess = us
    await set_tenant(db, None, bypass=True)
    s = await db.get(Session, sess.id)
    if s:
        await db.delete(s)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(us=Depends(get_user_session), db: AsyncSession = Depends(get_db)) -> dict:
    user, sess = us
    return await _me(db, user, sess)


class SwitchIn(BaseModel):
    firm_id: str


@router.post("/switch")
async def switch(body: SwitchIn, us=Depends(get_user_session), db: AsyncSession = Depends(get_db)) -> dict:
    user, sess = us
    await set_tenant(db, None, bypass=True)
    m = (await db.execute(select(Membership).where(Membership.user_id == user.id,
                                                   Membership.firm_id == body.firm_id))).scalar_one_or_none()
    if not m:
        raise NotFound("Workspace not found.")
    s = await db.get(Session, sess.id)
    s.firm_id = m.firm_id
    await db.flush()
    return await _me(db, user, s)


class FirmIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field("ca_firm", pattern="^(ca_firm|business)$")


@router.post("/firms")
async def create_firm(body: FirmIn, us=Depends(get_user_session), db: AsyncSession = Depends(get_db)) -> dict:
    user, sess = us
    await set_tenant(db, None, bypass=True)
    firm = Firm(name=body.name.strip(), kind=body.kind)
    db.add(firm)
    await db.flush()
    db.add(Membership(firm_id=firm.id, user_id=user.id, role="owner"))
    s = await db.get(Session, sess.id)
    s.firm_id = firm.id
    await db.flush()
    return await _me(db, user, s)


@router.get("/invite/{token}")
async def invite_info(token: str, db: AsyncSession = Depends(get_db)) -> dict:
    await set_tenant(db, None, bypass=True)
    inv = (await db.execute(select(Invite).where(Invite.token_hash == hash_token(token)))).scalar_one_or_none()
    if not inv or inv.accepted_at or inv.expires_at < datetime.now(UTC):
        raise NotFound("This invitation is invalid or has expired.")
    firm = await db.get(Firm, inv.firm_id)
    return {"email": inv.email, "role": inv.role, "firm_name": firm.name if firm else ""}


@router.post("/invite/{token}/accept")
async def accept_invite(token: str, us=Depends(get_user_session), db: AsyncSession = Depends(get_db)) -> dict:
    user, sess = us
    await set_tenant(db, None, bypass=True)
    inv = (await db.execute(select(Invite).where(Invite.token_hash == hash_token(token)))).scalar_one_or_none()
    if not inv or inv.accepted_at or inv.expires_at < datetime.now(UTC):
        raise NotFound("This invitation is invalid or has expired.")
    if inv.email.lower() != user.email:
        raise AppError("This invitation was sent to a different email address.")
    exists = (await db.execute(select(Membership).where(Membership.firm_id == inv.firm_id,
                                                        Membership.user_id == user.id))).scalar_one_or_none()
    if not exists:
        db.add(Membership(firm_id=inv.firm_id, user_id=user.id, role=inv.role, client_ids=inv.client_ids))
    inv.accepted_at = datetime.now(UTC)
    s = await db.get(Session, sess.id)
    s.firm_id = inv.firm_id
    await db.flush()
    return await _me(db, user, s)
