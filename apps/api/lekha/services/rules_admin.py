"""Rules administration with impact simulation (N4).

A rule change is first saved as a *draft* version with an effective date. Running an impact report
re-evaluates every affected invoice under the published rules and under the published rules with
the draft overlaid, and reports exactly what would change — new errors, cleared errors, and the
change in credit at risk — before anyone publishes it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.core.audit import audit
from lekha.core.errors import AppError, NotFound
from lekha.engines.risk import RiskInvoice, assess
from lekha.engines.validation import ValidationContext, validate_invoice
from lekha.jobs.queue import enqueue
from lekha.models import Client, ClientGstin, Invoice, InvoiceLine, Payment, RuleImpactReport, RuleVersionRow
from lekha.services.invoices import invoice_to_data
from lekha.services.rulebook import rulebook_for


async def list_rules(db: AsyncSession, firm_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = list((await db.execute(select(RuleVersionRow).where(
        (RuleVersionRow.firm_id.is_(None)) | (RuleVersionRow.firm_id == firm_id))
        .order_by(RuleVersionRow.code, RuleVersionRow.effective_from))).scalars())
    by: dict[str, list[RuleVersionRow]] = {}
    for r in rows:
        by.setdefault(r.code, []).append(r)
    today = date.today()
    out = []
    for code, vs in by.items():
        current = next((v for v in sorted(vs, key=lambda x: (x.effective_from, x.version), reverse=True)
                        if v.status == "published" and v.effective_from <= today and
                        (v.effective_to is None or today <= v.effective_to)), None)
        out.append({"code": code, "title": (current or vs[-1]).title, "current_version": current.version if current else None,
                    "versions": [{"id": str(v.id), "version": v.version, "status": v.status, "title": v.title,
                                  "source": v.source, "effective_from": v.effective_from.isoformat(),
                                  "effective_to": v.effective_to.isoformat() if v.effective_to else None,
                                  "params": v.params, "firm_scoped": v.firm_id is not None} for v in vs]})
    return out


async def create_draft(db: AsyncSession, firm_id: uuid.UUID, code: str, params: dict[str, Any], effective_from: date,
                       effective_to: date | None, title: str, source: str, user_id: uuid.UUID | None) -> RuleVersionRow:
    exists = (await db.execute(select(RuleVersionRow).where(RuleVersionRow.code == code))).scalars().first()
    if not exists:
        raise AppError(f"Unknown rule {code}.")
    _validate_params(code, params)
    version = (await db.execute(select(func.max(RuleVersionRow.version)).where(RuleVersionRow.code == code))).scalar() + 1
    row = RuleVersionRow(firm_id=firm_id, code=code, version=version, title=title or exists.title, source=source,
                         effective_from=effective_from, effective_to=effective_to, params=params, status="draft",
                         created_by=user_id)
    db.add(row)
    await db.flush()
    await audit(db, firm_id, "rule.drafted", "rule_version", row.id, actor_id=user_id, code=code, version=version)
    return row


def _validate_params(code: str, params: dict[str, Any]) -> None:
    if code == "GST_RATE_SLABS":
        v = params.get("value")
        if not isinstance(v, list) or not all(isinstance(x, (int, float)) and 0 <= x <= 100 for x in v):
            raise AppError("GST_RATE_SLABS needs a list of percentages, e.g. [0, 5, 18, 40].")
    elif code in ("ITC_PAYMENT_DAYS", "GSTR2B_GENERATION_DAY", "GSTR3B_DUE_DAY", "INVOICE_NUMBER_MAX_LEN"):
        if not isinstance(params.get("value"), int) or params["value"] <= 0:
            raise AppError(f"{code} needs a positive whole number in 'value'.")
    elif code in ("SEC_16_4_DEADLINE", "RULE_37A_DEADLINE"):
        if not (1 <= int(params.get("month", 0)) <= 12 and 1 <= int(params.get("day", 0)) <= 31):
            raise AppError(f"{code} needs 'month' and 'day'.")


async def start_impact(db: AsyncSession, firm_id: uuid.UUID, draft_ids: list[uuid.UUID]) -> RuleImpactReport:
    drafts = list((await db.execute(select(RuleVersionRow).where(RuleVersionRow.id.in_(draft_ids),
                                                                 RuleVersionRow.status == "draft"))).scalars())
    if not drafts:
        raise AppError("Choose at least one draft rule.")
    rep = RuleImpactReport(firm_id=firm_id, draft_ids=[str(d.id) for d in drafts], status="running")
    db.add(rep)
    await db.flush()
    await enqueue(db, "rules.impact", {"report_id": str(rep.id)}, firm_id=firm_id)
    return rep


async def run_impact(db: AsyncSession, firm_id: uuid.UUID, report_id: uuid.UUID, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    rep = await db.get(RuleImpactReport, report_id)
    draft_ids = [uuid.UUID(x) for x in rep.draft_ids]
    published = await rulebook_for(db, firm_id)
    simulated = await rulebook_for(db, firm_id, include_drafts=draft_ids)
    drafts = list((await db.execute(select(RuleVersionRow).where(RuleVersionRow.id.in_(draft_ids)))).scalars())
    start = min(d.effective_from for d in drafts)
    invoices = list((await db.execute(select(Invoice).where(Invoice.status.in_(("approved", "review")),
                                                            Invoice.invoice_date.is_not(None)))).scalars())
    paid = dict((await db.execute(select(Payment.invoice_id, func.sum(Payment.amount)).group_by(Payment.invoice_id))).all())
    gstins: dict[uuid.UUID, set[str]] = {}
    for g in (await db.execute(select(ClientGstin))).scalars():
        gstins.setdefault(g.client_id, set()).add(g.gstin)
    names = {c.id: c.name for c in (await db.execute(select(Client))).scalars()}
    changes: list[dict[str, Any]] = []
    new_err = cleared = 0
    risk_delta = Decimal(0)
    affected = 0
    for inv in invoices:
        # Invoice-date rules are affected from their effective date; deadline rules can affect older invoices too.
        lines = list((await db.execute(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id))).scalars())
        data = invoice_to_data(inv, lines)
        ctx = ValidationContext(client_gstins=gstins.get(inv.client_id, set()), today=today)
        a = {f.code for f in validate_invoice(data, published, ctx)}
        b = {f.code for f in validate_invoice(data, simulated, ctx)}
        ri = RiskInvoice(id=inv.id, supplier_gstin=inv.supplier_gstin, supplier_name=inv.supplier_name, number=inv.number,
                         invoice_date=inv.invoice_date, total=inv.total, itc=inv.itc, blocked_amount=inv.blocked_amount,
                         paid=paid.get(inv.id) or Decimal(0), status=inv.status, claimed_period=inv.claimed_period)
        ra = sum((r.amount for r in assess(ri, published, today)), Decimal(0))
        rb = sum((r.amount for r in assess(ri, simulated, today)), Decimal(0))
        if a != b or ra != rb:
            affected += 1
            added, removed = sorted(b - a), sorted(a - b)
            new_err += len(added)
            cleared += len(removed)
            risk_delta += rb - ra
            if len(changes) < 500:
                changes.append({"invoice_id": str(inv.id), "number": inv.number, "date": inv.invoice_date.isoformat(),
                                "supplier": inv.supplier_name, "client": names.get(inv.client_id),
                                "new_findings": added, "cleared_findings": removed,
                                "risk_before": str(ra), "risk_after": str(rb)})
    rep.changes = changes
    rep.summary = {"invoices_checked": len(invoices), "invoices_affected": affected, "new_findings": new_err,
                   "cleared_findings": cleared, "risk_change": str(risk_delta), "effective_from": start.isoformat()}
    rep.status = "done"
    return rep.summary


async def publish(db: AsyncSession, firm_id: uuid.UUID, draft_id: uuid.UUID, user_id: uuid.UUID | None) -> RuleVersionRow:
    d = await db.get(RuleVersionRow, draft_id)
    if not d or d.status != "draft" or d.firm_id != firm_id:
        raise NotFound("Draft not found.")
    d.status = "published"
    d.published_at = datetime.now(UTC)
    await audit(db, firm_id, "rule.published", "rule_version", d.id, actor_id=user_id, code=d.code, version=d.version,
                effective_from=d.effective_from.isoformat())
    for (cid,) in (await db.execute(select(Client.id))).all():
        await enqueue(db, "client.refresh", {"client_id": str(cid)}, firm_id=firm_id, dedupe_key=f"refresh:{cid}")
    return d

