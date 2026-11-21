"""Synthetic GST invoice generator (M15).

Produces realistic Indian purchase invoices with ground truth, in several visual templates, as
digital PDFs, clean images and degraded "phone photos". Used for the benchmark, tests and the
demo workspace. No real business data is used; GSTINs are syntactically valid but fictitious.
"""

from __future__ import annotations

import io
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from lekha.bench.catalog import ITEMS, invoice_number, make_party, rate_for
from lekha.engines.qr import make_einvoice_qr_text
from lekha.engines.states import STATES

D2 = Decimal("0.01")


def _q(x: Decimal) -> Decimal:
    return x.quantize(D2, ROUND_HALF_UP)


@dataclass
class SynthInvoice:
    supplier: dict
    buyer: dict
    number: str
    date: date
    place_of_supply: str
    lines: list[dict]
    taxable_value: Decimal
    igst: Decimal
    cgst: Decimal
    sgst: Decimal
    round_off: Decimal
    total: Decimal
    doc_type: str = "invoice"
    irn: str | None = None
    template: int = 0
    reverse_charge: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def truth(self) -> dict[str, Any]:
        return {
            "doc_type": self.doc_type,
            "number": self.number,
            "date": self.date.isoformat(),
            "supplier_gstin": self.supplier["gstin"],
            "supplier_name": self.supplier["name"],
            "buyer_gstin": self.buyer["gstin"],
            "buyer_name": self.buyer["name"],
            "place_of_supply": self.place_of_supply,
            "reverse_charge": self.reverse_charge,
            "taxable_value": str(self.taxable_value),
            "igst": str(self.igst),
            "cgst": str(self.cgst),
            "sgst": str(self.sgst),
            "cess": "0.00",
            "round_off": str(self.round_off),
            "total": str(self.total),
            "irn": self.irn,
            "lines": [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in ln.items()} for ln in self.lines],
        }


def make_invoice(rng: random.Random, buyer: dict, on: date | None = None, supplier: dict | None = None,
                 seq: int | None = None, n_lines: int | None = None, einvoice: bool | None = None,
                 template: int | None = None) -> SynthInvoice:
    on = on or date(2025, 4, 1) + timedelta(days=rng.randint(0, 420))
    supplier = supplier or make_party(rng)
    inter = supplier["state"] != buyer["state"]
    lines = []
    for item in rng.sample(ITEMS, n_lines or rng.choice([1, 1, 2, 2, 3, 4, 5])):
        qty = Decimal(rng.choice([1, 2, 3, 5, 6, 10, 12, 20, 25, 40, 50, 100]))
        price = Decimal(rng.randint(*item[3]))
        taxable = _q(qty * price)
        r = Decimal(rate_for(item, on))
        tax = _q(taxable * r / 100)
        half = _q(tax / 2)
        lines.append({
            "description": item[0], "hsn": item[1], "quantity": qty, "unit": item[2], "unit_price": _q(price),
            "taxable_value": taxable, "tax_rate": str(r.normalize()),
            "igst": tax if inter else Decimal("0.00"), "cgst": Decimal("0.00") if inter else half,
            "sgst": Decimal("0.00") if inter else half, "cess": Decimal("0.00"),
        })
    taxable = sum((ln["taxable_value"] for ln in lines), Decimal("0.00"))
    igst = sum((ln["igst"] for ln in lines), Decimal("0.00"))
    cgst = sum((ln["cgst"] for ln in lines), Decimal("0.00"))
    sgst = sum((ln["sgst"] for ln in lines), Decimal("0.00"))
    gross = taxable + igst + cgst + sgst
    total = gross.quantize(Decimal("1"), ROUND_HALF_UP)
    seq = seq or rng.randint(1, 4000)
    inv = SynthInvoice(
        supplier=supplier, buyer=buyer, number=invoice_number(rng, rng.randint(0, 5), seq, on), date=on,
        place_of_supply=buyer["state"], lines=lines, taxable_value=taxable, igst=igst, cgst=cgst, sgst=sgst,
        round_off=_q(total - gross), total=_q(total), template=template if template is not None else rng.randint(0, 3),
    )
    if einvoice if einvoice is not None else rng.random() < 0.35:
        inv.irn = "".join(rng.choice("0123456789abcdef") for _ in range(64))
    return inv


# ---------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------

def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def _money(v: Decimal) -> str:
    s = f"{v:,.2f}"
    whole, frac = s.split(".")
    digits = whole.replace(",", "")
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join([*parts, tail])
    return f"{whole}.{frac}"


