"""Monthly close workflow (N5): the whole GST month as an auditable checklist.

Deterministic steps do the work; the AI only writes the closing summary. Every step that has an
external or legal effect (IMS actions, sending follow-ups, finalising Table 4) stops at
`needs_approval` until a person approves it. Each step stores its output so the run is a record
of how the month was closed.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.core.audit import audit, jsonable
from lekha.core.errors import AppError, NotFound
from lekha.engines.dates import period_to_date
from lekha.jobs.queue import enqueue
from lekha.models import (
    Client,
    ClientGstin,
    CloseRun,
    CloseStep,
    Document,
    Firm,
    FollowUp,
    Gstr2bImport,
    ImsDecision,
    Invoice,
    RiskItem,
    Supplier,
)

log = structlog.get_logger("close")

STEPS: list[tuple[str, str, bool]] = [
    ("intake", "Check that every bill for the month has been read and reviewed", False),
    ("reconcile", "Reconcile the purchase register with GSTR-2B", False),
    ("ims", "Decide IMS actions (accept / reject / pending)", True),
    ("risk", "Check credit at risk before filing", False),
    ("followups", "Send follow-ups to suppliers with open issues", True),
    ("table4", "Prepare GSTR-3B Table 4 for filing", True),
    ("summary", "Write the month's close summary", False),
]


async def start_close(db: AsyncSession, firm_id: uuid.UUID, client_id: uuid.UUID, period: str,
                      gstin: str | None, trigger: str, user_id: uuid.UUID | None) -> CloseRun:
    gstins = list((await db.execute(select(ClientGstin.gstin).where(ClientGstin.client_id == client_id))).scalars())
    gstin = gstin or (gstins[0] if gstins else None)
    if not gstin or gstin not in gstins:
        raise AppError("Choose one of the client's GSTINs.")
    running = (await db.execute(select(CloseRun).where(CloseRun.client_id == client_id, CloseRun.period == period,
                                                       CloseRun.client_gstin == gstin,
                                                       CloseRun.status.in_(("running", "awaiting_approval"))))).scalars().first()
    if running:
        return running
    run = CloseRun(firm_id=firm_id, client_id=client_id, client_gstin=gstin, period=period, trigger=trigger)
    db.add(run)
    await db.flush()
    for i, (code, title, approval) in enumerate(STEPS):
        db.add(CloseStep(firm_id=firm_id, run_id=run.id, position=i, code=code, title=title, approval_required=approval))
    await db.flush()
    await audit(db, firm_id, "close.started", "close_run", run.id, actor_id=user_id,
                actor_kind="user" if user_id else "system", period=period, trigger=trigger)
    await enqueue(db, "close.run", {"run_id": str(run.id)}, firm_id=firm_id, dedupe_key=f"close:{run.id}")
    return run


async def _steps(db: AsyncSession, run_id: uuid.UUID) -> dict[str, CloseStep]:
    return {s.code: s for s in (await db.execute(select(CloseStep).where(CloseStep.run_id == run_id)
                                                 .order_by(CloseStep.position))).scalars()}


async def execute_close(db: AsyncSession, firm_id: uuid.UUID, run_id: uuid.UUID) -> dict[str, Any]:
    run = await db.get(CloseRun, run_id)
    if not run:
        return {"skipped": "run not found"}
    steps = await _steps(db, run_id)
    blocked = False
    for code, step in steps.items():
        if step.status not in ("pending", "failed"):
            continue
        if blocked:
            step.status = "skipped"
            continue
        step.status = "running"
        await db.flush()
        try:
            out = await RUNNERS[code](db, run)
            step.output = jsonable(out)
            step.error = None
            step.status = "needs_approval" if step.approval_required and not out.get("nothing_to_approve") else "done"
        except AppError as e:
            step.status, step.error = "failed", e.message
            blocked = code in ("reconcile",)
        except Exception as e:  # noqa: BLE001 - recorded on the step
            log.exception("close.step_failed", step=code)
            step.status, step.error = "failed", f"{type(e).__name__}: {e}"
            blocked = code in ("reconcile",)
    await _update_run_status(db, run, steps)
    return {"status": run.status}


async def _update_run_status(db: AsyncSession, run: CloseRun, steps: dict[str, CloseStep]) -> None:
    st = [s.status for s in steps.values()]
    if "needs_approval" in st:
        run.status = "awaiting_approval"
    elif "failed" in st:
        run.status = "failed"
    elif all(s in ("done", "approved", "skipped") for s in st):
        run.status = "approved"
        run.approved_at = run.approved_at or datetime.now(UTC)
    run.summary = {"steps": {k: s.status for k, s in steps.items()},
                   "headline": (steps["summary"].output or {}).get("headline") if "summary" in steps else None}


# --- step runners -------------------------------------------------------------------------------

async def _intake(db: AsyncSession, run: CloseRun) -> dict[str, Any]:
    pending_docs = (await db.execute(select(func.count()).select_from(Document).where(
        Document.client_id == run.client_id, Document.status.in_(("queued", "processing"))))).scalar_one()
    failed_docs = (await db.execute(select(func.count()).select_from(Document).where(
        Document.client_id == run.client_id, Document.status == "failed"))).scalar_one()
    in_review = (await db.execute(select(func.count()).select_from(Invoice).where(
        Invoice.client_id == run.client_id, Invoice.period == run.period, Invoice.status == "review"))).scalar_one()
    approved = (await db.execute(select(func.count()).select_from(Invoice).where(
        Invoice.client_id == run.client_id, Invoice.period == run.period, Invoice.status == "approved"))).scalar_one()
    notes = []
    if pending_docs:
        notes.append(f"{pending_docs} document(s) are still being read.")
    if failed_docs:
        notes.append(f"{failed_docs} document(s) could not be read — retry them in the Inbox.")
    if in_review:
        notes.append(f"{in_review} invoice(s) for the month still need review; they are reconciled but not claimed.")
    return {"approved": approved, "in_review": in_review, "pending_documents": pending_docs,
            "failed_documents": failed_docs, "notes": notes, "ok": not (pending_docs or failed_docs or in_review)}


async def _reconcile(db: AsyncSession, run: CloseRun) -> dict[str, Any]:
    imp = (await db.execute(select(Gstr2bImport).where(Gstr2bImport.client_id == run.client_id,
                                                       Gstr2bImport.client_gstin == run.client_gstin,
                                                       Gstr2bImport.period == run.period,
                                                       Gstr2bImport.superseded.is_(False)))).scalars().first()
    if not imp:
        raise AppError("GSTR-2B for this period has not been imported yet.")
    from lekha.services.reconcile import run_reconciliation

    return await run_reconciliation(db, run.firm_id, run.client_id, run.period)


async def _ims(db: AsyncSession, run: CloseRun) -> dict[str, Any]:
    from lekha.engines.ims import ims_deadline
    from lekha.models import Gstr2bRecord

    rows = (await db.execute(select(ImsDecision, Gstr2bRecord).join(Gstr2bRecord, Gstr2bRecord.id == ImsDecision.record_id)
                             .where(ImsDecision.client_id == run.client_id, ImsDecision.period == run.period))).all()
    undecided = [(d, r) for d, r in rows if not d.decided_action]
    rec: dict[str, dict[str, Any]] = {}
    for d, r in undecided:
        x = rec.setdefault(d.recommendation, {"count": 0, "itc": Decimal(0)})
        x["count"] += 1
        x["itc"] += r.igst + r.cgst + r.sgst + r.cess
    rejects = [{"supplier": r.supplier_name or r.supplier_gstin, "number": r.number, "reason": d.reason_text}
               for d, r in undecided if d.recommendation == "reject"][:10]
    return {"undecided": len(undecided), "recommended": {k: {"count": v["count"], "itc": str(v["itc"])} for k, v in rec.items()},
            "to_reject": rejects, "deadline": ims_deadline(run.period).isoformat(),
            "nothing_to_approve": not undecided,
            "approve_means": "Apply Lekha's recommendation to every undecided record."}


async def _risk(db: AsyncSession, run: CloseRun) -> dict[str, Any]:
    from lekha.services.risk import ledger, recompute_client

    await recompute_client(db, run.firm_id, run.client_id)
    led = await ledger(db, run.client_id)
    due_3b = period_to_date(run.period).replace(day=1)
    due_3b = (due_3b + timedelta(days=32)).replace(day=20)
    urgent = [{"title": r.title, "amount": str(r.amount), "due": r.due_date.isoformat() if r.due_date else None}
              for r in led["items"] if r.due_date and r.due_date <= due_3b][:10]
    return {"totals": led["totals"], "urgent_before_filing": urgent, "gstr3b_due": due_3b.isoformat()}


async def _followups(db: AsyncSession, run: CloseRun) -> dict[str, Any]:
    from lekha.services.suppliers import CHASE_KINDS, draft_followup

    gstins = set((await db.execute(select(RiskItem.supplier_gstin).where(
        RiskItem.client_id == run.client_id, RiskItem.status == "open", RiskItem.kind.in_(CHASE_KINDS)))).scalars())
    gstins.discard(None)
    firm = await db.get(Firm, run.firm_id)
    drafted = []
    recent = datetime.now(UTC) - timedelta(days=3)
    for g in sorted(gstins):
        sup = (await db.execute(select(Supplier).where(Supplier.gstin == g))).scalar_one_or_none()
        if not sup:
            continue
        exists = (await db.execute(select(FollowUp).where(FollowUp.supplier_id == sup.id, FollowUp.client_id == run.client_id,
                                                          FollowUp.created_at >= recent))).scalars().first()
        if exists:
            continue
        try:
            f = await draft_followup(db, run.firm_id, sup.id, run.client_id, firm.name if firm else "", None)
            drafted.append({"followup_id": str(f.id), "supplier": sup.trade_name or sup.gstin, "amount": str(f.amount),
                            "language": f.language})
        except AppError:
            continue
    return {"drafted": drafted, "nothing_to_approve": not drafted,
            "approve_means": "Mark these follow-ups as sent (and email them where an email service is configured)."}


async def _table4(db: AsyncSession, run: CloseRun) -> dict[str, Any]:
    from lekha.services.table4 import compute

    t = await compute(db, run.client_id, run.client_gstin, run.period)
    return {"net": t["cells"]["4C"]["total"], "cells": {k: v["total"] for k, v in t["cells"].items()},
            "included": t["included"], "records": t["records"],
            "approve_means": "Finalise Table 4 and record these invoices as claimed in this period."}


async def _summary(db: AsyncSession, run: CloseRun) -> dict[str, Any]:
    steps = await _steps(db, run.id)
    client = await db.get(Client, run.client_id)
    facts = {k: s.output for k, s in steps.items() if k != "summary"}
    t4 = facts.get("table4") or {}
    risk = (facts.get("risk") or {}).get("totals", {})
    rec = (facts.get("reconcile") or {}).get("counts", {})
    headline = (f"Net ITC ₹{Decimal(t4.get('net', '0')):,.0f} ready to claim; ₹{Decimal(risk.get('itc_at_risk', '0')):,.0f} "
                f"at risk; {rec.get('missing_in_2b', 0)} invoice(s) not yet reported by suppliers.")
    text = None
    from lekha.ai.client import AIRefused, AIUnavailable, ai_enabled, complete

    if ai_enabled():
        try:
            res = await complete(
                system="You write short, precise month-end GST close notes for an Indian CA firm partner. Use only "
                       "the facts given. Plain English, no markdown headers, at most 6 bullet points starting with '• '. "
                       "Mention rupee amounts in Indian format (₹1,23,456). End with the one most important action.",
                content=f"Client: {client.name if client else ''}\nPeriod: {run.period}\nFacts (JSON):\n"
                        f"{json.dumps(jsonable(facts))[:12000]}", max_tokens=2000, fast=True)
            text = res.text.strip()
        except (AIUnavailable, AIRefused):
            text = None
    if not text:
        bullets = [f"• {headline}"]
        intake = facts.get("intake") or {}
        bullets += [f"• {n}" for n in intake.get("notes", [])]
        ims = facts.get("ims") or {}
        if ims.get("undecided"):
            bullets.append(f"• {ims['undecided']} IMS record(s) awaiting a decision before {ims.get('deadline')}.")
        fu = facts.get("followups") or {}
        if fu.get("drafted"):
            bullets.append(f"• {len(fu['drafted'])} supplier follow-up(s) drafted.")
        text = "\n".join(bullets)
    return {"headline": headline, "text": text}


RUNNERS = {"intake": _intake, "reconcile": _reconcile, "ims": _ims, "risk": _risk, "followups": _followups,
           "table4": _table4, "summary": _summary}


async def approve_step(db: AsyncSession, firm_id: uuid.UUID, run_id: uuid.UUID, code: str,
                       user_id: uuid.UUID | None) -> CloseRun:
    run = await db.get(CloseRun, run_id)
    if not run:
        raise NotFound("Close run not found.")
    steps = await _steps(db, run_id)
    step = steps.get(code)
    if not step or step.status != "needs_approval":
        raise AppError("This step is not waiting for approval.")
    if code == "ims":
        from lekha.services.reconcile import decide_ims

        ids = list((await db.execute(select(ImsDecision.record_id).where(
            ImsDecision.client_id == run.client_id, ImsDecision.period == run.period,
            ImsDecision.decided_action.is_(None)))).scalars())
        await decide_ims(db, firm_id, ids, "use_recommendation", user_id, remark="Approved in monthly close")
    elif code == "followups":
        from lekha.services.suppliers import mark_sent

        for d in (step.output or {}).get("drafted", []):
            f = await db.get(FollowUp, uuid.UUID(d["followup_id"]))
            if f and f.status == "draft":
                await mark_sent(db, f, user_id)
    elif code == "table4":
        from lekha.services.table4 import finalise

        await finalise(db, firm_id, run.client_id, run.client_gstin, run.period, user_id)
    step.status = "approved"
    step.approved_by = user_id
    step.approved_at = datetime.now(UTC)
    await audit(db, firm_id, "close.step_approved", "close_run", run.id, actor_id=user_id, step=code)
    await _update_run_status(db, run, steps)
    if run.status == "approved":
        run.approved_by = user_id
    return run
