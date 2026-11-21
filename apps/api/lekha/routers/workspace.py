"""Firm settings, team, invites, clients, GSTINs, API keys, public upload links, audit log."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select

from lekha.core.audit import audit, snapshot
from lekha.core.config import get_settings
from lekha.core.db import set_tenant
from lekha.core.deps import Ctx, get_ctx
from lekha.core.errors import AppError, Conflict, NotFound
from lekha.core.security import hash_token, new_api_key, new_token
from lekha.engines import gstin as G
from lekha.models import ApiKey, AuditLog, Client, ClientGstin, Firm, Invite, Membership, PublicLink, User
from lekha.schemas.serialize import page, row, rows

router = APIRouter(prefix="/api", tags=["workspace"])


# --- Firm & team ---------------------------------------------------------------------------

@router.get("/firm")
async def get_firm(ctx: Ctx = Depends(get_ctx)) -> dict:
    await set_tenant(ctx.db, None, bypass=True)
    firm = await ctx.db.get(Firm, ctx.firm_id)
    members = (await ctx.db.execute(select(Membership, User).join(User, User.id == Membership.user_id)
                                    .where(Membership.firm_id == ctx.firm_id))).all()
    invites = (await ctx.db.execute(select(Invite).where(Invite.firm_id == ctx.firm_id,
                                                         Invite.accepted_at.is_(None)))).scalars()
    await set_tenant(ctx.db, ctx.firm_id)
    return {
        "firm": row(firm),
        "role": ctx.role,
        "members": [{"user_id": str(u.id), "name": u.name, "email": u.email, "role": m.role,
                     "client_ids": m.client_ids, "membership_id": str(m.id)} for m, u in members],
        "invites": [row(i, exclude=("token_hash",)) for i in invites],
    }


class FirmPatch(BaseModel):
    name: str | None = Field(None, max_length=200)
    settings: dict | None = None


@router.patch("/firm")
async def patch_firm(body: FirmPatch, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_admin()
    await set_tenant(ctx.db, None, bypass=True)
    firm = await ctx.db.get(Firm, ctx.firm_id)
    before = snapshot(firm, ["name", "settings"])
    if body.name:
        firm.name = body.name
    if body.settings is not None:
        firm.settings = {**(firm.settings or {}), **body.settings}
    await set_tenant(ctx.db, ctx.firm_id)
    await audit(ctx.db, ctx.firm_id, "firm.updated", "firm", firm.id, actor_id=ctx.user_id, before=before,
                after=snapshot(firm, ["name", "settings"]))
    return row(firm)


class InviteIn(BaseModel):
    email: EmailStr
    role: str = Field(pattern="^(partner|staff|client|viewer)$")
    client_ids: list[uuid.UUID] = []


@router.post("/invites")
async def create_invite(body: InviteIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_admin()
    if body.role == "client" and not body.client_ids:
        raise AppError("Choose which clients this person can see.")
    token = new_token()
    await set_tenant(ctx.db, None, bypass=True)
    inv = Invite(firm_id=ctx.firm_id, email=body.email.lower(), role=body.role,
                 client_ids=[str(c) for c in body.client_ids], token_hash=hash_token(token), invited_by=ctx.user_id,
                 expires_at=datetime.now(UTC) + timedelta(days=7))
    ctx.db.add(inv)
    await ctx.db.flush()
    await set_tenant(ctx.db, ctx.firm_id)
    await audit(ctx.db, ctx.firm_id, "invite.created", "invite", inv.id, actor_id=ctx.user_id, email=inv.email,
                role=inv.role)
    url = f"{get_settings().web_origin}/invite/{token}"
    return {"invite": row(inv, exclude=("token_hash",)), "url": url}


@router.delete("/invites/{invite_id}")
async def revoke_invite(invite_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_admin()
    await set_tenant(ctx.db, None, bypass=True)
    inv = await ctx.db.get(Invite, invite_id)
    if not inv or inv.firm_id != ctx.firm_id:
        raise NotFound("Invite not found.")
    await ctx.db.delete(inv)
    return {"ok": True}


class MemberPatch(BaseModel):
    role: str | None = Field(None, pattern="^(owner|partner|staff|client|viewer)$")
    client_ids: list[uuid.UUID] | None = None


@router.patch("/members/{membership_id}")
async def patch_member(membership_id: uuid.UUID, body: MemberPatch, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_admin()
    await set_tenant(ctx.db, None, bypass=True)
    m = await ctx.db.get(Membership, membership_id)
    if not m or m.firm_id != ctx.firm_id:
        raise NotFound("Member not found.")
    if m.role == "owner" and body.role and body.role != "owner":
        owners = (await ctx.db.execute(select(func.count()).select_from(Membership).where(
            Membership.firm_id == ctx.firm_id, Membership.role == "owner"))).scalar_one()
        if owners <= 1:
            raise AppError("A workspace needs at least one owner.")
    if body.role:
        m.role = body.role
    if body.client_ids is not None:
        m.client_ids = [str(c) for c in body.client_ids]
    await set_tenant(ctx.db, ctx.firm_id)
    await audit(ctx.db, ctx.firm_id, "member.updated", "membership", m.id, actor_id=ctx.user_id, role=m.role)
    return {"ok": True}


@router.delete("/members/{membership_id}")
async def remove_member(membership_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_admin()
    await set_tenant(ctx.db, None, bypass=True)
    m = await ctx.db.get(Membership, membership_id)
    if not m or m.firm_id != ctx.firm_id:
        raise NotFound("Member not found.")
    if m.user_id == ctx.user_id:
        raise AppError("You cannot remove yourself.")
    await ctx.db.delete(m)
    await set_tenant(ctx.db, ctx.firm_id)
    await audit(ctx.db, ctx.firm_id, "member.removed", "membership", membership_id, actor_id=ctx.user_id)
    return {"ok": True}


# --- Clients & GSTINs ----------------------------------------------------------------------

class GstinIn(BaseModel):
    gstin: str
    legal_name: str | None = None
    aato: int | None = None


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    trade_name: str | None = None
    contact_name: str | None = None
    contact_email: EmailStr | None = None
    contact_phone: str | None = None
    language: str = Field("en", pattern="^(en|hi|te)$")
    filing_frequency: str = Field("monthly", pattern="^(monthly|quarterly)$")
    gstins: list[GstinIn] = []


async def _client_payload(ctx: Ctx, c: Client) -> dict:
    gst = (await ctx.db.execute(select(ClientGstin).where(ClientGstin.client_id == c.id))).scalars()
    return row(c, extra={"gstins": rows(gst)})


@router.get("/clients")
async def list_clients(ctx: Ctx = Depends(get_ctx), include_archived: bool = False) -> list[dict]:
    q = select(Client).order_by(Client.name)
    if not include_archived:
        q = q.where(Client.archived.is_(False))
    if ctx.client_scope is not None:
        q = q.where(Client.id.in_(ctx.client_scope))
    clients = list((await ctx.db.execute(q)).scalars())
    gst = list((await ctx.db.execute(select(ClientGstin))).scalars())
    by: dict = {}
    for g in gst:
        by.setdefault(g.client_id, []).append(row(g))
    return [row(c, extra={"gstins": by.get(c.id, [])}) for c in clients]


async def _add_gstin(ctx: Ctx, client: Client, g: GstinIn, primary: bool) -> ClientGstin:
    chk = G.validate(g.gstin)
    if not chk.valid:
        raise AppError(f"GSTIN {chk.value} is not valid: " + "; ".join(chk.problems), code="invalid_gstin")
    dup = (await ctx.db.execute(select(ClientGstin).where(ClientGstin.gstin == chk.value))).scalar_one_or_none()
    if dup:
        raise Conflict(f"GSTIN {chk.value} already belongs to another client in this workspace.")
    cg = ClientGstin(firm_id=ctx.firm_id, client_id=client.id, gstin=chk.value, state_code=chk.value[:2],
                     legal_name=g.legal_name, is_primary=primary, aato=g.aato)
    ctx.db.add(cg)
    return cg


@router.post("/clients")
async def create_client(body: ClientIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    c = Client(firm_id=ctx.firm_id, **body.model_dump(exclude={"gstins"}))
    ctx.db.add(c)
    await ctx.db.flush()
    for i, g in enumerate(body.gstins):
        await _add_gstin(ctx, c, g, i == 0)
    await ctx.db.flush()
    await audit(ctx.db, ctx.firm_id, "client.created", "client", c.id, actor_id=ctx.user_id, name=c.name)
    return await _client_payload(ctx, c)


@router.get("/clients/{client_id}")
async def get_client(client_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    return await _client_payload(ctx, await ctx.client(client_id))


class ClientPatch(BaseModel):
    name: str | None = Field(None, max_length=200)
    trade_name: str | None = None
    contact_name: str | None = None
    contact_email: EmailStr | None = None
    contact_phone: str | None = None
    language: str | None = Field(None, pattern="^(en|hi|te)$")
    filing_frequency: str | None = Field(None, pattern="^(monthly|quarterly)$")
    archived: bool | None = None
    settings: dict | None = None


@router.patch("/clients/{client_id}")
async def patch_client(client_id: uuid.UUID, body: ClientPatch, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    c = await ctx.client(client_id)
    fields = body.model_dump(exclude_unset=True)
    before = snapshot(c, list(fields))
    for k, v in fields.items():
        if k == "settings" and v is not None:
            v = {**(c.settings or {}), **v}
        setattr(c, k, v)
    await audit(ctx.db, ctx.firm_id, "client.updated", "client", c.id, actor_id=ctx.user_id, before=before,
                after=snapshot(c, list(fields)))
    return await _client_payload(ctx, c)


@router.post("/clients/{client_id}/gstins")
async def add_gstin(client_id: uuid.UUID, body: GstinIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    c = await ctx.client(client_id)
    has = (await ctx.db.execute(select(func.count()).select_from(ClientGstin)
                                .where(ClientGstin.client_id == c.id))).scalar_one()
    cg = await _add_gstin(ctx, c, body, has == 0)
    await ctx.db.flush()
    return row(cg)


@router.delete("/clients/{client_id}/gstins/{gstin_id}")
async def delete_gstin(client_id: uuid.UUID, gstin_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner")
    await ctx.client(client_id)
    cg = await ctx.db.get(ClientGstin, gstin_id)
    if not cg or cg.client_id != client_id:
        raise NotFound("GSTIN not found.")
    await ctx.db.delete(cg)
    return {"ok": True}


# --- Public upload links -------------------------------------------------------------------

class LinkIn(BaseModel):
    label: str | None = None
    days: int = Field(90, ge=1, le=365)


@router.post("/clients/{client_id}/upload-links")
async def create_upload_link(client_id: uuid.UUID, body: LinkIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    c = await ctx.client(client_id)
    token = new_token(18)
    link = PublicLink(firm_id=ctx.firm_id, kind="upload", token_hash=hash_token(token), client_id=c.id,
                      label=body.label or f"Upload link for {c.name}",
                      expires_at=datetime.now(UTC) + timedelta(days=body.days), created_by=ctx.user_id)
    await set_tenant(ctx.db, None, bypass=True)
    ctx.db.add(link)
    await ctx.db.flush()
    await set_tenant(ctx.db, ctx.firm_id)
    await audit(ctx.db, ctx.firm_id, "link.created", "public_link", link.id, actor_id=ctx.user_id, kind="upload")
    return {"link": row(link, exclude=("token_hash",)), "url": f"{get_settings().web_origin}/u/{token}"}


@router.get("/clients/{client_id}/upload-links")
async def list_upload_links(client_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> list[dict]:
    await ctx.client(client_id)
    await set_tenant(ctx.db, None, bypass=True)
    links = (await ctx.db.execute(select(PublicLink).where(PublicLink.firm_id == ctx.firm_id,
                                                           PublicLink.client_id == client_id,
                                                           PublicLink.kind == "upload"))).scalars()
    out = rows(links, exclude=("token_hash",))
    await set_tenant(ctx.db, ctx.firm_id)
    return out


@router.delete("/links/{link_id}")
async def revoke_link(link_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require("owner", "partner", "staff")
    await set_tenant(ctx.db, None, bypass=True)
    link = await ctx.db.get(PublicLink, link_id)
    if not link or link.firm_id != ctx.firm_id:
        raise NotFound("Link not found.")
    link.revoked_at = datetime.now(UTC)
    await set_tenant(ctx.db, ctx.firm_id)
    return {"ok": True}


# --- API keys ------------------------------------------------------------------------------

class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: str = Field("staff", pattern="^(staff|viewer)$")


@router.get("/api-keys")
async def list_keys(ctx: Ctx = Depends(get_ctx)) -> list[dict]:
    ctx.require_admin()
    await set_tenant(ctx.db, None, bypass=True)
    keys = (await ctx.db.execute(select(ApiKey).where(ApiKey.firm_id == ctx.firm_id))).scalars()
    out = rows(keys, exclude=("key_hash",))
    await set_tenant(ctx.db, ctx.firm_id)
    return out


@router.post("/api-keys")
async def create_key(body: KeyIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_admin()
    full, prefix, h = new_api_key()
    await set_tenant(ctx.db, None, bypass=True)
    k = ApiKey(firm_id=ctx.firm_id, name=body.name, prefix=prefix, key_hash=h, role=body.role, created_by=ctx.user_id)
    ctx.db.add(k)
    await ctx.db.flush()
    await set_tenant(ctx.db, ctx.firm_id)
    await audit(ctx.db, ctx.firm_id, "api_key.created", "api_key", k.id, actor_id=ctx.user_id, name=body.name)
    return {"key": full, "record": row(k, exclude=("key_hash",))}


@router.delete("/api-keys/{key_id}")
async def revoke_key(key_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_admin()
    await set_tenant(ctx.db, None, bypass=True)
    k = await ctx.db.get(ApiKey, key_id)
    if not k or k.firm_id != ctx.firm_id:
        raise NotFound("Key not found.")
    k.revoked_at = datetime.now(UTC)
    await set_tenant(ctx.db, ctx.firm_id)
    await audit(ctx.db, ctx.firm_id, "api_key.revoked", "api_key", k.id, actor_id=ctx.user_id)
    return {"ok": True}


# --- Audit log -----------------------------------------------------------------------------

@router.get("/audit")
async def audit_log(ctx: Ctx = Depends(get_ctx), entity: str | None = None, entity_id: str | None = None,
                    limit: int = Query(50, le=200), offset: int = 0) -> dict:
    ctx.require("owner", "partner", "staff")
    q = select(AuditLog)
    if entity:
        q = q.where(AuditLog.entity == entity)
    if entity_id:
        q = q.where(AuditLog.entity_id == entity_id)
    total = (await ctx.db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = (await ctx.db.execute(q.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset))).scalars()
    return page(rows(items), total, limit, offset)
