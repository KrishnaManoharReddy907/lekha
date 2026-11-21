"""Demo workspace: a private, realistic sandbox per visitor (M15).

12 months of purchase invoices for three clients from ~30 suppliers with distinct behaviour
(prompt, late, unreliable), GSTR-2B statements with realistic discrepancies and late reporting,
payments with some overdue, an MSME supplier left unpaid, a shell supplier, a fake-invoice
template shared by three GSTINs, and a circular-trading loop among the clients. A handful of
current-month bills are uploaded as real documents so the review workstation has work to show.
Everything is synthetic; no real business data.
"""

from __future__ import annotations

import io
import random
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.bench.catalog import make_party
from lekha.bench.generator import degrade, make_invoice, render, to_pdf
from lekha.bench.gstr2b_gen import generate
from lekha.core.db import set_tenant
from lekha.core.security import hash_password, new_token
from lekha.engines.dates import add_months, period_of
from lekha.engines.invoice import normalise_number
from lekha.models import (
    Client,
    ClientGstin,
    Firm,
    Invoice,
    InvoiceLine,
    Membership,
    Payment,
    Supplier,
    User,
)

D = Decimal
TEMPLATE_HASH = "c3c3e1e1f0f08181"


def _months_back(today: date, n: int) -> list[date]:
    first = today.replace(day=1)
    return [add_months(first, -k) for k in range(n, 0, -1)]


async def create_demo_shell(db: AsyncSession, *, email: str | None = None, name: str = "Demo User") -> tuple[User, Firm]:
    """Instant part: the visitor's user, firm and membership. Data is filled in by `populate_demo`."""
    await set_tenant(db, None, bypass=True)
    user = User(email=email or f"demo-{uuid.uuid4().hex[:10]}@demo.lekha.app", name=name,
                password_hash=hash_password(new_token(16)))
    firm = Firm(name="Rao & Associates (demo)", kind="ca_firm", is_demo=True, settings={"demo_status": "building"})
    db.add_all([user, firm])
    await db.flush()
    db.add(Membership(firm_id=firm.id, user_id=user.id, role="owner"))
    await db.flush()
    return user, firm


async def create_demo(db: AsyncSession, *, email: str | None = None, name: str = "Demo User",
                      seed: int | None = None, today: date | None = None, with_documents: bool = True
                      ) -> tuple[User, Firm]:
    """Shell + data in one go (CLI, tests). Must run inside a bypass/system session."""
    user, firm = await create_demo_shell(db, email=email, name=name)
    await populate_demo(db, user, firm, seed=seed, today=today, with_documents=with_documents)
    return user, firm


