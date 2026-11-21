"""Documents (upload, status), invoices (review workstation), files and the public upload page."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.core.audit import audit
from lekha.core.db import set_tenant
from lekha.core.deps import Ctx, get_ctx, get_db
from lekha.core.errors import AppError, NotFound
from lekha.core.ratelimit import limiter
from lekha.core.security import hash_token
from lekha.core.storage import get_storage, verify_file_signature
from lekha.models import (
    AuditLog,
    Client,
    Document,
    Extraction,
    FieldEvidence,
    Invoice,
    InvoiceLine,
    Match,
    Payment,
    PublicLink,
    RiskItem,
    ValidationResult,
)
from lekha.schemas.serialize import page, row, rows
from lekha.services import invoices as S
from lekha.services.pipeline import ingest_document

router = APIRouter(prefix="/api", tags=["documents"])


def _doc_out(doc: Document) -> dict[str, Any]:
    out = row(doc, exclude=("ocr",))
    st = get_storage()
    out["page_urls"] = [st.signed_url(k, 3600) for k in (doc.page_images or [])]
    out["original_url"] = st.signed_url(doc.storage_key, 3600)
    return out


@router.post("/clients/{client_id}/documents")
async def upload(client_id: uuid.UUID, files: list[UploadFile] = File(...), source: str = Form("upload"),
                 ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_write()
    c = await ctx.client(client_id)
    if len(files) > 50:
        raise AppError("Upload at most 50 files at a time.")
    out, errors = [], []
    for f in files:
        data = await f.read()
        try:
            doc = await ingest_document(ctx.db, firm_id=ctx.firm_id, client_id=c.id, filename=f.filename or "file",
                                        data=data, declared_mime=f.content_type,
                                        source=source if source in ("upload", "camera", "api") else "upload",
                                        user_id=ctx.user_id)
            out.append(row(doc, exclude=("ocr",)))
        except AppError as e:
            errors.append({"filename": f.filename, "error": e.message})
    return {"documents": out, "errors": errors}


@router.get("/documents")
async def list_documents(ctx: Ctx = Depends(get_ctx), client_id: uuid.UUID | None = None, status: str | None = None,
                         limit: int = Query(50, le=200), offset: int = 0) -> dict:
    q = select(Document)
    if client_id:
        await ctx.client(client_id)
        q = q.where(Document.client_id == client_id)
    elif ctx.client_scope is not None:
        q = q.where(Document.client_id.in_(ctx.client_scope))
    if status:
        q = q.where(Document.status == status)
    total = (await ctx.db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    docs = list((await ctx.db.execute(q.order_by(Document.created_at.desc()).limit(limit).offset(offset))).scalars())
    inv_by_doc = {i.document_id: i for i in (await ctx.db.execute(
        select(Invoice).where(Invoice.document_id.in_([d.id for d in docs])))).scalars()}
    items = []
    for d in docs:
        o = row(d, exclude=("ocr", "page_images", "qr"))
        inv = inv_by_doc.get(d.id)
        o["invoice"] = {"id": str(inv.id), "status": inv.status, "number": inv.number,
                        "supplier_name": inv.supplier_name, "total": str(inv.total),
                        "confidence": inv.confidence} if inv else None
        items.append(o)
    return page(items, total, limit, offset)


@router.get("/documents/{document_id}")
async def get_document(document_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    doc = await ctx.db.get(Document, document_id)
    if not doc or not ctx.allows_client(doc.client_id):
        raise NotFound("Document not found.")
    return _doc_out(doc)


@router.post("/documents/{document_id}/retry")
async def retry_document(document_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_write()
    from lekha.jobs.queue import enqueue

    doc = await ctx.db.get(Document, document_id)
    if not doc or not ctx.allows_client(doc.client_id):
        raise NotFound("Document not found.")
    if doc.status not in ("failed",):
        raise AppError("Only failed documents can be retried.")
    doc.status = "queued"
    await enqueue(ctx.db, "document.process", {"document_id": str(doc.id)}, firm_id=ctx.firm_id)
    return {"ok": True}


# --- Invoices ------------------------------------------------------------------------------

@router.get("/invoices")
async def list_invoices(
    ctx: Ctx = Depends(get_ctx), client_id: uuid.UUID | None = None, status: str | None = None,
    period: str | None = None, supplier_gstin: str | None = None, q: str | None = None,
    sort: str = "-invoice_date", limit: int = Query(50, le=500), offset: int = 0,
) -> dict:
    stmt = select(Invoice)
    if client_id:
        await ctx.client(client_id)
        stmt = stmt.where(Invoice.client_id == client_id)
    elif ctx.client_scope is not None:
        stmt = stmt.where(Invoice.client_id.in_(ctx.client_scope))
    if status:
        stmt = stmt.where(Invoice.status.in_(status.split(",")))
    if period:
        stmt = stmt.where(Invoice.period == period)
    if supplier_gstin:
        stmt = stmt.where(Invoice.supplier_gstin == supplier_gstin)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Invoice.number.ilike(like), Invoice.supplier_name.ilike(like),
                              Invoice.supplier_gstin.ilike(like)))
    total = (await ctx.db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    col = getattr(Invoice, sort.lstrip("-"), Invoice.invoice_date)
    stmt = stmt.order_by(col.desc().nullslast() if sort.startswith("-") else col.asc().nullsfirst(),
                         Invoice.created_at.desc())
    items = (await ctx.db.execute(stmt.limit(limit).offset(offset))).scalars()
    return page([row(i, extra={"itc": i.itc}) for i in items], total, limit, offset)


@router.get("/invoices/review-queue")
async def review_queue(ctx: Ctx = Depends(get_ctx), client_id: uuid.UUID | None = None) -> list[dict]:
    """Invoices awaiting review, lowest confidence first (the order the workstation walks through)."""
    stmt = select(Invoice.id, Invoice.confidence).where(Invoice.status == "review")
    if client_id:
        await ctx.client(client_id)
        stmt = stmt.where(Invoice.client_id == client_id)
    elif ctx.client_scope is not None:
        stmt = stmt.where(Invoice.client_id.in_(ctx.client_scope))
    # Invoices with a document image first (that is where review adds value), then lowest confidence.
    rs = (await ctx.db.execute(stmt.order_by(Invoice.document_id.is_(None), Invoice.confidence.asc().nullsfirst(),
                                             Invoice.created_at))).all()
    return [{"id": str(r[0]), "confidence": r[1]} for r in rs]


@router.get("/invoices/{invoice_id}")
async def invoice_detail(invoice_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    inv = await S.get_invoice(ctx.db, invoice_id)
    if not ctx.allows_client(inv.client_id):
        raise NotFound("Invoice not found.")
    lines = (await ctx.db.execute(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id)
                                  .order_by(InvoiceLine.position))).scalars()
    ev = (await ctx.db.execute(select(FieldEvidence).where(FieldEvidence.invoice_id == inv.id))).scalars()
    val = (await ctx.db.execute(select(ValidationResult).where(ValidationResult.invoice_id == inv.id))).scalars()
    pays = (await ctx.db.execute(select(Payment).where(Payment.invoice_id == inv.id)
                                 .order_by(Payment.paid_on))).scalars()
    matches = (await ctx.db.execute(select(Match).where(Match.invoice_id == inv.id)
                                    .order_by(Match.created_at.desc()))).scalars()
    risks = (await ctx.db.execute(select(RiskItem).where(RiskItem.invoice_id == inv.id,
                                                         RiskItem.status == "open"))).scalars()
    trail = (await ctx.db.execute(select(AuditLog).where(AuditLog.entity_id.in_(
        [str(inv.id), str(inv.document_id)])).order_by(AuditLog.created_at))).scalars()
    doc = await ctx.db.get(Document, inv.document_id) if inv.document_id else None
    ext = (await ctx.db.execute(select(Extraction).where(Extraction.document_id == inv.document_id)
                                .order_by(Extraction.created_at.desc()))).scalars().first() if doc else None
    return {
        "invoice": row(inv, extra={"itc": inv.itc}),
        "lines": rows(lines),
        "evidence": {e.field: row(e) for e in ev},
        "validations": rows(val),
        "payments": rows(pays),
        "matches": rows(matches),
        "risks": rows(risks),
        "trail": rows(trail),
        "document": _doc_out(doc) | {"ocr": doc.ocr} if doc else None,
        "extraction": row(ext, exclude=("raw",)) | {"notes": (ext.raw or {}).get("notes")} if ext else None,
    }


class InvoicePatch(BaseModel):
    fields: dict[str, Any] = {}
    lines: list[dict[str, Any]] | None = None


@router.patch("/invoices/{invoice_id}")
async def patch_invoice(invoice_id: uuid.UUID, body: InvoicePatch, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_write()
    inv = await S.get_invoice(ctx.db, invoice_id)
    if not ctx.allows_client(inv.client_id):
        raise NotFound("Invoice not found.")
    await S.update_invoice(ctx.db, inv, body.fields, body.lines, ctx.user_id)
    await ctx.db.flush()
    return await invoice_detail(invoice_id, ctx)


class StatusIn(BaseModel):
    status: str
    reason: str | None = None


@router.post("/invoices/{invoice_id}/status")
async def invoice_status(invoice_id: uuid.UUID, body: StatusIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_write()
    inv = await S.get_invoice(ctx.db, invoice_id)
    if not ctx.allows_client(inv.client_id):
        raise NotFound("Invoice not found.")
    await S.set_status(ctx.db, inv, body.status, ctx.user_id, body.reason)
    return row(inv)


@router.post("/invoices/{invoice_id}/apply-fix/{validation_id}")
async def apply_fix(invoice_id: uuid.UUID, validation_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_write()
    inv = await S.get_invoice(ctx.db, invoice_id)
    v = await ctx.db.get(ValidationResult, validation_id)
    if not v or v.invoice_id != inv.id or not v.fix:
        raise NotFound("No fix available.")
    patch = {("invoice_date" if k == "date" else k): val for k, val in v.fix.items() if k != "reason"}
    await S.update_invoice(ctx.db, inv, patch, None, ctx.user_id)
    await ctx.db.flush()
    return await invoice_detail(invoice_id, ctx)


class ManualInvoiceIn(BaseModel):
    client_id: uuid.UUID
    fields: dict[str, Any]
    lines: list[dict[str, Any]] = []


@router.post("/invoices")
async def create_manual(body: ManualInvoiceIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    ctx.require_write()
    c = await ctx.client(body.client_id)
    inv = Invoice(firm_id=ctx.firm_id, client_id=c.id, source="manual", status="review")
    ctx.db.add(inv)
    await ctx.db.flush()
    await S.update_invoice(ctx.db, inv, body.fields, body.lines, ctx.user_id)
    await ctx.db.flush()
    return await invoice_detail(inv.id, ctx)


# --- Files (local storage only; S3 uses presigned URLs) --------------------------------------

@router.get("/files/{key:path}")
async def serve_file(key: str, exp: int, sig: str) -> Response:
    if not verify_file_signature(key, exp, sig):
        raise NotFound("Link expired.")
    data = await get_storage().get(key)
    mime = "application/pdf" if key.endswith(".pdf") else "image/png" if key.endswith(".png") else "image/jpeg"
    return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})


# --- Public upload page (no login) ----------------------------------------------------------

async def _link(db: AsyncSession, token: str, kind: str) -> PublicLink:
    await set_tenant(db, None, bypass=True)
    link = (await db.execute(select(PublicLink).where(PublicLink.token_hash == hash_token(token),
                                                      PublicLink.kind == kind))).scalar_one_or_none()
    if not link or link.revoked_at or (link.expires_at and link.expires_at < datetime.now(UTC)):
        raise NotFound("This link is invalid or has expired. Ask your accountant for a new one.")
    await set_tenant(db, link.firm_id)
    return link


@router.get("/public/upload/{token}")
async def public_upload_info(token: str, db: AsyncSession = Depends(get_db)) -> dict:
    link = await _link(db, token, "upload")
    client = await db.get(Client, link.client_id)
    recent = (await db.execute(select(Document).where(Document.client_id == link.client_id,
                                                      Document.source == "link")
                               .order_by(Document.created_at.desc()).limit(20))).scalars()
    from lekha.models import Firm

    await set_tenant(db, None, bypass=True)
    firm = await db.get(Firm, link.firm_id)
    return {"client_name": client.name if client else "", "firm_name": firm.name if firm else "",
            "recent": [{"filename": d.filename, "status": d.status, "created_at": d.created_at.isoformat()}
                       for d in recent]}


@router.post("/public/upload/{token}")
@limiter.limit("60/hour")
async def public_upload(request: Request, token: str, files: list[UploadFile] = File(...),
                        db: AsyncSession = Depends(get_db)) -> dict:
    link = await _link(db, token, "upload")
    if len(files) > 20:
        raise AppError("Upload at most 20 files at a time.")
    ok, errors = 0, []
    for f in files:
        try:
            await ingest_document(db, firm_id=link.firm_id, client_id=link.client_id, filename=f.filename or "file",
                                  data=await f.read(), declared_mime=f.content_type, source="link", user_id=None)
            ok += 1
        except AppError as e:
            errors.append({"filename": f.filename, "error": e.message})
    await set_tenant(db, None, bypass=True)
    link.use_count += ok
    await set_tenant(db, link.firm_id)
    await audit(db, link.firm_id, "link.upload", "public_link", link.id, actor_kind="public", files=ok)
    return {"uploaded": ok, "errors": errors}
