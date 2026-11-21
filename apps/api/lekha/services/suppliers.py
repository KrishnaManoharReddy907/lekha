"""Supplier hub (M10): scorecards, follow-up drafting in the supplier's language, self-service links."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.core.audit import audit
from lekha.core.config import get_settings
from lekha.core.errors import AppError, NotFound
from lekha.core.security import hash_token, new_token
from lekha.engines.dates import months_between, period_to_date
from lekha.engines.money import ZERO, inr
from lekha.engines.supplier_score import SupplierStats, score
from lekha.models import Client, FollowUp, Gstr2bImport, Invoice, Match, PublicLink, RiskItem, Supplier


async def rescore_suppliers(db: AsyncSession, firm_id: uuid.UUID) -> dict[str, int]:
    stats: dict[str, SupplierStats] = defaultdict(SupplierStats)
    latest: dict[str, date] = {}
    for imp in (await db.execute(select(Gstr2bImport).where(Gstr2bImport.superseded.is_(False)))).scalars():
        d = period_to_date(imp.period)
        latest[imp.client_gstin] = max(latest.get(imp.client_gstin, d), d)
    matches: dict[uuid.UUID, Match] = {}
    for m in (await db.execute(select(Match).where(Match.invoice_id.is_not(None)))).scalars():
        prev = matches.get(m.invoice_id)
        if prev is None or (prev.status == "missing_in_2b" and m.status != "missing_in_2b"):
            matches[m.invoice_id] = m
    for inv in (await db.execute(select(Invoice).where(Invoice.status.in_(("approved", "review"))))).scalars():
        if not inv.supplier_gstin or not inv.period:
            continue
        s = stats[inv.supplier_gstin]
        s.invoices += 1
        m = matches.get(inv.id)
        if m and m.status in ("matched", "mismatch"):
            lag = months_between(period_to_date(inv.period), period_to_date(m.period))
            if lag <= 0:
                s.on_time += 1
            else:
                s.late += 1
                s.total_lag_months += lag
            if m.status == "mismatch":
                s.mismatched += 1
        elif inv.client_gstin in latest and period_to_date(inv.period) <= latest[inv.client_gstin]:
            s.missing += 1
    for f in (await db.execute(select(FollowUp))).scalars():
        sup = await db.get(Supplier, f.supplier_id)
        if sup and f.status != "draft":
            stats[sup.gstin].followups += 1
            if f.status in ("responded", "resolved"):
                stats[sup.gstin].responded += 1
    n = 0
    for sup in (await db.execute(select(Supplier))).scalars():
        st = stats.get(sup.gstin)
        if not st:
            continue
        sc = score(st)
        sup.score = sc["score"]
        sup.score_detail = {**(sup.score_detail or {}), **sc}
        n += 1
    return {"scored": n}


# --- Follow-up messages ------------------------------------------------------------------------

ISSUE_TEXT = {
    "en": {
        "NOT_IN_2B": "not reported in your GSTR-1 (missing from our GSTR-2B)",
        "VALUE_MISMATCH": "reported with a lower tax amount than on the invoice",
        "WRONG_TAX_HEAD": "reported as IGST instead of CGST+SGST (or the reverse)",
        "R37A_SUPPLIER_3B": "reported, but the tax has not been paid in GSTR-3B",
    },
    "hi": {
        "NOT_IN_2B": "आपके GSTR-1 में दर्ज नहीं है (हमारे GSTR-2B में नहीं दिख रहा)",
        "VALUE_MISMATCH": "बिल से कम टैक्स राशि के साथ दर्ज किया गया है",
        "WRONG_TAX_HEAD": "CGST+SGST की जगह IGST में (या उल्टा) दर्ज किया गया है",
        "R37A_SUPPLIER_3B": "दर्ज है, लेकिन GSTR-3B में टैक्स जमा नहीं हुआ है",
    },
    "te": {
        "NOT_IN_2B": "మీ GSTR-1లో నమోదు కాలేదు (మా GSTR-2Bలో కనిపించడం లేదు)",
        "VALUE_MISMATCH": "ఇన్వాయిస్ కంటే తక్కువ పన్ను మొత్తంతో నమోదు చేయబడింది",
        "WRONG_TAX_HEAD": "CGST+SGSTకి బదులుగా IGSTగా (లేదా వ్యతిరేకంగా) నమోదు చేయబడింది",
        "R37A_SUPPLIER_3B": "నమోదు అయింది, కానీ GSTR-3Bలో పన్ను చెల్లించలేదు",
    },
}

TEMPLATES = {
    "en": {
        "subject": "GST correction needed for {n} invoice(s) — {client}",
        "intro": "Dear {supplier},\n\nWhile reconciling our input tax credit for {client} (GSTIN {gstin}), we found "
                 "the following invoice(s) from you that need attention:\n",
        "item": "• Invoice {number} dated {date} — {issue} (ITC {amount})",
        "ask": "\nPlease file or amend your GSTR-1 so these appear correctly in our GSTR-2B before the 11th. "
               "Together these hold back {total} of input tax credit for us.",
        "link": "\nYou can see the details and tell us the status here (no login needed): {url}",
        "sign": "\n\nThank you,\n{firm}",
    },
    "hi": {
        "subject": "{n} बिल के लिए GST सुधार आवश्यक — {client}",
        "intro": "प्रिय {supplier},\n\n{client} (GSTIN {gstin}) का इनपुट टैक्स क्रेडिट मिलान करते समय आपके निम्नलिखित "
                 "बिलों में समस्या मिली है:\n",
        "item": "• बिल {number} दिनांक {date} — {issue} (ITC {amount})",
        "ask": "\nकृपया 11 तारीख से पहले अपना GSTR-1 फाइल या संशोधित करें ताकि ये हमारे GSTR-2B में सही दिखें। "
               "इनके कारण हमारा कुल {total} इनपुट टैक्स क्रेडिट रुका हुआ है।",
        "link": "\nविवरण देखने और स्थिति बताने के लिए यह लिंक खोलें (लॉगिन की आवश्यकता नहीं): {url}",
        "sign": "\n\nधन्यवाद,\n{firm}",
    },
    "te": {
        "subject": "{n} ఇన్వాయిస్(ల)కు GST సవరణ అవసరం — {client}",
        "intro": "ప్రియమైన {supplier},\n\n{client} (GSTIN {gstin}) ఇన్‌పుట్ ట్యాక్స్ క్రెడిట్ సరిపోల్చేటప్పుడు, మీ నుండి "
                 "వచ్చిన ఈ క్రింది ఇన్వాయిస్‌లలో సమస్య కనిపించింది:\n",
        "item": "• ఇన్వాయిస్ {number} తేదీ {date} — {issue} (ITC {amount})",
        "ask": "\nదయచేసి 11వ తేదీలోపు మీ GSTR-1 ఫైల్ చేయండి లేదా సవరించండి, తద్వారా ఇవి మా GSTR-2Bలో సరిగ్గా కనిపిస్తాయి. "
               "వీటి వల్ల మొత్తం {total} ఇన్‌పుట్ ట్యాక్స్ క్రెడిట్ ఆగిపోయింది.",
        "link": "\nవివరాలు చూడటానికి మరియు స్థితి తెలపడానికి ఈ లింక్ తెరవండి (లాగిన్ అవసరం లేదు): {url}",
        "sign": "\n\nధన్యవాదాలు,\n{firm}",
    },
}
CHASE_KINDS = ("NOT_IN_2B", "VALUE_MISMATCH", "WRONG_TAX_HEAD", "R37A_SUPPLIER_3B")


async def open_issues(db: AsyncSession, supplier_gstin: str, client_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    q = select(RiskItem, Invoice).join(Invoice, Invoice.id == RiskItem.invoice_id).where(
        RiskItem.supplier_gstin == supplier_gstin, RiskItem.kind.in_(CHASE_KINDS),
        RiskItem.status.in_(("open", "accepted")))
    if client_id:
        q = q.where(RiskItem.client_id == client_id)
    return [{"risk_id": str(r.id), "invoice_id": str(i.id), "client_id": str(i.client_id), "kind": r.kind,
             "number": i.number, "date": i.invoice_date.isoformat() if i.invoice_date else None,
             "amount": str(r.amount)} for r, i in (await db.execute(q)).all()]


async def ensure_supplier_link(db: AsyncSession, firm_id: uuid.UUID, supplier: Supplier, user_id: uuid.UUID | None
                               ) -> tuple[PublicLink, str]:
    token = new_token(18)
    link = PublicLink(firm_id=firm_id, kind="supplier", token_hash=hash_token(token), supplier_id=supplier.id,
                      label=f"Supplier portal for {supplier.trade_name or supplier.gstin}",
                      expires_at=datetime.now(UTC) + timedelta(days=60), created_by=user_id)
    db.add(link)
    await db.flush()
    return link, f"{get_settings().web_origin}/s/{token}"


async def draft_followup(db: AsyncSession, firm_id: uuid.UUID, supplier_id: uuid.UUID, client_id: uuid.UUID,
                         firm_name: str, user_id: uuid.UUID | None, channel: str = "email",
                         language: str | None = None) -> FollowUp:
    sup = await db.get(Supplier, supplier_id)
    client = await db.get(Client, client_id)
    if not sup or not client:
        raise NotFound("Supplier or client not found.")
    issues = await open_issues(db, sup.gstin, client_id)
    if not issues:
        raise AppError("This supplier has no open GST issues for this client.")
    lang = language or sup.language or "en"
    t, it = TEMPLATES.get(lang, TEMPLATES["en"]), ISSUE_TEXT.get(lang, ISSUE_TEXT["en"])
    total = sum((Decimal(x["amount"]) for x in issues), ZERO)
    link, url = await ensure_supplier_link(db, firm_id, sup, user_id)
    from lekha.models import ClientGstin

    gstin = (await db.execute(select(ClientGstin.gstin).where(ClientGstin.client_id == client_id))).scalars().first()
    name = sup.trade_name or sup.legal_name or sup.gstin
    body = t["intro"].format(supplier=name, client=client.name, gstin=gstin or "")
    body += "\n".join(t["item"].format(number=x["number"], date=x["date"] or "—", issue=it[x["kind"]],
                                       amount=inr(Decimal(x["amount"]))) for x in issues)
    body += t["ask"].format(total=inr(total)) + t["link"].format(url=url) + t["sign"].format(firm=firm_name)
    prior = list((await db.execute(select(FollowUp).where(FollowUp.supplier_id == sup.id,
                                                          FollowUp.client_id == client_id,
                                                          FollowUp.status.in_(("sent", "escalated"))))).scalars())
    f = FollowUp(firm_id=firm_id, supplier_id=sup.id, client_id=client_id, channel=channel, language=lang,
                 subject=t["subject"].format(n=len(issues), client=client.name), body=body, items=issues, amount=total,
                 status="draft", escalation_level=len(prior), link_id=link.id, created_by=user_id,
                 next_action_on=date.today() + timedelta(days=3))
    db.add(f)
    await db.flush()
    await audit(db, firm_id, "followup.drafted", "followup", f.id, actor_id=user_id, supplier=sup.gstin,
                amount=str(total), language=lang)
    return f


async def mark_sent(db: AsyncSession, f: FollowUp, user_id: uuid.UUID | None) -> FollowUp:
    f.status = "sent" if f.escalation_level == 0 else "escalated"
    f.sent_at = datetime.now(UTC)
    f.next_action_on = date.today() + timedelta(days=4 if f.escalation_level == 0 else 2)
    sup = await db.get(Supplier, f.supplier_id)
    s = get_settings()
    if f.channel == "email" and sup and sup.email and s.resend_api_key:
        import httpx

        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {s.resend_api_key}"},
                             json={"from": s.email_from, "to": [sup.email], "subject": f.subject, "text": f.body})
            f.response = {"email_status": r.status_code}
    await audit(db, f.firm_id, "followup.sent", "followup", f.id, actor_id=user_id, channel=f.channel)
    return f