async def populate_demo(db: AsyncSession, user: User, firm: Firm, *, seed: int | None = None,
                        today: date | None = None, with_documents: bool = True) -> None:
    today = today or date.today()
    rng = random.Random(seed if seed is not None else random.randint(1, 10**9))
    await set_tenant(db, firm.id)
    fid = firm.id

    clients_spec = [("Deccan FurnitureWorks Pvt Ltd", "36"), ("Sri Lakshmi Textiles", "37"),
                    ("Kaveri Pharma Distributors", "29")]
    clients: list[tuple[Client, dict]] = []
    for nm, st in clients_spec:
        party = make_party(rng, st) | {"name": nm}
        c = Client(firm_id=fid, name=nm, contact_name=rng.choice(["Ravi Kumar", "Anitha Reddy", "Suresh Rao"]),
                   contact_email=f"accounts@{nm.split()[0].lower()}.example", language=rng.choice(["en", "te", "hi"]))
        db.add(c)
        await db.flush()
        db.add(ClientGstin(firm_id=fid, client_id=c.id, gstin=party["gstin"], state_code=st, legal_name=nm))
        clients.append((c, party))

    behaviours = ["prompt"] * 18 + ["late"] * 6 + ["unreliable"] * 4
    pool = []
    for b in behaviours:
        p = make_party(rng)
        pool.append({"party": p, "behaviour": b, "msme": rng.choice([None, None, None, "micro", "small"])})
    shell = {"party": make_party(rng, "07") | {"name": "Swift Trade Impex"}, "behaviour": "prompt", "msme": None}
    factory = [{"party": make_party(rng) | {"name": n}, "behaviour": "prompt", "msme": None}
               for n in ("Apex Steel Traders", "Zenith Metal Mart", "Nova Alloys & Co")]
    suppliers: dict[str, Supplier] = {}
    months = _months_back(today, 12)
    for sp in [*pool, shell, *factory]:
        g = sp["party"]["gstin"]
        first_seen = months[0] if sp not in (shell, *factory) else add_months(today.replace(day=1), -2)
        s = Supplier(firm_id=fid, gstin=g, legal_name=sp["party"]["name"], trade_name=sp["party"]["name"],
                     state_code=g[:2], email=sp["party"]["email"], msme_category=sp["msme"],
                     udyam_number=f"UDYAM-{g[:2]}-00-{rng.randint(1000000, 9999999)}" if sp["msme"] else None,
                     first_seen=first_seen, language=rng.choice(["en", "en", "hi", "te"]))
        db.add(s)
        suppliers[g] = s
    await db.flush()

    all_invoices: list[tuple[Invoice, dict, dict, Client]] = []  # (row, truth, supplier spec, client)
    seq: dict[str, int] = {}

    def add_invoice(client: Client, party: dict, sp: dict, on: date, status: str = "approved", **kw) -> Invoice:
        g = sp["party"]["gstin"]
        seq[g] = seq.get(g, rng.randint(10, 400)) + (rng.randint(1, 3) if sp is shell else rng.randint(4, 40))
        inv = make_invoice(rng, party, on=on, supplier=sp["party"], seq=seq[g], **kw)
        t = inv.truth()
        row = Invoice(firm_id=fid, client_id=client.id, client_gstin=party["gstin"], supplier_id=suppliers[g].id,
                      source="demo", doc_type="invoice", number=t["number"], number_norm=normalise_number(t["number"]),
                      invoice_date=on, period=period_of(on), supplier_gstin=g, supplier_name=sp["party"]["name"],
                      buyer_gstin=party["gstin"], buyer_name=client.name, place_of_supply=party["state"],
                      taxable_value=D(t["taxable_value"]), igst=D(t["igst"]), cgst=D(t["cgst"]), sgst=D(t["sgst"]),
                      round_off=D(t["round_off"]), total=D(t["total"]), irn=t["irn"], status=status,
                      confidence=round(rng.uniform(0.93, 0.995), 3), received_on=on,
                      approved_at=datetime.now(UTC) if status == "approved" else None)
        db.add(row)
        all_invoices.append((row, t, sp, client))
        return row

    local_pool: dict[str, list[dict[str, Any]]] = {}
    for _client, party in clients:
        local_pool[party["gstin"]] = []
        for _ in range(7):
            sp = {"party": make_party(rng, party["state"]), "behaviour": rng.choice(["prompt"] * 5 + ["late", "unreliable"]),
                  "msme": rng.choice([None, None, "micro", "small"])}
            g = sp["party"]["gstin"]
            suppliers[g] = Supplier(firm_id=fid, gstin=g, legal_name=sp["party"]["name"], trade_name=sp["party"]["name"],
                                    state_code=g[:2], email=sp["party"]["email"], msme_category=sp["msme"],
                                    first_seen=months[0], language=rng.choice(["en", "hi", "te"]))
            db.add(suppliers[g])
            local_pool[party["gstin"]].append(sp)
    await db.flush()
    for client, party in clients:
        # Most purchases are local (CGST+SGST); some come from other states (IGST).
        my_suppliers = local_pool[party["gstin"]] * 2 + rng.sample(pool, 9)
        for m in months:
            for _ in range(rng.randint(14, 22)):
                sp = rng.choice(my_suppliers)
                add_invoice(client, party, sp, m + timedelta(days=rng.randint(0, 27)))
    # Shell supplier: three big, round, recent invoices to different clients.
    for (client, party), back in zip(clients, (2, 1, 1), strict=True):
        on = add_months(today.replace(day=1), -back) + timedelta(days=rng.randint(2, 20))
        inv = add_invoice(client, party, shell, on, n_lines=1)
        inv.taxable_value = D(rng.choice([800000, 900000, 1200000]))
        inv.igst = (inv.taxable_value * D("0.18")).quantize(D("0.01"))
        inv.cgst = inv.sgst = D(0)
        inv.total = inv.taxable_value + inv.igst
        inv.round_off = D(0)
    # Circular trading among the three clients (they sell to each other in a loop).
    loop = [(0, 1), (1, 2), (2, 0)]
    for a, b in loop:
        sell_party = clients[a][1]
        buyer_client, buyer_party = clients[b]
        sp = {"party": sell_party | {"email": "accounts@x.example"}, "behaviour": "prompt", "msme": None}
        if sell_party["gstin"] not in suppliers:
            s = Supplier(firm_id=fid, gstin=sell_party["gstin"], legal_name=clients[a][0].name,
                         trade_name=clients[a][0].name, state_code=sell_party["state"], first_seen=months[6])
            db.add(s)
            await db.flush()
            suppliers[sell_party["gstin"]] = s
        for k in range(3):
            on = months[-3 + k] + timedelta(days=rng.randint(1, 25))
            inv = add_invoice(buyer_client, buyer_party, sp, on, n_lines=1)
            inv.taxable_value = D(rng.choice([1450000, 1500000, 1525000]))
            inv.igst = (inv.taxable_value * D("0.18")).quantize(D("0.01")) if sell_party["state"] != buyer_party["state"] else D(0)
            inv.cgst = inv.sgst = D(0) if inv.igst else (inv.taxable_value * D("0.09")).quantize(D("0.01"))
            inv.total = inv.taxable_value + inv.igst + inv.cgst + inv.sgst
            inv.round_off = D(0)
    # Template factory: three suppliers whose invoices share an identical layout.
    for sp in factory:
        client, party = rng.choice(clients)
        add_invoice(client, party, sp, add_months(today.replace(day=1), -1) + timedelta(days=rng.randint(1, 20)))
    await db.flush()

    # Lines (so blocked credits and HSN show), payments (some overdue, MSME unpaid).
    for row, t, sp, _client in all_invoices:
        for i, ln in enumerate(t["lines"]):
            db.add(InvoiceLine(firm_id=fid, invoice_id=row.id, position=i, description=ln["description"], hsn=ln["hsn"],
                               quantity=D(ln["quantity"]), unit=ln["unit"], unit_price=D(ln["unit_price"]),
                               taxable_value=D(ln["taxable_value"]), tax_rate=D(ln["tax_rate"]), igst=D(ln["igst"]),
                               cgst=D(ln["cgst"]), sgst=D(ln["sgst"])))
            if ln["hsn"].startswith("9963"):
                row.blocked_amount += D(ln["igst"]) + D(ln["cgst"]) + D(ln["sgst"])
        age = (today - row.invoice_date).days
        r = rng.random()
        if sp["msme"] and age < 70 and r < 0.6:
            continue  # unpaid MSME bill → 43B(h)
        if age > 120 and r < 0.06:
            continue  # long-unpaid → Rule 37 exposure
        if age > 25 or r < 0.3:
            pay_after = rng.randint(10, min(120, max(11, age)))
            db.add(Payment(firm_id=fid, invoice_id=row.id, amount=row.total, paid_on=row.invoice_date +
                           timedelta(days=pay_after), mode=rng.choice(["NEFT", "RTGS", "UPI", "Cheque"]),
                           reference=f"UTR{rng.randint(10**9, 10**10)}"))
    await db.flush()

    # GSTR-2B per client per month with late reporting by behaviour and realistic discrepancies.
    from lekha.services.reconcile import import_gstr2b, run_reconciliation

    import json

    for client, party in clients:
        mine = [(r, t, sp) for r, t, sp, c in all_invoices if c.id == client.id]
        lag_of: dict[uuid.UUID, int | None] = {}
        for r, _t, sp in mine:
            b = sp["behaviour"]
            if b == "prompt":
                lag_of[r.id] = 0 if rng.random() < 0.94 else 1
            elif b == "late":
                lag_of[r.id] = rng.choice([1, 1, 2, 3])
            else:
                lag_of[r.id] = None if rng.random() < 0.3 else rng.choice([0, 1, 2, 4])
        for m in months:
            p = period_of(m)
            include = [(r, t) for r, t, _sp in mine if lag_of[r.id] is not None and
                       period_of(add_months(r.invoice_date.replace(day=1), lag_of[r.id])) == p]
            invs = [t for _r, t in include]
            forced = {}
            for k in range(len(invs)):
                x = rng.random()
                if x < 0.03:
                    forced[k] = "value_diff"
                elif x < 0.07:
                    forced[k] = "number_format"
                elif x < 0.09:
                    forced[k] = "date_diff"
                elif x < 0.10:
                    forced[k] = "tax_head"
                elif x < 0.11:
                    forced[k] = "itc_unavailable"
                else:
                    forced[k] = "clean"
            doc, _labels, _c = generate(party["gstin"], p, invs, rng, extra_records=rng.randint(0, 2),
                                        scenario_weights=[("clean", 1)], forced=forced)
            await import_gstr2b(db, firm_id=fid, client_id=client.id, filename=f"GSTR2B_{p}.json",
                                data=json.dumps(doc).encode(), period=p, gstin=party["gstin"], user_id=user.id,
                                auto_close=False)
            await db.flush()
            await run_reconciliation(db, fid, client.id, p, user.id)
            await db.flush()

    # Current month: invoices awaiting review (no 2B yet), some as real documents.
    cur = today.replace(day=1)
    for client, party in clients:
        for _ in range(rng.randint(5, 8)):
            add_invoice(client, party, rng.choice(pool + local_pool[party["gstin"]]),
                        cur + timedelta(days=rng.randint(0, max(0, today.day - 1))), status="review")
    await db.flush()
    if with_documents:
        await _demo_documents(db, fid, clients[0][0], clients[0][1], pool, rng, today, user.id)

    from lekha.services.refresh import refresh_client

    for client, _p in clients:
        await refresh_client(db, fid, client.id)
    from lekha.services.intelligence import analyse_firm

    # Template-reuse needs layout hashes: give the factory suppliers' invoices identical ones.
    from lekha.models import Document

    for row, _t, sp, client in all_invoices:
        if sp in factory:
            d = Document(firm_id=fid, client_id=client.id, storage_key="demo/none", filename=f"{row.number}.pdf",
                         mime="application/pdf", size=1, sha256=uuid.uuid4().hex, layout_hash=TEMPLATE_HASH,
                         status="extracted", source="demo")
            db.add(d)
            await db.flush()
            row.document_id = d.id
    await db.flush()
    await analyse_firm(db, fid, today)
    # Show the monthly close on the most recent period for the first client.
    from lekha.services.close import start_close

    await start_close(db, fid, clients[0][0].id, period_of(months[-1]), clients[0][1]["gstin"], "gstr2b", None)
    await set_tenant(db, None, bypass=True)
    firm.settings = {**(firm.settings or {}), "demo_status": "ready"}
    await set_tenant(db, fid)


