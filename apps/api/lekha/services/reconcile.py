"""GSTR-2B import, reconciliation runs and IMS recommendations (M7, M8)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.core.audit import audit
from lekha.core.errors import AppError
from lekha.core.storage import get_storage
from lekha.engines import gstr2b as P
from lekha.engines import ims as IMS
from lekha.engines.dates import period_to_date
from lekha.engines.matching import Doc, reconcile
from lekha.engines.money import ZERO
from lekha.jobs.queue import enqueue
from lekha.models import (
    ClientGstin,
    GraphSignal,
    Gstr2bImport,
    Gstr2bRecord,
    ImsDecision,
    Invoice,
    Match,
    Supplier,
)
from lekha.services.rulebook import rulebook_for


def _period_key(p: str) -> tuple[int, int]:
    return int(p[2:]), int(p[:2])


async def import_gstr2b(db: AsyncSession, *, firm_id: uuid.UUID, client_id: uuid.UUID, filename: str, data: bytes,
                        period: str | None, gstin: str | None, user_id: uuid.UUID | None,
                        auto_close: bool = True) -> Gstr2bImport:
    try:
        stmt = P.parse(filename, data)
    except P.Gstr2bError as e:
        raise AppError(str(e), code="bad_2b") from e
    gstins = {g.gstin: g for g in (await db.execute(select(ClientGstin).where(ClientGstin.client_id == client_id))).scalars()}
    if not gstins:
        raise AppError("Add the client's GSTIN before importing GSTR-2B.")
    client_gstin = stmt.gstin or gstin or (next(iter(gstins)) if len(gstins) == 1 else None)
    if not client_gstin:
        raise AppError("Choose which GSTIN this GSTR-2B belongs to.", code="need_gstin")
    if client_gstin not in gstins:
        raise AppError(f"This GSTR-2B is for {client_gstin}, which is not one of this client's GSTINs.",
                       code="wrong_gstin")
    period = stmt.period or period
    if not period or len(period) != 6:
        raise AppError("Choose the return period (month) of this GSTR-2B.", code="need_period")

    # Re-import of the same period replaces the old one, keeping the user's IMS decisions.
    old = list((await db.execute(select(Gstr2bImport).where(Gstr2bImport.client_gstin == client_gstin,
                                                            Gstr2bImport.period == period,
                                                            Gstr2bImport.superseded.is_(False)))).scalars())
    kept: dict[tuple[str, str], tuple[str | None, uuid.UUID | None, str | None]] = {}
    for imp in old:
        rows = (await db.execute(select(Gstr2bRecord, ImsDecision).join(
            ImsDecision, ImsDecision.record_id == Gstr2bRecord.id, isouter=True).where(
            Gstr2bRecord.import_id == imp.id))).all()
        for rec, dec in rows:
            if dec and dec.decided_action:
                kept[(rec.supplier_gstin, rec.number_norm)] = (dec.decided_action, dec.decided_by, dec.remark)
        imp.superseded = True
        await db.execute(delete(Gstr2bRecord).where(Gstr2bRecord.import_id == imp.id))

    imp = Gstr2bImport(firm_id=firm_id, client_id=client_id, client_gstin=client_gstin, period=period,
                       generated_on=stmt.generated_on, filename=filename[:300], record_count=len(stmt.records),
                       summary=stmt.summary(), uploaded_by=user_id)
    db.add(imp)
    await db.flush()
    key = f"firms/{firm_id}/gstr2b/{imp.id}/{'source.xlsx' if filename.lower().endswith('xlsx') else 'source.json'}"
    await get_storage().put(key, data, "application/octet-stream")
    imp.storage_key = key
    for r in stmt.records:
        rec = Gstr2bRecord(
            firm_id=firm_id, import_id=imp.id, client_id=client_id, client_gstin=client_gstin, period=period,
            section=r.section, doc_type=r.doc_type, supplier_gstin=r.supplier_gstin, supplier_name=r.supplier_name,
            supplier_filing_date=r.supplier_filing_date, supplier_period=r.supplier_period, number=r.number[:60],
            number_norm=r.number_norm[:60], invoice_date=r.invoice_date, place_of_supply=r.place_of_supply,
            reverse_charge=r.reverse_charge, taxable_value=r.taxable_value, igst=r.igst, cgst=r.cgst, sgst=r.sgst,
            cess=r.cess, total=r.total, itc_available=r.itc_available, itc_unavailable_reason=r.itc_unavailable_reason,
            irn=r.irn, original_number=r.original_number, raw=_jsonable(r.raw))
        db.add(rec)
        k = (r.supplier_gstin, r.number_norm)
        if k in kept:
            await db.flush()
            action, by, remark = kept[k]
            db.add(ImsDecision(firm_id=firm_id, client_id=client_id, record_id=rec.id, period=period,
                               recommendation=action or "pending", decided_action=action, decided_by=by, remark=remark,
                               decided_at=datetime.now(UTC)))
    # Learn supplier names from the portal (authoritative trade names).
    names = {r.supplier_gstin: r.supplier_name for r in stmt.records if r.supplier_name and len(r.supplier_gstin) == 15}
    existing = {s.gstin: s for s in (await db.execute(select(Supplier).where(Supplier.gstin.in_(names)))).scalars()}
    for g, n in names.items():
        s = existing.get(g)
        if s is None:
            db.add(Supplier(firm_id=firm_id, gstin=g, trade_name=n, legal_name=n, state_code=g[:2],
                            first_seen=period_to_date(period)))
        elif not s.trade_name:
            s.trade_name = n
    await db.flush()
    await audit(db, firm_id, "gstr2b.imported", "gstr2b_import", imp.id, actor_id=user_id, period=period,
                gstin=client_gstin, records=len(stmt.records), replaced=len(old))
    await enqueue(db, "reconcile.run", {"client_id": str(client_id), "period": period,
                                        "actor_id": str(user_id) if user_id else None}, firm_id=firm_id,
                  dedupe_key=f"recon:{client_id}:{period}")
    if auto_close:
        # A new GSTR-2B starts the month's close automatically (each consequential step still needs approval).
        await enqueue(db, "close.auto", {"client_id": str(client_id), "period": period, "gstin": client_gstin},
                      firm_id=firm_id, delay_s=5, dedupe_key=f"close-auto:{client_id}:{period}")
    return imp


def _jsonable(v: Any) -> Any:
    from lekha.core.audit import jsonable

    return jsonable(v)


def _inv_doc(i: Invoice) -> Doc:
    return Doc(id=i.id, supplier_gstin=i.supplier_gstin or "", number=i.number or "", date=i.invoice_date,
               taxable=i.taxable_value, igst=i.igst, cgst=i.cgst, sgst=i.sgst, cess=i.cess,
               doc_type=i.doc_type if i.doc_type != "bill_of_supply" else "invoice")


def _rec_doc(r: Gstr2bRecord) -> Doc:
    return Doc(id=r.id, supplier_gstin=r.supplier_gstin, number=r.number, date=r.invoice_date, taxable=r.taxable_value,
               igst=r.igst, cgst=r.cgst, sgst=r.sgst, cess=r.cess, doc_type=r.doc_type, itc_available=r.itc_available,
               reason=r.itc_unavailable_reason)


async def run_reconciliation(db: AsyncSession, firm_id: uuid.UUID, client_id: uuid.UUID, period: str,
                             actor_id: uuid.UUID | None = None) -> dict[str, Any]:
    run_id = uuid.uuid4()
    book_rules = await rulebook_for(db, firm_id)
    tol = Decimal(str(book_rules.param("AMOUNT_TOLERANCE", period_to_date(period))))
    gstins = list((await db.execute(select(ClientGstin.gstin).where(ClientGstin.client_id == client_id))).scalars())
    pk = _period_key(period)
    records = list((await db.execute(select(Gstr2bRecord).where(Gstr2bRecord.client_id == client_id,
                                                                 Gstr2bRecord.period == period))).scalars())
    # Invoices already matched in an earlier period are settled; everything else up to this period is open.
    settled = set((await db.execute(select(Match.invoice_id).where(
        Match.client_id == client_id, Match.period != period, Match.status.in_(("matched", "mismatch")),
        Match.invoice_id.is_not(None)))).scalars())
    invoices = [i for i in (await db.execute(select(Invoice).where(
        Invoice.client_id == client_id, Invoice.status.in_(("approved", "review")),
        Invoice.client_gstin.in_(gstins)))).scalars()
        if i.period and _period_key(i.period) <= pk and i.id not in settled and i.doc_type != "bill_of_supply"]

    results = reconcile([_inv_doc(i) for i in invoices], [_rec_doc(r) for r in records], tol)

    # Replace this period's results and any stale "missing" rows for the invoices we just re-evaluated.
    await db.execute(delete(Match).where(Match.client_id == client_id, Match.period == period))
    await db.execute(delete(Match).where(Match.client_id == client_id, Match.status == "missing_in_2b",
                                         Match.invoice_id.in_([i.id for i in invoices])))
    inv_by = {i.id: i for i in invoices}
    rec_by = {r.id: r for r in records}
    counts: dict[str, int] = {}
    itc_books = itc_2b = ZERO
    match_for_record: dict[uuid.UUID, Match] = {}
    for res in results:
        counts[res.status] = counts.get(res.status, 0) + 1
        itc_books += res.itc_books
        itc_2b += res.itc_2b
        # One row per link. For split/merge groups, every record (or invoice) links to the single counterpart;
        # the group's ITC totals are stored on the first row only so sums stay correct.
        b_ids: list[Any] = res.book_ids or [None]
        r_ids: list[Any] = res.record_ids or [None]
        if len(b_ids) == 1:
            pairs = [(b_ids[0], r) for r in r_ids]
        else:
            pairs = [(b, r_ids[0]) for b in b_ids]
        for n, (b, r) in enumerate(pairs):
            m = Match(firm_id=firm_id, client_id=client_id, period=period,
                      client_gstin=(inv_by[b].client_gstin if b else rec_by[r].client_gstin) or "",
                      invoice_id=b, record_id=r, group_id=res.group_id, kind=res.kind, status=res.status,
                      mismatch_types=res.mismatch_types, score=res.score, features=res.features,
                      explanation=_jsonable(res.explanation), itc_books=res.itc_books if n == 0 else ZERO,
                      itc_2b=res.itc_2b if n == 0 else ZERO, run_id=run_id)
            db.add(m)
            if r:
                match_for_record[r] = m
    await db.flush()
    await _recommend_ims(db, firm_id, client_id, period, records, match_for_record, inv_by)
    summary = {"period": period, "run_id": str(run_id), "counts": counts, "itc_books": str(itc_books),
               "itc_2b": str(itc_2b), "records": len(records), "invoices": len(invoices)}
    await audit(db, firm_id, "reconciliation.run", "client", client_id, actor_id=actor_id,
                actor_kind="user" if actor_id else "system", **summary)
    await enqueue(db, "client.refresh", {"client_id": str(client_id)}, firm_id=firm_id, delay_s=1,
                  dedupe_key=f"refresh:{client_id}")
    await enqueue(db, "graph.analyse", {}, firm_id=firm_id, delay_s=3, dedupe_key=f"graph:{firm_id}")
    return summary


async def _recommend_ims(db: AsyncSession, firm_id: uuid.UUID, client_id: uuid.UUID, period: str,
                         records: list[Gstr2bRecord], match_for_record: dict[uuid.UUID, Match],
                         inv_by: dict[uuid.UUID, Invoice]) -> None:
    risky = {}
    for s in (await db.execute(select(GraphSignal).where(GraphSignal.status != "dismissed"))).scalars():
        for g in s.members or []:
            risky[g] = max(risky.get(g, 0.0), s.score)
    existing = {d.record_id: d for d in (await db.execute(select(ImsDecision).where(
        ImsDecision.record_id.in_([r.id for r in records])))).scalars()}
    today = date.today()
    for r in records:
        m = match_for_record.get(r.id)
        inv = inv_by.get(m.invoice_id) if m and m.invoice_id else None
        rec = IMS.recommend(IMS.ImsInput(
            record_doc_type=r.doc_type, itc_available=r.itc_available,
            match_status=m.status if m else None, mismatch_types=list(m.mismatch_types or []) if m else [],
            itc_books=inv.itc if inv else ZERO, itc_2b=r.itc, invoice_status=inv.status if inv else None,
            goods_received=inv.goods_received if inv else True,
            record_age_days=(today - r.invoice_date).days if r.invoice_date else 0,
            supplier_risk=risky.get(r.supplier_gstin, 0.0), blocked_17_5=bool(inv and inv.blocked_amount > 0)))
        d = existing.get(r.id)
        if d is None:
            db.add(ImsDecision(firm_id=firm_id, client_id=client_id, record_id=r.id, period=period,
                               recommendation=rec.action, reason_codes=rec.reasons, reason_text=rec.text,
                               confidence=rec.confidence))
        else:
            d.recommendation, d.reason_codes, d.reason_text, d.confidence = rec.action, rec.reasons, rec.text, rec.confidence


async def decide_ims(db: AsyncSession, firm_id: uuid.UUID, record_ids: list[uuid.UUID], action: str | None,
                     user_id: uuid.UUID | None, remark: str | None = None) -> int:
    if action not in ("accept", "reject", "pending", "use_recommendation", None):
        raise AppError("Action must be accept, reject, pending or use_recommendation.")
    rows = list((await db.execute(select(ImsDecision).where(ImsDecision.record_id.in_(record_ids)))).scalars())
    for d in rows:
        chosen = d.recommendation if action == "use_recommendation" else action
        d.decided_action = chosen
        d.decided_by = user_id
        d.decided_at = datetime.now(UTC) if chosen else None
        d.remark = remark
    await audit(db, firm_id, "ims.decided", "ims", None, actor_id=user_id, decision=action, count=len(rows))
    return len(rows)


async def periods_for_client(db: AsyncSession, client_id: uuid.UUID) -> list[dict[str, Any]]:
    inv = dict((await db.execute(select(Invoice.period, func.count()).where(
        Invoice.client_id == client_id, Invoice.period.is_not(None)).group_by(Invoice.period))).all())
    imports = {i.period: i for i in (await db.execute(select(Gstr2bImport).where(
        Gstr2bImport.client_id == client_id, Gstr2bImport.superseded.is_(False)))).scalars()}
    periods = sorted(set(inv) | set(imports), key=_period_key, reverse=True)
    return [{"period": p, "invoices": inv.get(p, 0), "has_2b": p in imports,
             "import_id": str(imports[p].id) if p in imports else None,
             "generated_on": imports[p].generated_on.isoformat() if p in imports and imports[p].generated_on else None,
             "records": imports[p].record_count if p in imports else 0} for p in periods]
