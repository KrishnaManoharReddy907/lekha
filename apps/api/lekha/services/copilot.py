"""Copilot (M13): plain-language questions over a client's data, answered from read-only tools.

Provider-neutral agent loop: at each step the model returns JSON choosing either one tool call
or the final answer (structured outputs work the same on Gemini and Claude). Tools are read-only
functions over the tenant-scoped session, so the copilot can never change data or see another
firm's records (row-level security applies underneath). Answers cite invoice numbers.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Awaitable, Callable

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.core.audit import jsonable
from lekha.models import (
    Client,
    CopilotMessage,
    Gstr2bImport,
    Invoice,
    Match,
    RiskItem,
    Supplier,
    ValidationResult,
)

MAX_STEPS = 5
LANG = {"en": "English", "hi": "Hindi (Devanagari script)", "te": "Telugu (Telugu script)"}

Tool = Callable[[AsyncSession, uuid.UUID, dict[str, Any]], Awaitable[Any]]


async def t_summary(db: AsyncSession, client_id: uuid.UUID, _a: dict[str, Any]) -> dict[str, Any]:
    q = select(Invoice.status, func.count(), func.sum(Invoice.igst + Invoice.cgst + Invoice.sgst + Invoice.cess)).where(
        Invoice.client_id == client_id).group_by(Invoice.status)
    by = {s: {"count": n, "itc": str(t or 0)} for s, n, t in (await db.execute(q)).all()}
    risk = dict((await db.execute(select(RiskItem.kind, func.sum(RiskItem.amount)).where(
        RiskItem.client_id == client_id, RiskItem.status == "open").group_by(RiskItem.kind))).all())
    last = (await db.execute(select(Gstr2bImport.period).where(Gstr2bImport.client_id == client_id,
                                                               Gstr2bImport.superseded.is_(False))
                             .order_by(Gstr2bImport.period.desc()))).scalars().all()
    latest = max(last, key=lambda p: (p[2:], p[:2])) if last else None
    return {"invoices_by_status": by, "open_risk_by_kind": {k: str(v) for k, v in risk.items()},
            "latest_gstr2b_period": latest}


async def t_search_invoices(db: AsyncSession, client_id: uuid.UUID, a: dict[str, Any]) -> list[dict[str, Any]]:
    q = select(Invoice).where(Invoice.client_id == client_id)
    if a.get("text"):
        like = f"%{a['text']}%"
        q = q.where(or_(Invoice.number.ilike(like), Invoice.supplier_name.ilike(like), Invoice.supplier_gstin.ilike(like)))
    if a.get("status"):
        q = q.where(Invoice.status == a["status"])
    if a.get("period"):
        q = q.where(Invoice.period == a["period"])
    if a.get("min_total"):
        q = q.where(Invoice.total >= Decimal(str(a["min_total"])))
    order = Invoice.total.desc() if a.get("sort") == "largest" else Invoice.invoice_date.desc()
    rows = (await db.execute(q.order_by(order).limit(min(int(a.get("limit") or 15), 30)))).scalars()
    return [{"number": i.number, "date": i.invoice_date, "supplier": i.supplier_name, "gstin": i.supplier_gstin,
             "total": i.total, "itc": i.itc, "status": i.status, "flags": [f.get("code") for f in i.flags or []]}
            for i in rows]


async def t_risk(db: AsyncSession, client_id: uuid.UUID, a: dict[str, Any]) -> list[dict[str, Any]]:
    q = select(RiskItem).where(RiskItem.client_id == client_id, RiskItem.status == "open")
    if a.get("kind"):
        q = q.where(RiskItem.kind == a["kind"])
    rows = (await db.execute(q.order_by(RiskItem.amount.desc()).limit(20))).scalars()
    return [{"kind": r.kind, "title": r.title, "amount": r.amount, "due": r.due_date, "severity": r.severity} for r in rows]


async def t_suppliers(db: AsyncSession, client_id: uuid.UUID, a: dict[str, Any]) -> list[dict[str, Any]]:
    q = select(Supplier)
    if a.get("text"):
        like = f"%{a['text']}%"
        q = q.where(or_(Supplier.trade_name.ilike(like), Supplier.legal_name.ilike(like), Supplier.gstin.ilike(like)))
    sups = list((await db.execute(q)).scalars())
    risk = dict((await db.execute(select(RiskItem.supplier_gstin, func.sum(RiskItem.amount)).where(
        RiskItem.client_id == client_id, RiskItem.status == "open").group_by(RiskItem.supplier_gstin))).all())
    out = [{"name": s.trade_name or s.legal_name, "gstin": s.gstin, "score": s.score,
            "grade": (s.score_detail or {}).get("grade"), "on_time_rate": (s.score_detail or {}).get("on_time_rate"),
            "msme": s.msme_category, "open_risk": risk.get(s.gstin, 0)} for s in sups]
    key = a.get("sort") or "open_risk"
    out.sort(key=lambda x: (x["score"] or 100) if key == "worst_score" else -float(x["open_risk"] or 0))
    return out[:15]


async def t_reconciliation(db: AsyncSession, client_id: uuid.UUID, a: dict[str, Any]) -> dict[str, Any]:
    period = a.get("period")
    if not period:
        periods = (await db.execute(select(Gstr2bImport.period).where(Gstr2bImport.client_id == client_id,
                                                                      Gstr2bImport.superseded.is_(False)))).scalars().all()
        period = max(periods, key=lambda p: (p[2:], p[:2])) if periods else None
    if not period:
        return {"error": "No GSTR-2B imported yet."}
    counts = dict((await db.execute(select(Match.status, func.count()).where(
        Match.client_id == client_id, Match.period == period).group_by(Match.status))).all())
    probs = (await db.execute(select(Match, Invoice).join(Invoice, Invoice.id == Match.invoice_id, isouter=True).where(
        Match.client_id == client_id, Match.period == period, Match.status.in_(("mismatch", "missing_in_2b")))
        .order_by(Match.itc_books.desc()).limit(10))).all()
    return {"period": period, "counts": counts, "top_problems": [
        {"status": m.status, "number": i.number if i else None, "supplier": i.supplier_name if i else None,
         "itc_books": m.itc_books, "itc_2b": m.itc_2b, "why": m.explanation.get("summary")} for m, i in probs]}


async def t_deadlines(db: AsyncSession, client_id: uuid.UUID, _a: dict[str, Any]) -> list[dict[str, Any]]:
    horizon = date.today() + timedelta(days=45)
    rows = (await db.execute(select(RiskItem).where(RiskItem.client_id == client_id, RiskItem.status == "open",
                                                    RiskItem.due_date.is_not(None), RiskItem.due_date <= horizon)
                             .order_by(RiskItem.due_date).limit(20))).scalars()
    return [{"due": r.due_date, "what": r.title, "amount": r.amount} for r in rows]


async def t_explain_invoice(db: AsyncSession, client_id: uuid.UUID, a: dict[str, Any]) -> dict[str, Any]:
    inv = (await db.execute(select(Invoice).where(Invoice.client_id == client_id,
                                                  Invoice.number.ilike(f"%{a.get('number', '')}%")))).scalars().first()
    if not inv:
        return {"error": "No such invoice."}
    vals = (await db.execute(select(ValidationResult).where(ValidationResult.invoice_id == inv.id))).scalars()
    m = (await db.execute(select(Match).where(Match.invoice_id == inv.id).order_by(Match.created_at.desc()))).scalars().first()
    risks = (await db.execute(select(RiskItem).where(RiskItem.invoice_id == inv.id, RiskItem.status == "open"))).scalars()
    return {"number": inv.number, "supplier": inv.supplier_name, "date": inv.invoice_date, "total": inv.total,
            "itc": inv.itc, "status": inv.status, "checks": [{"severity": v.severity, "message": v.message} for v in vals],
            "gstr2b": m.explanation if m else None, "risks": [{"title": r.title, "amount": r.amount} for r in risks]}


TOOLS: dict[str, tuple[Tool, str]] = {
    "summary": (t_summary, "Overall numbers for the client: invoices by status with ITC, open risk by kind, latest GSTR-2B period. No args."),
    "search_invoices": (t_search_invoices, "Find invoices. Args: text (number/supplier/GSTIN), status (review|approved|rejected|duplicate), period (MMYYYY), min_total, sort ('largest'|'recent'), limit."),
    "risk_items": (t_risk, "Open credit-at-risk items, largest first. Args: kind (NOT_IN_2B|VALUE_MISMATCH|WRONG_TAX_HEAD|R37_PAYMENT|R37A_SUPPLIER_3B|MSME_43BH|S16_4_DEADLINE|S17_5_BLOCKED|NOT_RECEIVED) optional."),
    "suppliers": (t_suppliers, "Suppliers with compliance score and open risk. Args: text (name/GSTIN) optional, sort ('open_risk'|'worst_score')."),
    "reconciliation": (t_reconciliation, "GSTR-2B reconciliation counts and top problems. Args: period (MMYYYY) optional — defaults to latest."),
    "deadlines": (t_deadlines, "Deadlines in the next 45 days with amounts. No args."),
    "explain_invoice": (t_explain_invoice, "Everything about one invoice: checks, GSTR-2B match explanation, risks. Args: number."),
}

STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["tool", "answer"]},
        "tool": {"anyOf": [{"type": "string", "enum": list(TOOLS)}, {"type": "null"}]},
        "args_json": {"type": "string"},
        "answer": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["action", "tool", "args_json", "answer"],
    "additionalProperties": False,
}


def _system(client_name: str, language: str) -> str:
    tools = "\n".join(f"- {n}: {d}" for n, (_f, d) in TOOLS.items())
    return f"""You are Lekha's GST copilot for an Indian CA firm, answering questions about the client "{client_name}".