async def _demo_documents(db: AsyncSession, fid: uuid.UUID, client: Client, party: dict, pool: list[dict[str, Any]],
                          rng: random.Random, today: date, user_id: uuid.UUID) -> None:
    from lekha.services.pipeline import ingest_document

    for k in range(4):
        sp = rng.choice(pool)
        inv = make_invoice(rng, party | {"name": client.name}, on=today - timedelta(days=rng.randint(1, 12)),
                           supplier=sp["party"], einvoice=k % 2 == 0, template=k % 4)
        if k == 0:
            data, mime, name = to_pdf(inv), "application/pdf", f"{sp['party']['name']}.pdf"
        else:
            img = render(inv)
            if k >= 2:
                img = degrade(img, rng, 0.8)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=88)
            data, mime, name = buf.getvalue(), "image/jpeg", f"WhatsApp Image {today:%Y-%m-%d} at {10 + k}.{rng.randint(10, 59)}.jpg"
        await ingest_document(db, firm_id=fid, client_id=client.id, filename=name, data=data, declared_mime=mime,
                              source="demo", user_id=user_id)


async def create_demo_cli(email: str) -> None:
    from lekha.core.db import system_session
    from lekha.jobs.queue import run_until_empty

    async with system_session() as db:
        existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if existing:
            print(f"{email} already exists")
            return
        password = "Demo@Lekha2026"
        user, firm = await create_demo(db, email=email, name="Demo Owner")
        user.password_hash = hash_password(password)
    await run_until_empty()
    print(f"Demo workspace '{firm.name}' ready. Sign in as {email} / {password}")