class Canvas:
    def __init__(self, w: int, h: int):
        self.img = Image.new("RGB", (w, h), "white")
        self.d = ImageDraw.Draw(self.img)

    def text(self, xy, s: str, size: int = 18, bold: bool = False, anchor: str = "la", fill=(20, 20, 20)) -> None:
        f = _font(size)
        self.d.text(xy, s, font=f, fill=fill, anchor=anchor)
        if bold:
            self.d.text((xy[0] + 1, xy[1]), s, font=f, fill=fill, anchor=anchor)

    def line(self, xy, width: int = 1, fill=(60, 60, 60)) -> None:
        self.d.line(xy, fill=fill, width=width)

    def rect(self, xy, outline=(60, 60, 60), fill=None, width=1) -> None:
        self.d.rectangle(xy, outline=outline, fill=fill, width=width)


def _qr_image(inv: SynthInvoice, size: int) -> Image.Image | None:
    if not inv.irn:
        return None
    import qrcode

    payload = {
        "SellerGstin": inv.supplier["gstin"], "BuyerGstin": inv.buyer["gstin"], "DocNo": inv.number,
        "DocTyp": "INV", "DocDt": inv.date.strftime("%d/%m/%Y"), "TotInvVal": float(inv.total),
        "ItemCnt": len(inv.lines), "MainHsnCode": inv.lines[0]["hsn"], "Irn": inv.irn,
        "IrnDt": f"{inv.date.isoformat()} 11:24:00",
    }
    if inv.meta.get("qr_tamper"):
        payload["TotInvVal"] = float(inv.total) + inv.meta["qr_tamper"]
    q = qrcode.QRCode(box_size=4, border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(make_einvoice_qr_text(payload))
    q.make(fit=True)
    img = q.make_image(fill_color="black", back_color="white").convert("RGB")
    # A signed e-invoice payload needs a dense QR; keep at least 3 px per module so it stays readable.
    side = max(size, (q.modules_count + 8) * 3)
    return img.resize((side, side), Image.NEAREST)


def render(inv: SynthInvoice) -> Image.Image:
    return [_tpl_classic, _tpl_modern, _tpl_thermal, _tpl_compact][inv.template % 4](inv)


def _party_block(c: Canvas, x: int, y: int, title: str, p: dict, size: int = 17) -> int:
    c.text((x, y), title, size - 2, fill=(90, 90, 90))
    c.text((x, y + 24), p["name"], size + 1, bold=True)
    c.text((x, y + 50), p["address"], size - 1)
    c.text((x, y + 74), f"GSTIN: {p['gstin']}", size, bold=True)
    c.text((x, y + 98), f"State: {STATES[p['state']].name} ({p['state']})", size - 1)
    return y + 124


def _lines_table(c: Canvas, inv: SynthInvoice, x0: int, y: int, x1: int, size: int = 16) -> int:
    cols = [("#", 40), ("Description", 330), ("HSN/SAC", 110), ("Qty", 80), ("Unit", 70), ("Rate", 120),
            ("Taxable", 150), ("GST %", 70)]
    scale = (x1 - x0) / sum(w for _, w in cols)
    xs = [x0]
    for _, w in cols:
        xs.append(xs[-1] + int(w * scale))
    c.rect((x0, y, x1, y + 32), fill=(235, 235, 235))
    for (name, _), cx in zip(cols, xs, strict=False):
        c.text((cx + 6, y + 8), name, size, bold=True)
    y += 32
    for i, ln in enumerate(inv.lines):
        vals = [str(i + 1), ln["description"], ln["hsn"], str(ln["quantity"]), ln["unit"], _money(ln["unit_price"]),
                _money(ln["taxable_value"]), f"{ln['tax_rate']}%"]
        for j, (v, cx) in enumerate(zip(vals, xs, strict=False)):
            right = j in (3, 5, 6)
            c.text((xs[j + 1] - 8 if right else cx + 6, y + 9), v, size, anchor="ra" if right else "la")
        c.line((x0, y + 34, x1, y + 34), fill=(200, 200, 200))
        y += 34
    c.rect((x0, y - 34 * len(inv.lines) - 32, x1, y))
    return y


def _totals(c: Canvas, inv: SynthInvoice, x_label: int, x_val: int, y: int, size: int = 18) -> int:
    rows = [("Taxable Value", inv.taxable_value)]
    if inv.igst > 0:
        rows.append((f"IGST @ {inv.lines[0]['tax_rate']}%" if len({ln['tax_rate'] for ln in inv.lines}) == 1
                     else "IGST", inv.igst))
    else:
        rows += [("CGST", inv.cgst), ("SGST", inv.sgst)]
    if inv.round_off:
        rows.append(("Round Off", inv.round_off))
    for label, v in rows:
        c.text((x_label, y), label, size)
        c.text((x_val, y), _money(v), size, anchor="ra")
        y += 30
    c.line((x_label, y, x_val, y), width=2)
    c.text((x_label, y + 8), "Invoice Total (Rs.)", size + 2, bold=True)
    c.text((x_val, y + 8), _money(inv.total), size + 2, bold=True, anchor="ra")
    return y + 48


def _tpl_classic(inv: SynthInvoice) -> Image.Image:
    c = Canvas(1240, 1754)
    c.text((620, 50), "TAX INVOICE", 34, bold=True, anchor="ma")
    c.text((620, 92), "(Original for Recipient)", 16, anchor="ma", fill=(100, 100, 100))
    _party_block(c, 70, 140, "Supplier", inv.supplier, 18)
    c.text((760, 164), f"Invoice No: {inv.number}", 19, bold=True)
    c.text((760, 194), f"Invoice Date: {inv.date:%d-%b-%Y}", 18)
    c.text((760, 222), f"Place of Supply: {inv.place_of_supply}-{STATES[inv.place_of_supply].name}", 17)
    c.text((760, 250), "Reverse Charge: No", 17)
    c.line((60, 290, 1180, 290))
    _party_block(c, 70, 305, "Bill To", inv.buyer, 18)
    y = _lines_table(c, inv, 60, 460, 1180)
    y = _totals(c, inv, 720, 1170, y + 30)
    qr = _qr_image(inv, 190)
    if qr:
        c.img.paste(qr, (80, y + 10))
        c.text((80, y + 16 + qr.height), f"IRN: {inv.irn[:32]}", 13)
        c.text((80, y + 34 + qr.height), f"     {inv.irn[32:]}", 13)
    c.text((80, 1600), "Declaration: We declare that this invoice shows the actual price of the goods described.", 15)
    c.text((1160, 1660), f"For {inv.supplier['name']}", 17, anchor="ra")
    c.text((1160, 1700), "Authorised Signatory", 15, anchor="ra")
    return c.img


def _tpl_modern(inv: SynthInvoice) -> Image.Image:
    c = Canvas(1240, 1754)
    c.rect((0, 0, 1240, 170), fill=(28, 74, 92), outline=(28, 74, 92))
    c.text((70, 40), inv.supplier["name"].upper(), 30, bold=True, fill=(255, 255, 255))
    c.text((70, 88), inv.supplier["address"], 17, fill=(225, 235, 240))
    c.text((70, 116), f"GSTIN {inv.supplier['gstin']}   |   {inv.supplier['phone']}", 17, fill=(225, 235, 240))
    c.text((1170, 40), "Tax Invoice", 28, bold=True, anchor="ra", fill=(255, 255, 255))
    c.text((70, 210), "BILLED TO", 15, fill=(28, 74, 92), bold=True)
    c.text((70, 236), inv.buyer["name"], 20, bold=True)
    c.text((70, 266), inv.buyer["address"], 16)
    c.text((70, 292), f"GSTIN: {inv.buyer['gstin']}", 18, bold=True)
    for i, (k, v) in enumerate([("Invoice #", inv.number), ("Date", inv.date.strftime("%d/%m/%Y")),
                                ("Place of supply", f"{STATES[inv.place_of_supply].name} ({inv.place_of_supply})")]):
        c.text((800, 210 + i * 34), k, 16, fill=(90, 90, 90))
        c.text((1170, 210 + i * 34), v, 18, bold=True, anchor="ra")
    y = _lines_table(c, inv, 60, 360, 1180, 16)
    y = _totals(c, inv, 720, 1170, y + 30)
    qr = _qr_image(inv, 170)
    if qr:
        c.img.paste(qr, (70, y + 10))
        c.text((90 + qr.width, y + 20), "IRN", 14, bold=True)
        c.text((90 + qr.width, y + 42), inv.irn[:32], 13)
        c.text((90 + qr.width, y + 60), inv.irn[32:], 13)
    c.text((620, 1690), "Thank you for your business", 16, anchor="ma", fill=(90, 90, 90))
    return c.img


def _tpl_thermal(inv: SynthInvoice) -> Image.Image:
    h = 900 + 70 * len(inv.lines) + (460 if inv.irn else 0)
    c = Canvas(620, h)
    y = 30
    c.text((310, y), inv.supplier["name"], 24, bold=True, anchor="ma")
    y += 34
    c.text((310, y), inv.supplier["address"], 14, anchor="ma")
    y += 24
    c.text((310, y), f"GSTIN:{inv.supplier['gstin']}", 17, anchor="ma", bold=True)
    y += 30
    c.text((310, y), "TAX INVOICE", 20, bold=True, anchor="ma")
    y += 34
    c.text((20, y), f"Bill No: {inv.number}", 17)
    c.text((600, y), inv.date.strftime("%d.%m.%Y"), 17, anchor="ra")
    y += 28
    c.text((20, y), f"Cust: {inv.buyer['name']}", 15)
    y += 24
    c.text((20, y), f"Cust GSTIN: {inv.buyer['gstin']}", 15, bold=True)
    y += 24
    c.text((20, y), f"POS: {inv.place_of_supply}", 15)
    y += 26
    c.line((20, y, 600, y))
    y += 8
    for ln in inv.lines:
        c.text((20, y), ln["description"][:34], 15)
        y += 22
        c.text((20, y), f"{ln['quantity']} x {_money(ln['unit_price'])}  HSN {ln['hsn']}  @{ln['tax_rate']}%", 14)
        c.text((600, y), _money(ln["taxable_value"]), 15, anchor="ra")
        y += 30
    c.line((20, y, 600, y))
    y += 12
    y = _totals(c, inv, 20, 600, y, 16)
    qr = _qr_image(inv, 220)
    if qr:
        c.img.paste(qr, ((620 - qr.width) // 2, y))
        y += qr.height + 10
    c.text((310, y + 10), "*** Thank you, visit again ***", 14, anchor="ma")
    return c.img


def _tpl_compact(inv: SynthInvoice) -> Image.Image:
    c = Canvas(1240, 1754)
    c.text((60, 50), inv.supplier["name"], 26, bold=True)
    c.text((60, 86), inv.supplier["address"], 16)
    c.text((60, 110), f"GSTIN/UIN: {inv.supplier['gstin']}   Email: {inv.supplier['email']}", 16)
    c.rect((60, 150, 1180, 300))
    c.line((620, 150, 620, 300))
    c.text((80, 162), "Buyer (Bill to)", 15, fill=(90, 90, 90))
    c.text((80, 186), inv.buyer["name"], 18, bold=True)
    c.text((80, 212), inv.buyer["address"], 15)
    c.text((80, 238), f"GSTIN/UIN : {inv.buyer['gstin']}", 16, bold=True)
    c.text((80, 264), f"State Name : {STATES[inv.buyer['state']].name}, Code : {inv.buyer['state']}", 15)
    c.text((640, 162), "Invoice No.", 15, fill=(90, 90, 90))
    c.text((640, 186), inv.number, 19, bold=True)
    c.text((920, 162), "Dated", 15, fill=(90, 90, 90))
    c.text((920, 186), inv.date.strftime("%d-%m-%Y"), 19, bold=True)
    c.text((640, 236), f"Place of Supply: {STATES[inv.place_of_supply].name}", 15)
    y = _lines_table(c, inv, 60, 330, 1180, 15)
    y = _totals(c, inv, 760, 1170, y + 24, 17)
    qr = _qr_image(inv, 180)
    if qr:
        c.img.paste(qr, (60, 1680 - qr.height))
        c.text((80 + qr.width, 1700 - qr.height), f"IRN : {inv.irn}", 12)
    c.text((60, 1700), "This is a Computer Generated Invoice", 14, fill=(90, 90, 90))
    return c.img


def degrade(img: Image.Image, rng: random.Random, strength: float = 1.0) -> Image.Image:
    """Make a clean render look like a phone photo: rotation, perspective, lighting, blur, noise, JPEG."""
    import numpy as np

    w, h = img.size
    angle = rng.uniform(-4, 4) * strength
    img = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=(rng.randint(90, 160),) * 3)
    w, h = img.size
    m = int(min(w, h) * 0.04 * strength)
    quad = (rng.randint(0, m), rng.randint(0, m), rng.randint(0, m), h - rng.randint(0, m),
            w - rng.randint(0, m), h - rng.randint(0, m), w - rng.randint(0, m), rng.randint(0, m))
    img = img.transform((w, h), Image.QUAD, quad, resample=Image.BICUBIC)
    arr = np.asarray(img).astype(np.float32)
    gx = np.linspace(rng.uniform(0.75, 1.0), rng.uniform(0.9, 1.05), w)[None, :, None]
    gy = np.linspace(rng.uniform(0.85, 1.0), rng.uniform(0.8, 1.05), h)[:, None, None]
    arr = arr * gx * gy * np.array([1.0, rng.uniform(0.96, 1.0), rng.uniform(0.88, 0.97)])
    arr += rng.uniform(4, 10) * strength * np.random.default_rng(rng.randint(0, 10**6)).standard_normal(arr.shape)
    img = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))
    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 1.1) * strength))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=rng.randint(55, 80))
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def to_pdf(inv: SynthInvoice) -> bytes:
    """Digital PDF with a real text layer (like invoices exported from billing software)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    y = H - 50
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawCentredString(W / 2, y, "TAX INVOICE")
    y -= 36
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(40, y, inv.supplier["name"])
    pdf.setFont("Helvetica", 9)
    pdf.drawString(40, y - 14, inv.supplier["address"])
    pdf.drawString(40, y - 28, f"GSTIN: {inv.supplier['gstin']}")
    pdf.drawString(360, y, f"Invoice No: {inv.number}")
    pdf.drawString(360, y - 14, f"Invoice Date: {inv.date:%d/%m/%Y}")
    pdf.drawString(360, y - 28, f"Place of Supply: {inv.place_of_supply}-{STATES[inv.place_of_supply].name}")
    y -= 64
    pdf.setFont("Helvetica", 9)
    pdf.drawString(40, y, "Bill To:")
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(40, y - 14, inv.buyer["name"])
    pdf.setFont("Helvetica", 9)
    pdf.drawString(40, y - 28, inv.buyer["address"])
    pdf.drawString(40, y - 42, f"GSTIN: {inv.buyer['gstin']}")
    y -= 70
    heads = [("Description", 40), ("HSN", 250), ("Qty", 300), ("Rate", 350), ("Taxable", 430), ("GST%", 510)]
    pdf.setFont("Helvetica-Bold", 9)
    for t, x in heads:
        pdf.drawString(x, y, t)
    y -= 14
    pdf.setFont("Helvetica", 9)
    for ln in inv.lines:
        pdf.drawString(40, y, ln["description"])
        pdf.drawString(250, y, ln["hsn"])
        pdf.drawString(300, y, str(ln["quantity"]))
        pdf.drawString(350, y, _money(ln["unit_price"]))
        pdf.drawString(430, y, _money(ln["taxable_value"]))
        pdf.drawString(510, y, f"{ln['tax_rate']}%")
        y -= 14
    y -= 10
    rows = [("Taxable Value", inv.taxable_value)]
    rows += [("IGST", inv.igst)] if inv.igst else [("CGST", inv.cgst), ("SGST", inv.sgst)]
    if inv.round_off:
        rows.append(("Round Off", inv.round_off))
    rows.append(("Invoice Total", inv.total))
    for label, v in rows:
        pdf.setFont("Helvetica-Bold" if label == "Invoice Total" else "Helvetica", 10)
        pdf.drawString(380, y, label)
        pdf.drawRightString(560, y, _money(v))
        y -= 15
    if inv.irn:
        pdf.setFont("Helvetica", 7)
        pdf.drawString(40, 60, f"IRN: {inv.irn}")
    pdf.showPage()
    pdf.save()
    return buf.getvalue()


def to_file(inv: SynthInvoice, rng: random.Random, kind: str | None = None) -> tuple[bytes, str, str]:
    """Returns (bytes, mime, kind) where kind is pdf | clean | photo."""
    kind = kind or rng.choices(["pdf", "clean", "photo"], weights=[3, 3, 4])[0]
    if kind == "pdf":
        return to_pdf(inv), "application/pdf", kind
    img = render(inv)
    if kind == "photo":
        img = degrade(img, rng, rng.uniform(0.6, 1.2))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue(), "image/jpeg", kind
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), "image/png", kind


def write_benchmark(out: str, n: int, seed: int = 7) -> int:
    rng = random.Random(seed)
    root = Path(out)
    root.mkdir(parents=True, exist_ok=True)
    buyer = make_party(rng, "36")
    manifest = []
    for i in range(n):
        inv = make_invoice(rng, buyer)
        data, mime, kind = to_file(inv, rng)
        ext = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"}[mime]
        name = f"inv-{i:04d}.{ext}"
        (root / name).write_bytes(data)
        manifest.append({"file": name, "kind": kind, "template": inv.template, "truth": inv.truth()})
    (root / "manifest.json").write_text(json.dumps({"buyer": buyer, "seed": seed, "items": manifest}, indent=1))
    return n