Today is {date.today():%d %b %Y}. You can call read-only tools to look up facts; never guess numbers.

Tools:
{tools}

At each step return JSON: either {{"action":"tool","tool":<name>,"args_json":"<JSON object as a string>","answer":null}}
or {{"action":"answer","tool":null,"args_json":"{{}}","answer":<final answer>}}.
Call at most {MAX_STEPS - 1} tools. Answer in {LANG.get(language, 'English')}, concisely, using Indian number format (₹1,23,456).
Write plain text (no markdown, no asterisks); use "• " for lists. Cite invoice numbers and supplier names from tool results. If the data does not answer the question, say so and suggest
where in Lekha to look. GST terms: ITC = input tax credit, GSTR-2B = monthly supplier-reported ITC statement, IMS =
accept/reject/pending of supplier records. Do not give legal advice beyond the GST rules reflected in the data."""


async def ask(db: AsyncSession, firm_id: uuid.UUID, client_id: uuid.UUID, user_id: uuid.UUID | None, message: str,
              conversation_id: uuid.UUID | None, language: str = "en") -> dict[str, Any]:
    from lekha.ai.client import AIRefused, AIUnavailable, ai_enabled, complete

    conversation_id = conversation_id or uuid.uuid4()
    client = await db.get(Client, client_id)
    history = list((await db.execute(select(CopilotMessage).where(CopilotMessage.conversation_id == conversation_id)
                                     .order_by(CopilotMessage.created_at))).scalars())[-8:]
    db.add(CopilotMessage(firm_id=firm_id, user_id=user_id, client_id=client_id, conversation_id=conversation_id,
                          role="user", content=message[:4000]))
    calls: list[dict[str, Any]] = []
    answer: str | None = None
    tokens_in = tokens_out = 0
    if ai_enabled():
        transcript = "".join(f"{m.role.upper()}: {m.content}\n" for m in history) + f"USER: {message}\n"
        try:
            for _ in range(MAX_STEPS):
                res = await complete(system=_system(client.name if client else "", language), content=transcript,
                                     schema=STEP_SCHEMA, max_tokens=4000, fast=True)
                tokens_in += res.input_tokens
                tokens_out += res.output_tokens
                step = res.data or {}
                if step.get("action") == "answer" or not step.get("tool") or len(calls) >= MAX_STEPS - 1:
                    answer = step.get("answer") or answer
                    if answer:
                        break
                    transcript += "SYSTEM: Give the final answer now.\n"
                    continue
                try:
                    args = json.loads(step.get("args_json") or "{}")
                except json.JSONDecodeError:
                    args = {}
                fn = TOOLS[step["tool"]][0]
                result = jsonable(await fn(db, client_id, args if isinstance(args, dict) else {}))
                calls.append({"tool": step["tool"], "args": args})
                transcript += f"TOOL {step['tool']}({json.dumps(args)}) → {json.dumps(result)[:6000]}\n"
        except (AIUnavailable, AIRefused):
            answer = None
    if not answer:
        answer, calls = await _offline(db, client_id, message)
    db.add(CopilotMessage(firm_id=firm_id, user_id=user_id, client_id=client_id, conversation_id=conversation_id,
                          role="assistant", content=answer, tool_calls=calls, input_tokens=tokens_in,
                          output_tokens=tokens_out))
    return {"conversation_id": str(conversation_id), "answer": answer, "tool_calls": calls}


async def _offline(db: AsyncSession, client_id: uuid.UUID, message: str) -> tuple[str, list[dict[str, Any]]]:
    """Keyword routing when no AI provider is available: still useful, clearly labelled."""
    m = message.lower()
    if re.search(r"deadline|due|when", m):
        rows = await t_deadlines(db, client_id, {})
        body = "\n".join(f"• {r['due']:%d %b}: {r['what']} (₹{r['amount']:,.0f})" for r in rows) or "No deadlines in the next 45 days."
        return f"Upcoming deadlines:\n{body}", [{"tool": "deadlines", "args": {}}]
    if re.search(r"supplier|vendor", m):
        rows = await t_suppliers(db, client_id, {"sort": "open_risk"})
        body = "\n".join(f"• {r['name']} — open risk ₹{float(r['open_risk'] or 0):,.0f}, grade {r['grade'] or '—'}" for r in rows[:8])
        return f"Suppliers holding back the most credit:\n{body}", [{"tool": "suppliers", "args": {"sort": "open_risk"}}]
    rows = await t_risk(db, client_id, {})
    total = sum((Decimal(str(r["amount"])) for r in rows), Decimal(0))
    body = "\n".join(f"• {r['title']} — ₹{r['amount']:,.0f}" for r in rows[:8])
    return (f"Top credit at risk (₹{total:,.0f} across the {len(rows)} largest items):\n{body}\n\n"
            "(Answered without AI. Add a GEMINI_API_KEY for full natural-language answers.)",
            [{"tool": "risk_items", "args": {}}])
