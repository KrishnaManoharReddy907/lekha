"""Exports: Tally XML, Excel workbook, client PDF summary, CSV/JSON."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import func, select

from lekha.core.audit import audit
from lekha.core.deps import Ctx, get_ctx
from lekha.core.errors import AppError
from lekha.engines.money import ZERO, inr
from lekha.engines.tally import Ledgers, Voucher, build_xml
from lekha.models import Gstr2bRecord, Invoice, Match, Payment, RiskItem
from lekha.schemas.serialize import row

router = APIRouter(prefix="/api/exports", tags=["exports"])


def _attach(data: bytes | str, mime: str, name: str) -> Response:
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def _invoices(ctx: Ctx, client_id: uuid.UUID, period: str | None, status: str | None = "approved") -> list[Invoice]:
    await ctx.client(client_id)
    q = select(Invoice).where(Invoice.client_id == client_id)
    if status:
        q = q.where(Invoice.status == status)
    if period:
        q = q.where(Invoice.period == period)
    return list((await ctx.db.execute(q.order_by(Invoice.invoice_date))).scalars())


@router.get("/tally")
async def tally(client_id: uuid.UUID, period: str | None = None, ctx: Ctx = Depends(get_ctx)) -> Response:
    client = await ctx.client(client_id)
    invs = await _invoices(ctx, client_id, period)
    if not invs:
        raise AppError("No approved invoices to export for this period.")
    cfg = (client.settings or {}).get("tally_ledgers", {})
    led = Ledgers(**{k: v for k, v in cfg.items() if k in Ledgers.__dataclass_fields__})
    vouchers = [Voucher(number=i.number, date=i.invoice_date or date.today(), supplier_name=i.supplier_name or i.supplier_gstin or "Supplier",
                        supplier_gstin=i.supplier_gstin, place_of_supply=i.place_of_supply, taxable=i.taxable_value,
                        igst=i.igst, cgst=i.cgst, sgst=i.sgst, cess=i.cess, round_off=i.round_off, total=i.total)
                for i in invs if i.invoice_date]
    xml = build_xml(vouchers, led)
    await audit(ctx.db, ctx.firm_id, "export.tally", "client", client_id, actor_id=ctx.user_id, count=len(vouchers))
    return _attach(xml, "application/xml", f"lekha-tally-{period or 'all'}.xml")


@router.get("/register.csv")
async def register_csv(client_id: uuid.UUID, period: str | None = None, ctx: Ctx = Depends(get_ctx)) -> Response:
    invs = await _invoices(ctx, client_id, period, status=None)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Invoice date", "Invoice no", "Supplier", "Supplier GSTIN", "Place of supply", "Taxable", "IGST", "CGST",
                "SGST", "Cess", "Total", "Status", "Confidence", "Blocked ITC", "IRN"])
    for i in invs:
        w.writerow([i.invoice_date, i.number, i.supplier_name, i.supplier_gstin, i.place_of_supply, i.taxable_value, i.igst,
                    i.cgst, i.sgst, i.cess, i.total, i.status, i.confidence, i.blocked_amount, i.irn or ""])
    return _attach(buf.getvalue(), "text/csv", f"purchase-register-{period or 'all'}.csv")


@router.get("/register.json")
async def register_json(client_id: uuid.UUID, period: str | None = None, ctx: Ctx = Depends(get_ctx)) -> Response:
    invs = await _invoices(ctx, client_id, period, status=None)
    return _attach(json.dumps([row(i) for i in invs], indent=1, default=str), "application/json",
                   f"purchase-register-{period or 'all'}.json")


@router.get("/workbook.xlsx")
async def workbook(client_id: uuid.UUID, period: str, ctx: Ctx = Depends(get_ctx)) -> Response:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    client = await ctx.client(client_id)
    wb = Workbook()
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="0F6B5C")

    def sheet(title: str, header: list[str], rows: list[list]) -> None:
        ws = wb.create_sheet(title)
        ws.append(header)
        for c in ws[1]:
            c.font, c.fill = head, fill
        for r in rows:
            ws.append([float(x) if isinstance(x, Decimal) else x for x in r])
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(42, max(10, max(len(str(c.value or "")) for c in col) + 2))
        ws.freeze_panes = "A2"

    wb.remove(wb.active)
    invs = await _invoices(ctx, client_id, period, status=None)
    sheet("Purchase register", ["Date", "Number", "Supplier", "GSTIN", "Taxable", "IGST", "CGST", "SGST", "Total", "Status"],
          [[i.invoice_date, i.number, i.supplier_name, i.supplier_gstin, i.taxable_value, i.igst, i.cgst, i.sgst, i.total,
            i.status] for i in invs])
    ms = (await ctx.db.execute(select(Match, Invoice, Gstr2bRecord).join(Invoice, Invoice.id == Match.invoice_id, isouter=True)
                               .join(Gstr2bRecord, Gstr2bRecord.id == Match.record_id, isouter=True)
                               .where(Match.client_id == client_id, Match.period == period))).all()
    sheet(f"Reconciliation {period}", ["Status", "Supplier", "Books no", "2B no", "ITC books", "ITC 2B", "Differences", "Explanation"],
          [[m.status, (i.supplier_name if i else None) or (r.supplier_name if r else None), i.number if i else "",
            r.number if r else "", m.itc_books, m.itc_2b, ", ".join(m.mismatch_types or []), m.explanation.get("summary")]
           for m, i, r in ms])
    risks = (await ctx.db.execute(select(RiskItem).where(RiskItem.client_id == client_id, RiskItem.status == "open")
                                  .order_by(RiskItem.due_date))).scalars()
    sheet("Credit at risk", ["Kind", "Title", "Amount", "Due", "Severity", "Rule"],
          [[r.kind, r.title, r.amount, r.due_date, r.severity, r.rule] for r in risks])
    out = io.BytesIO()
    wb.save(out)
    await audit(ctx.db, ctx.firm_id, "export.workbook", "client", client_id, actor_id=ctx.user_id, period=period)
    return _attach(out.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   f"lekha-{client.name[:20].replace(' ', '-')}-{period}.xlsx")


@router.get("/summary.pdf")
async def summary_pdf(client_id: uuid.UUID, period: str, ctx: Ctx = Depends(get_ctx)) -> Response:
    """One-page plain-language summary a CA can send to the business owner."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    client = await ctx.client(client_id)
    invs = await _invoices(ctx, client_id, period, status=None)
    approved = [i for i in invs if i.status == "approved"]
    itc = sum((i.itc for i in approved), ZERO)
    counts = dict((await ctx.db.execute(select(Match.status, func.count()).where(
        Match.client_id == client_id, Match.period == period).group_by(Match.status))).all())
    risks = list((await ctx.db.execute(select(RiskItem).where(RiskItem.client_id == client_id, RiskItem.status == "open")
                                       .order_by(RiskItem.amount.desc()))).scalars())
    at_risk = sum((r.amount for r in risks if r.kind != "MSME_43BH"), ZERO)
    unpaid = (await ctx.db.execute(select(func.sum(Invoice.total)).where(Invoice.client_id == client_id,
                                                                         Invoice.status == "approved"))).scalar() or 0
    paid = (await ctx.db.execute(select(func.sum(Payment.amount)).join(Invoice, Invoice.id == Payment.invoice_id).where(
        Invoice.client_id == client_id))).scalar() or 0

    def rs(v: Decimal) -> str:
        return inr(v).replace("₹", "Rs. ")

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    green = HexColor("#0F6B5C")
    c.setFillColor(green)
    c.rect(0, H - 90, W, 90, fill=1, stroke=0)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, H - 50, f"GST credit summary — {client.name}")
    c.setFont("Helvetica", 11)
    c.drawString(40, H - 70, f"Return period {period[:2]}/{period[2:]} · prepared {date.today():%d %b %Y} with Lekha")
    y = H - 130
    c.setFillColor(HexColor("#141D1A"))
    tiles = [("Tax credit ready to claim", rs(itc)), ("Credit at risk", rs(at_risk)),
             ("Invoices reviewed", str(len(approved))), ("Missing in GSTR-2B", str(counts.get("missing_in_2b", 0)))]
    for k, (label, val) in enumerate(tiles):
        x = 40 + (k % 2) * 260
        yy = y - (k // 2) * 70
        c.setFont("Helvetica", 10)
        c.drawString(x, yy, label.upper())
        c.setFont("Helvetica-Bold", 20)
        c.drawString(x, yy - 24, val)
    y -= 170
    c.setFont("Helvetica-Bold", 13)
    c.drawString(40, y, "What needs your attention")
    y -= 20
    c.setFont("Helvetica", 10.5)
    for r in risks[:12]:
        line = f"• {r.title} — {rs(r.amount)}" + (f" (by {r.due_date:%d %b %Y})" if r.due_date else "")
        c.drawString(46, y, line[:105])
        y -= 16
        if y < 120:
            break
    if not risks:
        c.drawString(46, y, "• Nothing at risk. Well done.")
        y -= 16
    y -= 14
    c.setFont("Helvetica", 10)
    c.setFillColor(HexColor("#44514C"))
    c.drawString(40, y, f"Outstanding to suppliers: {rs(Decimal(str(unpaid)) - Decimal(str(paid)))}. Pay within 180 days to keep the credit;")
    c.drawString(40, y - 14, "MSME suppliers within 45 days to keep the income-tax deduction.")
    c.setFont("Helvetica-Oblique", 8.5)
    c.drawString(40, 40, "Prepared from your GST data. Recommendations are for review by your accountant; this is not legal advice.")
    c.save()
    await audit(ctx.db, ctx.firm_id, "export.summary_pdf", "client", client_id, actor_id=ctx.user_id, period=period)
    return _attach(buf.getvalue(), "application/pdf", f"gst-summary-{period}.pdf")


