"""Import an existing purchase register (Tally / Busy / Excel / CSV export) as approved invoices (M6)."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.core.audit import audit
from lekha.core.errors import AppError
from lekha.engines import gstin as G
from lekha.engines.dates import parse_date, period_of
from lekha.engines.gstr2b import _map_header
from lekha.engines.invoice import InvoiceData, normalise_number
from lekha.engines.money import d
from lekha.engines.states import parse_place_of_supply
from lekha.engines.validation import ValidationContext, validate_invoice
from lekha.jobs.queue import enqueue
from lekha.models import ClientGstin, Invoice, Supplier, ValidationResult
from lekha.services.rulebook import rulebook_for


def _rows(filename: str, data: bytes) -> list[list[Any]]:
    if filename.lower().endswith((".xlsx", ".xlsm")) or data[:2] == b"PK":
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    text = data.decode("utf-8-sig", errors="replace")
    return [list(r) for r in csv.reader(io.StringIO(text))]


async def import_register(db: AsyncSession, firm_id: uuid.UUID, client_id: uuid.UUID, filename: str, data: bytes,
                          user_id: uuid.UUID | None) -> dict[str, Any]:
    rows = _rows(filename, data)
    header_idx, mapping = None, {}
    for i, r in enumerate(rows[:15]):
        m = _map_header(r)
        if "supplier_gstin" in m and "number" in m and ("taxable_value" in m or "total" in m):
            header_idx, mapping = i, m
            break
    if header_idx is None:
        raise AppError("Could not find the columns. The file needs at least: Supplier GSTIN, Invoice No, Invoice "
                       "Date and Taxable Value (or Invoice Value).", code="bad_register")
    gstins = {g.gstin for g in (await db.execute(select(ClientGstin).where(ClientGstin.client_id == client_id))).scalars()}
    default_gstin = next(iter(gstins)) if len(gstins) == 1 else None
    known = {s.gstin: s for s in (await db.execute(select(Supplier))).scalars()}
    existing = {(i.supplier_gstin, i.number_norm) for i in (await db.execute(select(Invoice).where(
        Invoice.client_id == client_id))).scalars()}
    book = await rulebook_for(db, firm_id)
    created, skipped, problems = 0, 0, []
    for n, r in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        def v(k: str) -> Any:
            i = mapping.get(k)
            return r[i] if i is not None and i < len(r) else None

        sg = G.normalise(str(v("supplier_gstin") or ""))
        if not sg:
            continue
        number = str(v("number") or "").strip()
        dt = parse_date(v("invoice_date"))
        if not number or not dt:
            problems.append(f"Row {n}: missing invoice number or date")
            continue
        key = (sg, normalise_number(number))
        if key in existing:
            skipped += 1
            continue
        existing.add(key)
        tx, ig, cg, ss, cs = d(v("taxable_value")), d(v("igst")), d(v("cgst")), d(v("sgst")), d(v("cess"))
        total = d(v("total")) if v("total") not in (None, "") else tx + ig + cg + ss + cs
        inv = Invoice(firm_id=firm_id, client_id=client_id, client_gstin=default_gstin, source="import",
                      doc_type="invoice", number=number[:60], number_norm=normalise_number(number)[:60],
                      invoice_date=dt, period=period_of(dt), supplier_gstin=sg,
                      supplier_name=(str(v("supplier_name")) if v("supplier_name") else None),
                      buyer_gstin=default_gstin, place_of_supply=parse_place_of_supply(str(v("place_of_supply") or "")),
                      taxable_value=tx, igst=ig, cgst=cg, sgst=ss, cess=cs, total=total, status="approved",
                      confidence=1.0, received_on=dt, approved_by=user_id)
        if G.is_valid(sg):
            s = known.get(sg)
            if not s:
                s = Supplier(firm_id=firm_id, gstin=sg, legal_name=inv.supplier_name, state_code=sg[:2], first_seen=dt)
                db.add(s)
                await db.flush()
                known[sg] = s
            inv.supplier_id = s.id
        db.add(inv)
        await db.flush()
        data_ = InvoiceData(number=number, date=dt, supplier_gstin=sg, buyer_gstin=default_gstin,
                            place_of_supply=inv.place_of_supply or (default_gstin[:2] if default_gstin else None),
                            taxable_value=tx, igst=ig, cgst=cg, sgst=ss, cess=cs, total=total)
        for f in validate_invoice(data_, book, ValidationContext(client_gstins=gstins, today=date.today())):
            if f.code not in ("BUYER_GSTIN_MISSING", "POS_MISSING"):
                db.add(ValidationResult(firm_id=firm_id, invoice_id=inv.id, code=f.code, severity=f.severity,
                                        message=f.message, field=f.field, fix=f.fix, rule=f.rule))
        created += 1
    await audit(db, firm_id, "register.imported", "client", client_id, actor_id=user_id, created=created,
                skipped=skipped, filename=filename)
    await enqueue(db, "client.refresh", {"client_id": str(client_id)}, firm_id=firm_id, delay_s=1,
                  dedupe_key=f"refresh:{client_id}")
    return {"created": created, "skipped_duplicates": skipped, "problems": problems[:50]}
