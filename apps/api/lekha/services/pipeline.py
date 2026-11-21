"""Document → invoice pipeline (M2–M4): OCR, QR, extraction, memory, validation, confidence, persistence."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.ai.client import AIRefused, AIUnavailable, ai_enabled, provider
from lekha.ai.extract import PROMPT_VERSION, extract_with_claude
from lekha.ai.offline import extract_offline
from lekha.core.audit import audit
from lekha.core.config import get_settings
from lekha.core.errors import AppError
from lekha.core.storage import get_storage
from lekha.engines import confidence as C
from lekha.engines import gstin as G
from lekha.engines import qr as Q
from lekha.engines.dates import period_of
from lekha.engines.invoice import InvoiceData, normalise_number
from lekha.engines.money import ZERO
from lekha.engines.states import parse_place_of_supply
from lekha.engines.validation import ERROR, ValidationContext, blocked_reason, validate_invoice
from lekha.jobs.queue import enqueue
from lekha.models import (
    ClientGstin,
    Document,
    Extraction,
    FieldEvidence,
    Invoice,
    InvoiceLine,
    Supplier,
    SupplierMemory,
    ValidationResult,
)
from lekha.ocr import pages as P
from lekha.services.rulebook import rulebook_for

log = structlog.get_logger("pipeline")


# ---------------------------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------------------------

async def ingest_document(
    db: AsyncSession,
    *,
    firm_id: uuid.UUID,
    client_id: uuid.UUID,
    filename: str,
    data: bytes,
    declared_mime: str | None,
    source: str,
    user_id: uuid.UUID | None,
) -> Document:
    s = get_settings()
    if len(data) == 0:
        raise AppError("The file is empty.")
    if len(data) > s.max_upload_mb * 1024 * 1024:
        raise AppError(f"File is larger than {s.max_upload_mb} MB.")
    mime = P.sniff_mime(data, declared_mime)
    if mime not in P.ALLOWED_MIME:
        raise AppError("Only PDF, JPG, PNG, WEBP and HEIC files are supported.", code="unsupported_type")
    sha = hashlib.sha256(data).hexdigest()
    existing = (
        await db.execute(select(Document).where(Document.client_id == client_id, Document.sha256 == sha,
                                                Document.status != "failed"))
    ).scalars().first()
    doc_id = uuid.uuid4()
    ext = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
           "image/heic": "heic"}[mime]
    key = f"firms/{firm_id}/docs/{doc_id}/original.{ext}"
    doc = Document(id=doc_id, firm_id=firm_id, client_id=client_id, storage_key=key, filename=filename[:300],
                   mime=mime, size=len(data), sha256=sha, source=source, uploaded_by=user_id)
    if existing:
        doc.status = "duplicate"
        doc.duplicate_of = existing.id
        doc.storage_key = existing.storage_key
        db.add(doc)
        await db.flush()
        return doc
    await get_storage().put(key, data, mime)
    db.add(doc)
    await db.flush()
    await enqueue(db, "document.process", {"document_id": str(doc.id)}, firm_id=firm_id,
                  dedupe_key=f"doc:{doc.id}")
    await audit(db, firm_id, "document.uploaded", "document", doc.id, actor_id=user_id,
                actor_kind="public" if source == "link" else "user", filename=filename, source=source)
    return doc


# ---------------------------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------------------------

def _extracted_to_invoice_dict(ex: dict[str, Any]) -> dict[str, Any]:
    f = {k: (v or {}).get("value") for k, v in ex.get("fields", {}).items()}
    lines = []
    for ln in ex.get("lines") or []:
        lines.append({k: ln.get(k) for k in ("description", "hsn", "quantity", "unit", "unit_price", "taxable_value",
                                             "tax_rate", "igst", "cgst", "sgst", "cess")})
    doc_type = ex.get("doc_type") or "invoice"
    return {
        "doc_type": "invoice" if doc_type == "other" else doc_type,
        "number": f.get("number") or "",
        "date": f.get("date"),
        "supplier_gstin": G.normalise(f.get("supplier_gstin")) or None,
        "supplier_name": f.get("supplier_name"),
        "buyer_gstin": G.normalise(f.get("buyer_gstin")) or None,
        "buyer_name": f.get("buyer_name"),
        "place_of_supply": parse_place_of_supply(f.get("place_of_supply")),
        "reverse_charge": str(f.get("reverse_charge") or "").lower() == "true",
        "taxable_value": _num(f.get("taxable_value")),
        "igst": _num(f.get("igst")),
        "cgst": _num(f.get("cgst")),
        "sgst": _num(f.get("sgst")),
        "cess": _num(f.get("cess")),
        "round_off": _num(f.get("round_off")),
        "total": _num(f.get("total")),
        "irn": f.get("irn"),
        "lines": [{**ln, **{k: _num(ln.get(k)) for k in ("quantity", "unit_price", "taxable_value", "igst", "cgst",
                                                          "sgst", "cess")},
                   "tax_rate": (str(ln.get("tax_rate")).replace("%", "").strip() or None)
                   if ln.get("tax_rate") not in (None, "") else None}
                  for ln in lines],
    }


def _num(v: Any) -> str | None:
    if v in (None, ""):
        return None
    s = str(v).replace(",", "").replace("₹", "").strip()
    try:
        float(s)
        return s
    except ValueError:
        return None


async def _memory(db: AsyncSession, supplier_gstin: str | None) -> list[SupplierMemory]:
    if not supplier_gstin:
        return []
    rows = await db.execute(select(SupplierMemory).where(SupplierMemory.supplier_gstin == supplier_gstin)
                            .order_by(SupplierMemory.hits.desc()).limit(20))
    return list(rows.scalars())


def _memory_hints(mem: list[SupplierMemory]) -> list[str]:
    return [m.hint or f"{m.field}: a value read as '{m.wrong}' was corrected to '{m.right}' ({m.hits}×)" for m in mem]


async def process_document(db: AsyncSession, document_id: uuid.UUID) -> dict[str, Any]:
    doc = await db.get(Document, document_id)
    if not doc:
        return {"skipped": "document not found"}
    if doc.status in ("extracted", "duplicate"):
        return {"skipped": doc.status}
    doc.status = "processing"
    await db.flush()
    storage = get_storage()
    data = await storage.get(doc.storage_key)
    pages = await _run_cpu(P.process, data, doc.mime)
    doc.pages = len(pages)
    keys = []
    for i, pg in enumerate(pages):
        k = f"firms/{doc.firm_id}/docs/{doc.id}/page-{i + 1}.png"
        await storage.put(k, P.png_bytes(pg.image, 1800), "image/png")
        keys.append(k)
    doc.page_images = keys
    doc.ocr = {"pages": [{"w": pg.size[0], "h": pg.size[1], "source": pg.source,
                          "tokens": [t.to_dict() for t in pg.tokens]} for pg in pages]}
    doc.phash, doc.layout_hash = P.page_hashes(pages[0])
    qrs = await _run_cpu(Q.decode_images, [pg.image for pg in pages])
    einv = next((q for q in qrs if q.kind == "einvoice"), None)
    doc.qr = {"kind": einv.kind, "payload": einv.payload, "signature_verified": einv.signature_verified} if einv else (
        {"kind": qrs[0].kind} if qrs else None)

    client_gstins = set((await db.execute(select(ClientGstin.gstin).where(ClientGstin.client_id == doc.client_id)))
                        .scalars())
    offline = extract_offline(pages, client_gstins)
    guess_supplier = (offline["fields"].get("supplier_gstin") or {}).get("value")
    if einv and einv.payload and einv.payload.get("SellerGstin"):
        guess_supplier = einv.payload["SellerGstin"]
    memory = await _memory(db, guess_supplier)

    engine, model, raw, usage = "offline", None, offline, None
    if ai_enabled():
        try:
            raw, usage = await extract_with_claude(pages, _memory_hints(memory))
            engine, model = provider() or "ai", usage.model
        except (AIUnavailable, AIRefused) as exc:
            log.warning("extract.ai_unavailable", error=str(exc))
            raw = {**offline, "notes": f"AI extraction unavailable ({exc}); used offline rules."}
    ext = Extraction(firm_id=doc.firm_id, document_id=doc.id, engine=engine, model=model,
                     prompt_version=PROMPT_VERSION, raw=raw,
                     input_tokens=usage.input_tokens if usage else 0, output_tokens=usage.output_tokens if usage else 0,
                     cost_usd=usage.cost_usd if usage else 0.0, latency_ms=usage.latency_ms if usage else 0,
                     request_id=usage.request_id if usage else None)
    db.add(ext)

    inv_dict = _extracted_to_invoice_dict(raw)
    sources: dict[str, str] = {}
    # The signed e-invoice QR is authoritative: fill gaps and correct misreads (but keep genuine mismatches).
    qr_corrections: list[dict[str, Any]] = []
    if einv and einv.payload:
        inv_dict, qr_sources, qr_corrections = Q.reconcile_with_qr(inv_dict, einv.payload, P.full_text(pages))
        sources.update(qr_sources)
    # Supplier memory: re-apply corrections humans made before for this supplier.
    supplier_mem = await _memory(db, inv_dict.get("supplier_gstin")) if inv_dict.get("supplier_gstin") != guess_supplier \
        else memory
    memory_fields: set[str] = set()
    for m in supplier_mem:
        if m.field in inv_dict and str(inv_dict.get(m.field) or "") == m.wrong:
            inv_dict[m.field] = m.right
            sources[m.field] = "memory"
            memory_fields.add(m.field)

    inv = InvoiceData.from_dict(inv_dict)
    qr_cmp = Q.compare(inv, einv.payload) if einv and einv.payload else None
    if qr_cmp and not inv.irn and qr_cmp.get("irn"):
        inv.irn = qr_cmp["irn"]

    book = await rulebook_for(db, doc.firm_id)
    known = set((await db.execute(select(Supplier.gstin))).scalars())
    findings = validate_invoice(inv, book, ValidationContext(client_gstins=client_gstins, known_suppliers=known,
                                                             today=date.today()))
    from lekha.engines.validation import Finding

    if qr_corrections:
        findings.append(Finding(
            "QR_CORRECTED", "info",
            "Corrected from the signed e-invoice QR: " + "; ".join(
                f"{c['field']} read as '{c['read']}' → '{c['qr']}'" for c in qr_corrections), qr_corrections[0]["field"],
            data={"corrections": qr_corrections}))
    if qr_cmp and qr_cmp["status"] == "mismatch":
        bad = [k for k, v in qr_cmp["checks"].items() if not v["ok"]]
        findings.append(Finding("QR_MISMATCH", ERROR,
                                "The signed e-invoice QR code disagrees with the printed invoice on: "
                                + ", ".join(bad) + ". Possible tampering or a re-typed invoice.", bad[0] if bad else None,
                                data=qr_cmp))

    # Field confidence.
    tokens_by_id = {t["id"]: t for pg in doc.ocr["pages"] for t in pg["tokens"]}
    field_sev: dict[str, str] = {}
    for f in findings:
        if f.field:
            root = f.field.split(".")[0].split("[")[0]
            if f.severity == ERROR or field_sev.get(root) != ERROR:
                field_sev[root] = f.severity if f.severity in (ERROR, "warning") else field_sev.get(root, "ok")
    weights = C.load_weights()
    evidence: list[FieldEvidence] = []
    scores: dict[str, float] = {}
    fields_raw = raw.get("fields", {})
    final_values = {
        "supplier_gstin": inv.supplier_gstin, "supplier_name": inv.supplier_name, "buyer_gstin": inv.buyer_gstin,
        "buyer_name": inv.buyer_name, "number": inv.number, "date": inv.date.isoformat() if inv.date else None,
        "place_of_supply": inv.place_of_supply, "reverse_charge": str(inv.reverse_charge).lower(),
        "taxable_value": str(inv.taxable_value), "igst": str(inv.igst), "cgst": str(inv.cgst), "sgst": str(inv.sgst),
        "cess": str(inv.cess), "round_off": str(inv.round_off), "total": str(inv.total), "irn": inv.irn,
    }
    for name, value in final_values.items():
        fr = fields_raw.get(name) or {}
        ids = [i for i in fr.get("token_ids") or [] if i in tokens_by_id]
        toks = [tokens_by_id[i] for i in ids]
        sev = field_sev.get(name)
        qr_ok = None
        if qr_cmp and name in qr_cmp["checks"]:
            qr_ok = 1.0 if qr_cmp["checks"][name]["ok"] else 0.0
        sig = C.FieldSignals(
            certainty=C.CERTAINTY.get(fr.get("certainty", "low"), 0.0) if value not in (None, "", "0.00", "0") or
            fr.get("value") else 0.5,
            grounded=C.grounded(name, fr.get("value"), [t["text"] for t in toks]),
            validator=0.0 if sev == ERROR else 0.5 if sev == "warning" else 1.0,
            qr=qr_ok,
            memory=0.0 if name in memory_fields else 1.0,
            ocr_conf=sum(t["conf"] for t in toks) / len(toks) if toks else 0.6,
        )
        sc = C.score(sig, weights)
        scores[name] = sc
        bbox = _union([t["box"] for t in toks]) if toks else None
        page = (ids[0] // 10000) + 1 if ids else None
        evidence.append(FieldEvidence(firm_id=doc.firm_id, field=name, value=value, confidence=round(sc, 4),
                                      page=page, token_ids=ids, bbox=bbox, source=sources.get(name, "offline" if engine == "offline" else "model"),
                                      signals=sig.vector()))
    conf = C.invoice_confidence(scores)

    # Upsert supplier in the firm-wide master.
    supplier = None
    if inv.supplier_gstin and G.is_valid(inv.supplier_gstin):
        supplier = (await db.execute(select(Supplier).where(Supplier.gstin == inv.supplier_gstin))).scalar_one_or_none()
        if not supplier:
            supplier = Supplier(firm_id=doc.firm_id, gstin=inv.supplier_gstin, legal_name=inv.supplier_name,
                                state_code=inv.supplier_gstin[:2], first_seen=inv.date or date.today())
            db.add(supplier)
            await db.flush()
        elif not supplier.legal_name and inv.supplier_name:
            supplier.legal_name = inv.supplier_name

    client_gstin = inv.buyer_gstin if inv.buyer_gstin in client_gstins else (
        next(iter(client_gstins)) if len(client_gstins) == 1 else None)
    invoice = Invoice(
        firm_id=doc.firm_id, client_id=doc.client_id, client_gstin=client_gstin, document_id=doc.id,
        supplier_id=supplier.id if supplier else None, source="document", doc_type=inv.doc_type,
        number=inv.number[:60], number_norm=normalise_number(inv.number)[:60], invoice_date=inv.date,
        period=period_of(inv.date) if inv.date else None, supplier_gstin=inv.supplier_gstin,
        supplier_name=inv.supplier_name, buyer_gstin=inv.buyer_gstin, buyer_name=inv.buyer_name,
        place_of_supply=inv.place_of_supply, reverse_charge=inv.reverse_charge, taxable_value=inv.taxable_value,
        igst=inv.igst, cgst=inv.cgst, sgst=inv.sgst, cess=inv.cess, round_off=inv.round_off, total=inv.total,
        irn=inv.irn, qr_status=qr_cmp["status"] if qr_cmp else ("none" if not qrs else "other"),
        confidence=conf, status="review", received_on=inv.date,
    )
    db.add(invoice)
    await db.flush()
    await _replace_children(db, invoice, inv, evidence, findings, book)

    auto = (conf >= get_settings().auto_approve_confidence and not any(f.severity == ERROR for f in findings)
            and engine != "offline")
    if auto:
        invoice.status = "approved"
        invoice.approved_at = datetime.now(UTC)
    doc.status = "extracted"
    doc.error = None
    await audit(db, doc.firm_id, "invoice.extracted", "invoice", invoice.id, actor_kind="system",
                engine=engine, confidence=conf, auto_approved=auto)
    await enqueue(db, "client.refresh", {"client_id": str(doc.client_id)}, firm_id=doc.firm_id, delay_s=2,
                  dedupe_key=f"refresh:{doc.client_id}")
    return {"invoice_id": str(invoice.id), "engine": engine, "confidence": conf, "auto_approved": auto,
            "errors": sum(1 for f in findings if f.severity == ERROR)}


async def _replace_children(db: AsyncSession, invoice: Invoice, inv: InvoiceData, evidence: list[FieldEvidence],
                            findings: list, book) -> None:
    await db.execute(delete(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id))
    await db.execute(delete(ValidationResult).where(ValidationResult.invoice_id == invoice.id))
    blocked_params = book.get("BLOCKED_CREDITS_17_5", inv.date or date.today())
    blocked_total = ZERO
    for i, ln in enumerate(inv.lines):
        reason = blocked_reason(ln.hsn, ln.description, blocked_params)
        line_tax = ln.igst + ln.cgst + ln.sgst + ln.cess
        if reason:
            if line_tax:
                blocked_total += line_tax
            elif ln.tax_rate is not None:
                blocked_total += (ln.taxable_value * ln.tax_rate / 100).quantize(ZERO)
        db.add(InvoiceLine(firm_id=invoice.firm_id, invoice_id=invoice.id, position=i, description=ln.description,
                           hsn=ln.hsn, quantity=ln.quantity, unit=(ln.unit or None) and ln.unit[:20],
                           unit_price=ln.unit_price, taxable_value=ln.taxable_value, tax_rate=ln.tax_rate,
                           igst=ln.igst, cgst=ln.cgst, sgst=ln.sgst, cess=ln.cess, blocked_reason=reason))
    invoice.blocked_amount = min(blocked_total, invoice.itc) if inv.lines else ZERO
    if evidence:
        await db.execute(delete(FieldEvidence).where(FieldEvidence.invoice_id == invoice.id))
        for e in evidence:
            e.invoice_id = invoice.id
            db.add(e)
    for f in findings:
        db.add(ValidationResult(firm_id=invoice.firm_id, invoice_id=invoice.id, code=f.code, severity=f.severity,
                                message=f.message, field=f.field, fix=f.fix, rule=f.rule, data=_jsonable(f.data)))


def _jsonable(v: Any) -> Any:
    from lekha.core.audit import jsonable

    return jsonable(v)


def _union(boxes: list[list[float]]) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


async def _run_cpu(fn, *args):
    import asyncio

    return await asyncio.to_thread(fn, *args)


async def mark_failed(db: AsyncSession, document_id: uuid.UUID, error: str) -> None:
    doc = await db.get(Document, document_id)
    if doc:
        doc.status = "failed"
        doc.error = error[:2000]
