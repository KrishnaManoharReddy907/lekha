"""Notice Defense Pack (N2).

When the department sends DRC-01C (ITC claimed in GSTR-3B exceeds GSTR-2B) or ASMT-10 (scrutiny
discrepancies), Lekha rebuilds the picture for each period from its own records:
  claimed (finalised Table 4, else computed) vs available in GSTR-2B,
then explains the difference with evidence it already holds — re-claims of earlier reversals,
reverse-charge ITC (never in 2B), import IGST, and credit notes — and isolates what remains
unexplained (to be paid via DRC-03). It produces a reply draft, an evidence table and a ZIP bundle
with invoice images. The draft is a starting point for a professional, never a filing.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
import zipfile
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.core.audit import audit, jsonable
from lekha.core.errors import AppError
from lekha.core.storage import get_storage
from lekha.engines.dates import add_months, period_of, period_to_date
from lekha.engines.money import ZERO, inr
from lekha.models import (
    Client,
    Document,
    Gstr2bRecord,
    Invoice,
    Notice,
    NoticePack,
    Payment,
    Table4Snapshot,
)

FORMS = {
    "DRC-01C": "Intimation of difference between ITC available in GSTR-2B and ITC availed in GSTR-3B",
    "ASMT-10": "Notice for intimating discrepancies in the return after scrutiny",
    "DRC-01B": "Intimation of difference in liability between GSTR-1 and GSTR-3B",
    "OTHER": "Other notice",
}


def _periods(start: str, end: str) -> list[str]:
    out, d, last = [], period_to_date(start), period_to_date(end)
    while d <= last and len(out) < 36:
        out.append(period_of(d))
        d = add_months(d, 1)
    return out


async def analyse(db: AsyncSession, notice: Notice) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    total_claimed = total_available = total_explained = ZERO
    for p in _periods(notice.period_from, notice.period_to):
        snap = (await db.execute(select(Table4Snapshot).where(
            Table4Snapshot.client_id == notice.client_id, Table4Snapshot.client_gstin == notice.client_gstin,
            Table4Snapshot.period == p, Table4Snapshot.status == "final").order_by(Table4Snapshot.created_at.desc())
        )).scalars().first()
        if snap:
            cells, sources, basis = snap.cells, snap.sources, "finalised in Lekha"
        else:
            try:
                from lekha.services.table4 import compute

                t = await compute(db, notice.client_id, notice.client_gstin, p)
                cells, sources, basis = t["cells"], t["sources"], "computed (not finalised)"
            except AppError:
                continue
        claimed = Decimal(cells["4C"]["total"]) + Decimal(cells["4D1"]["total"])
        recs = list((await db.execute(select(Gstr2bRecord).where(
            Gstr2bRecord.client_id == notice.client_id, Gstr2bRecord.client_gstin == notice.client_gstin,
            Gstr2bRecord.period == p))).scalars())
        available = sum(((r.igst + r.cgst + r.sgst + r.cess) * (-1 if r.doc_type == "credit_note" else 1)
                         for r in recs if r.itc_available), ZERO)
        explained: list[dict[str, Any]] = []
        for key, why in (("4D1", "Re-claim of ITC reversed in an earlier period after the condition was met "
                                 "(supplier paid / goods received), permitted by Rule 37(2) / Sec 16(2)"),
                         ("4A3", "ITC on inward supplies under reverse charge, paid in cash by us; never appears "
                                 "in GSTR-2B"),
                         ("4A1", "IGST paid on import of goods (Bill of Entry); reflected through ICEGATE")):
            amt = Decimal(cells[key]["total"])
            if amt > 0:
                explained.append({"category": key, "reason": why, "amount": str(amt)})
                for s in sources.get(key, []):
                    evidence.append({"period": p, "category": key, "ref": s["ref"], "label": s["label"],
                                     "amount": s["amount"], "reason": s.get("reason", why)})
        exp_total = sum((Decimal(e["amount"]) for e in explained), ZERO)
        diff = claimed - available
        unexplained = max(ZERO, diff - exp_total)
        rows.append({"period": p, "basis": basis, "claimed": str(claimed), "available_2b": str(available),
                     "difference": str(diff), "explained": explained, "unexplained": str(unexplained)})
        total_claimed += claimed
        total_available += available
        total_explained += min(exp_total, max(ZERO, diff))
    if not rows:
        raise AppError("No GSTR-2B / Table 4 data in Lekha for these periods. Import the GSTR-2B files first.")
    total_diff = total_claimed - total_available
    return {"rows": rows, "evidence": evidence, "totals": {
        "claimed": str(total_claimed), "available_2b": str(total_available), "difference": str(total_diff),
        "explained": str(total_explained), "unexplained": str(max(ZERO, total_diff - total_explained)),
        "notice_amount": str(notice.amount)}}


def _reply_text(notice: Notice, client: Client, a: dict[str, Any]) -> str:
    t = a["totals"]
    lines = [
        "To,\nThe Proper Officer,\nGoods and Services Tax Department",
        "",
        f"Subject: Reply to {notice.form}{' ref. ' + notice.reference if notice.reference else ''} — "
        f"GSTIN {notice.client_gstin} — tax period {notice.period_from} to {notice.period_to}",
        "",
        "Respected Sir/Madam,",
        "",
        f"With reference to the above intimation regarding a difference of {inr(Decimal(notice.amount))} between the "
        f"input tax credit available in GSTR-2B and the credit availed in GSTR-3B, we, {client.name}, submit as "
        "follows:",
        "",
        (f"1. As per our reconciliation, ITC availed is {inr(Decimal(t['claimed']))} against "
         f"{inr(Decimal(t['available_2b']))} available in GSTR-2B for the period, a difference of "
         f"{inr(Decimal(t['difference']))}.") if Decimal(t["difference"]) > 0 else
        (f"1. As per our reconciliation, ITC availed is {inr(Decimal(t['claimed']))}, which is "
         f"{inr(-Decimal(t['difference']))} LOWER than the {inr(Decimal(t['available_2b']))} available in GSTR-2B for "
         "the period. No ITC in excess of GSTR-2B has been availed."),
    ]
    n = 2
    cats: dict[str, Decimal] = {}
    for r in a["rows"]:
        for e in r["explained"]:
            cats[e["reason"]] = cats.get(e["reason"], ZERO) + Decimal(e["amount"])
    for reason, amt in cats.items():
        lines.append(f"{n}. {inr(amt)} relates to: {reason}. Invoice-wise details are enclosed (Annexure A).")
        n += 1
    unexplained = Decimal(t["unexplained"])
    if unexplained > 0:
        lines.append(f"{n}. The balance of {inr(unexplained)} is being reviewed; to the extent it is found ineligible "
                     "it will be paid along with applicable interest through FORM GST DRC-03.")
    else:
        lines.append(f"{n}. In view of the above, the difference stands fully explained and no excess ITC has been "
                     "availed.")
    lines += ["", "We request you to kindly accept this explanation and drop the proceedings. We shall be glad to "
              "furnish any further information required.", "", "Thanking you,", f"For {client.name}",
              "Authorised Signatory", "", "Enclosures: Annexure A — evidence table; copies of invoices and payment "
              "records."]
    return "\n".join(lines)


async def build_pack(db: AsyncSession, firm_id: uuid.UUID, notice_id: uuid.UUID) -> dict[str, Any]:
    notice = await db.get(Notice, notice_id)
    client = await db.get(Client, notice.client_id)
    a = await analyse(db, notice)
    reply = _reply_text(notice, client, a)
    from lekha.ai.client import AIRefused, AIUnavailable, ai_enabled, complete

    if ai_enabled():
        try:
            res = await complete(
                system="You are an experienced Indian GST practitioner. Improve the clarity and formality of the draft "
                       "reply below WITHOUT adding facts, amounts, legal sections or claims that are not already in it. "
                       "Keep the numbered structure and every amount exactly as given. Return only the letter text.",
                content=reply, max_tokens=4000, fast=True)
            if res.text and all(x in res.text for x in (notice.client_gstin,)):
                reply = res.text.strip()
        except (AIUnavailable, AIRefused):
            pass
    version = ((await db.execute(select(func.max(NoticePack.version)).where(NoticePack.notice_id == notice.id)))
               .scalar() or 0) + 1
    pack = NoticePack(firm_id=firm_id, notice_id=notice.id, version=version, analysis=jsonable(a), reply_text=reply,
                      evidence=a["evidence"])
    db.add(pack)
    await db.flush()
    pack.zip_key = await _zip(db, firm_id, notice, pack, a, reply)
    await audit(db, firm_id, "notice.pack_built", "notice", notice.id, actor_kind="system", version=version,
                unexplained=a["totals"]["unexplained"])
    return {"pack_id": str(pack.id), "version": version}


async def _zip(db: AsyncSession, firm_id: uuid.UUID, notice: Notice, pack: NoticePack, a: dict[str, Any],
               reply: str) -> str:
    st = get_storage()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("01-reply-draft.txt", reply)
        z.writestr("02-reply-draft.pdf", _pdf(reply))
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Period", "Category", "Document", "Amount (Rs)", "Explanation"])
        for e in a["evidence"]:
            w.writerow([e["period"], e["category"], e["label"], e["amount"], e["reason"]])
        z.writestr("03-annexure-A-evidence.csv", out.getvalue())
        z.writestr("04-period-reconciliation.json", json.dumps(a["rows"], indent=2))
        inv_ids = [uuid.UUID(e["ref"][4:]) for e in a["evidence"] if e["ref"].startswith("inv:")]
        for inv in (await db.execute(select(Invoice).where(Invoice.id.in_(inv_ids)))).scalars():
            pays = (await db.execute(select(Payment).where(Payment.invoice_id == inv.id))).scalars().all()
            z.writestr(f"invoices/{inv.number.replace('/', '-')}-details.json", json.dumps(jsonable({
                "number": inv.number, "date": inv.invoice_date, "supplier": inv.supplier_name,
                "supplier_gstin": inv.supplier_gstin, "taxable": inv.taxable_value, "itc": inv.itc,
                "payments": [{"amount": p.amount, "paid_on": p.paid_on, "reference": p.reference} for p in pays]}),
                indent=2))
            if inv.document_id:
                doc = await db.get(Document, inv.document_id)
                if doc and doc.page_images:
                    try:
                        z.writestr(f"invoices/{inv.number.replace('/', '-')}-page1.png", await st.get(doc.page_images[0]))
                    except (OSError, FileNotFoundError):
                        pass
    key = f"firms/{firm_id}/notices/{notice.id}/pack-v{pack.version}.zip"
    await st.put(key, buf.getvalue(), "application/zip")
    return key


def _pdf(text: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 60
    c.setFont("Helvetica", 10)
    for para in text.replace("₹", "Rs. ").split("\n"):
        words, line = para.split(" "), ""
        for word in words:
            if c.stringWidth(line + " " + word, "Helvetica", 10) > w - 120:
                c.drawString(60, y, line)
                y -= 14
                line = word
                if y < 60:
                    c.showPage()
                    c.setFont("Helvetica", 10)
                    y = h - 60
            else:
                line = (line + " " + word).strip()
        c.drawString(60, y, line)
        y -= 14
        if y < 60:
            c.showPage()
            c.setFont("Helvetica", 10)
            y = h - 60
    c.save()
    return buf.getvalue()


def form_options() -> list[dict[str, str]]:
    return [{"form": k, "title": v} for k, v in FORMS.items()]


