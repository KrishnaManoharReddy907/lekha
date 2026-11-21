"""Invoice review: edits (with supplier memory learning), re-validation, approval, fixes."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.core.audit import audit, jsonable, snapshot
from lekha.core.errors import AppError, NotFound
from lekha.engines import gstin as G
from lekha.engines.dates import parse_date, period_of
from lekha.engines.invoice import InvoiceData, normalise_number
from lekha.engines.money import d
from lekha.engines.validation import ValidationContext, validate_invoice
from lekha.jobs.queue import enqueue
from lekha.models import ClientGstin, FieldEvidence, Invoice, InvoiceLine, Supplier, SupplierMemory
from lekha.services.pipeline import _replace_children
from lekha.services.rulebook import rulebook_for

HEADER = ["doc_type", "number", "invoice_date", "supplier_gstin", "supplier_name", "buyer_gstin", "buyer_name",
          "place_of_supply", "reverse_charge", "taxable_value", "igst", "cgst", "sgst", "cess", "round_off", "total",
          "irn", "goods_received", "received_on", "notes"]
MONEY = {"taxable_value", "igst", "cgst", "sgst", "cess", "round_off", "total"}
EVIDENCE_NAME = {"invoice_date": "date"}


def invoice_to_data(inv: Invoice, lines: list[InvoiceLine]) -> InvoiceData:
    return InvoiceData.from_dict({
        "doc_type": inv.doc_type, "number": inv.number, "date": inv.invoice_date, "supplier_gstin": inv.supplier_gstin,
        "supplier_name": inv.supplier_name, "buyer_gstin": inv.buyer_gstin, "buyer_name": inv.buyer_name,
        "place_of_supply": inv.place_of_supply, "reverse_charge": inv.reverse_charge,
        "taxable_value": inv.taxable_value, "igst": inv.igst, "cgst": inv.cgst, "sgst": inv.sgst, "cess": inv.cess,
        "round_off": inv.round_off, "total": inv.total, "irn": inv.irn,
        "lines": [{"description": ln.description, "hsn": ln.hsn, "quantity": ln.quantity, "unit": ln.unit,
                   "unit_price": ln.unit_price, "taxable_value": ln.taxable_value, "tax_rate": ln.tax_rate,
                   "igst": ln.igst, "cgst": ln.cgst, "sgst": ln.sgst, "cess": ln.cess} for ln in lines],
    })


async def revalidate(db: AsyncSession, inv: Invoice) -> list:
    lines = list((await db.execute(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id)
                                   .order_by(InvoiceLine.position))).scalars())
    data = invoice_to_data(inv, lines)
    book = await rulebook_for(db, inv.firm_id)
    client_gstins = set((await db.execute(select(ClientGstin.gstin).where(ClientGstin.client_id == inv.client_id)))
                        .scalars())
    known = set((await db.execute(select(Supplier.gstin))).scalars())
    findings = validate_invoice(data, book, ValidationContext(client_gstins=client_gstins, known_suppliers=known,
                                                              today=date.today()))
    await _replace_children(db, inv, data, [], findings, book)
    return findings


async def _learn(db: AsyncSession, inv: Invoice, field: str, wrong: Any, right: Any) -> None:
    if not inv.supplier_gstin or wrong in (None, "") or str(wrong) == str(right):
        return
    key = EVIDENCE_NAME.get(field, field)
    w, r = str(wrong)[:300], str(right)[:300]
    mem = (await db.execute(select(SupplierMemory).where(SupplierMemory.supplier_gstin == inv.supplier_gstin,
                                                         SupplierMemory.field == key,
                                                         SupplierMemory.wrong == w))).scalar_one_or_none()
    if mem:
        mem.hits += 1
        mem.right = r
    else:
        db.add(SupplierMemory(firm_id=inv.firm_id, supplier_gstin=inv.supplier_gstin, field=key, wrong=w, right=r,
                              hint=f"For this supplier, '{w}' in {key} was corrected to '{r}'."))


async def update_invoice(db: AsyncSession, inv: Invoice, patch: dict[str, Any], lines: list[dict] | None,
                         actor_id: uuid.UUID | None) -> list:
    if inv.status == "approved" and patch.keys() - {"notes", "goods_received", "received_on"}:
        raise AppError("Approved invoices cannot be edited. Reopen it first.", code="approved")
    before = snapshot(inv, HEADER)
    from_model = {e.field for e in (await db.execute(select(FieldEvidence).where(FieldEvidence.invoice_id == inv.id)))
                  .scalars() if e.source in ("model", "offline", "memory")}
    for k, v in patch.items():
        if k not in HEADER:
            continue
        if k in MONEY:
            v = d(v)
        elif k in ("invoice_date", "received_on"):
            v = parse_date(v)
        elif k in ("supplier_gstin", "buyer_gstin"):
            v = G.normalise(v) or None
        old = getattr(inv, k)
        if EVIDENCE_NAME.get(k, k) in from_model and jsonable(old) != jsonable(v):
            await _learn(db, inv, k, jsonable(old), jsonable(v))
        setattr(inv, k, v)
        ev = (await db.execute(select(FieldEvidence).where(FieldEvidence.invoice_id == inv.id,
                                                           FieldEvidence.field == EVIDENCE_NAME.get(k, k)))
              ).scalar_one_or_none()
        if ev and jsonable(ev.value) != jsonable(v):
            ev.value = None if v is None else str(jsonable(v))
            ev.source = "human"
            ev.confidence = 1.0
    inv.number_norm = normalise_number(inv.number)
    inv.period = period_of(inv.invoice_date) if inv.invoice_date else None
    if "supplier_gstin" in patch and inv.supplier_gstin and G.is_valid(inv.supplier_gstin):
        sup = (await db.execute(select(Supplier).where(Supplier.gstin == inv.supplier_gstin))).scalar_one_or_none()
        if not sup:
            sup = Supplier(firm_id=inv.firm_id, gstin=inv.supplier_gstin, legal_name=inv.supplier_name,
                           state_code=inv.supplier_gstin[:2], first_seen=inv.invoice_date or date.today())
            db.add(sup)
            await db.flush()
        inv.supplier_id = sup.id
    if "buyer_gstin" in patch:
        cg = (await db.execute(select(ClientGstin).where(ClientGstin.gstin == inv.buyer_gstin,
                                                         ClientGstin.client_id == inv.client_id))).scalar_one_or_none()
        if cg:
            inv.client_gstin = cg.gstin
    if lines is not None:
        from sqlalchemy import delete

        await db.execute(delete(InvoiceLine).where(InvoiceLine.invoice_id == inv.id))
        for i, ln in enumerate(lines):
            db.add(InvoiceLine(
                firm_id=inv.firm_id, invoice_id=inv.id, position=i, description=str(ln.get("description") or ""),
                hsn=(str(ln.get("hsn")).strip() or None) if ln.get("hsn") else None,
                quantity=d(ln["quantity"]) if ln.get("quantity") not in (None, "") else None, unit=ln.get("unit"),
                unit_price=d(ln["unit_price"]) if ln.get("unit_price") not in (None, "") else None,
                taxable_value=d(ln.get("taxable_value")),
                tax_rate=Decimal(str(ln["tax_rate"])) if ln.get("tax_rate") not in (None, "") else None,
                igst=d(ln.get("igst")), cgst=d(ln.get("cgst")), sgst=d(ln.get("sgst")), cess=d(ln.get("cess"))))
        await db.flush()
    findings = await revalidate(db, inv)
    # Confidence after human review: fields a human touched count as certain.
    evs = list((await db.execute(select(FieldEvidence).where(FieldEvidence.invoice_id == inv.id))).scalars())
    if evs:
        from lekha.engines.confidence import invoice_confidence

        inv.confidence = invoice_confidence({e.field: e.confidence for e in evs})
    await audit(db, inv.firm_id, "invoice.updated", "invoice", inv.id, actor_id=actor_id, before=before,
                after=snapshot(inv, HEADER))
    return findings


async def set_status(db: AsyncSession, inv: Invoice, status: str, actor_id: uuid.UUID | None,
                     reason: str | None = None) -> None:
    if status not in ("approved", "rejected", "review"):
        raise AppError("Unknown status.")
    if status == "approved":
        from lekha.models import ValidationResult

        blocking = (await db.execute(select(ValidationResult).where(
            ValidationResult.invoice_id == inv.id, ValidationResult.severity == "error",
            ValidationResult.resolved.is_(False), ValidationResult.code.in_(
                ("INVOICE_NUMBER_MISSING", "DATE_MISSING", "AMOUNTS_MISSING", "DATE_IN_FUTURE"))))).scalars().first()
        if blocking:
            raise AppError(f"Fix this before approving: {blocking.message}", code="blocking_error")
        inv.approved_by = actor_id
        inv.approved_at = datetime.now(UTC)
    before = inv.status
    inv.status = status
    if reason:
        inv.notes = ((inv.notes or "") + f"\n[{status}] {reason}").strip()
    await audit(db, inv.firm_id, f"invoice.{status}", "invoice", inv.id, actor_id=actor_id,
                before={"status": before}, after={"status": status}, reason=reason)
    await enqueue(db, "client.refresh", {"client_id": str(inv.client_id)}, firm_id=inv.firm_id, delay_s=1,
                  dedupe_key=f"refresh:{inv.client_id}")


async def get_invoice(db: AsyncSession, invoice_id: uuid.UUID) -> Invoice:
    inv = await db.get(Invoice, invoice_id)
    if not inv:
        raise NotFound("Invoice not found.")
    return inv
