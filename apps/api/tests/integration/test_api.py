from __future__ import annotations

import io
import random
import uuid

import httpx
import pytest
from sqlalchemy import select, text

from lekha.bench.catalog import make_party
from lekha.bench.generator import make_invoice, render, to_pdf
from lekha.core.db import tenant_session
from lekha.jobs.queue import run_until_empty
from lekha.main import app
from lekha.models import AuditLog, Client, Invoice, SupplierMemory
from tests.integration.helpers import Api, unique_gstin

pytestmark = [pytest.mark.db, pytest.mark.asyncio(loop_scope="session")]

import lekha.jobs.handlers  # noqa: E402,F401  register job handlers


def http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_signup_login_me_logout():
    async with http() as c:
        api = Api(c)
        data = await api.signup()
        assert data["firm"]["role"] == "owner"
        assert (await api.get("/api/auth/me")).json()["user"]["email"] == api.email
        assert (await api.post("/api/auth/logout")).status_code == 200
        assert (await api.get("/api/auth/me")).status_code == 401
        r = await c.post("/api/auth/login", json={"email": api.email, "password": "wrong-password1A"})
        assert r.status_code == 401
        r = await c.post("/api/auth/login", json={"email": api.email, "password": "Str0ngPassword"})
        assert r.status_code == 200


async def test_weak_password_and_duplicate_email():
    async with http() as c:
        r = await c.post("/api/auth/signup", json={"name": "x", "email": "weak@example.com", "password": "short"})
        assert r.status_code == 400 and r.json()["error"]["code"] == "weak_password"
        api = Api(c)
        await api.signup(email="dup@example.com")
        r = await c.post("/api/auth/signup", json={"name": "y", "email": "dup@example.com",
                                                   "password": "Str0ngPassword"})
        assert r.status_code == 409


async def test_csrf_required_for_mutations():
    async with http() as c:
        api = Api(c)
        await api.signup()
        r = await c.post("/api/clients", json={"name": "No CSRF"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "csrf"


async def test_client_gstin_validation():
    async with http() as c:
        api = Api(c)
        await api.signup()
        bad = unique_gstin()[:-1] + ("A" if unique_gstin()[-1] != "A" else "B")
        r = await api.post("/api/clients", json={"name": "Bad", "gstins": [{"gstin": "36AAGCD7781Q1Z2"}]})
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_gstin"
        _ = bad
        cl = await api.client()
        assert cl["gstins"][0]["state_code"] == "36"


async def test_row_level_security_isolates_firms():
    async with http() as ca, http() as cb:
        a, b = Api(ca), Api(cb)
        await a.signup(firm="Firm A")
        await b.signup(firm="Firm B")
        client_a = await a.client("Secret Client of A")
        # API layer: B cannot read A's client by id, and does not see it in lists.
        assert (await b.get(f"/api/clients/{client_a['id']}")).status_code == 404
        assert all(c["id"] != client_a["id"] for c in (await b.get("/api/clients")).json())
        firm_b = (await b.get("/api/auth/me")).json()["firm"]["id"]
        # Database layer: even a raw query under B's tenant context sees zero of A's rows.
        async with tenant_session(uuid.UUID(firm_b)) as db:
            n = (await db.execute(text("select count(*) from clients where id = :i"), {"i": client_a["id"]})).scalar()
            assert n == 0
        # And B cannot write a row into A's firm.
        firm_a = (await a.get("/api/auth/me")).json()["firm"]["id"]
        with pytest.raises(Exception, match="row-level security"):
            async with tenant_session(uuid.UUID(firm_b)) as db:
                db.add(Client(firm_id=uuid.UUID(firm_a), name="injected"))
                await db.flush()


async def _upload(api: Api, client: dict, data: bytes, name: str, mime: str) -> dict:
    r = await api.post(f"/api/clients/{client['id']}/documents", files=[("files", (name, data, mime))])
    assert r.status_code == 200, r.text
    return r.json()


async def test_upload_extract_review_learn_approve():
    rng = random.Random(11)
    async with http() as c:
        api = Api(c)
        await api.signup()
        gst = unique_gstin("36")
        client = await api.client(gstin=gst)
        buyer = make_party(rng, "36") | {"gstin": gst, "name": client["name"]}
        inv = make_invoice(rng, buyer, einvoice=False, template=0)
        pdf = to_pdf(inv)
        res = await _upload(api, client, pdf, "bill.pdf", "application/pdf")
        assert res["documents"][0]["status"] == "queued"
        # Same file again is recognised as a duplicate immediately.
        dup = await _upload(api, client, pdf, "bill-again.pdf", "application/pdf")
        assert dup["documents"][0]["status"] == "duplicate"

        assert await run_until_empty() >= 1
        listing = (await api.get("/api/invoices", params={"client_id": client["id"]})).json()
        assert listing["total"] == 1
        item = listing["items"][0]
        assert item["supplier_gstin"] == inv.supplier["gstin"]
        assert item["number"] == inv.number
        assert item["total"] == str(inv.total)

        detail = (await api.get(f"/api/invoices/{item['id']}")).json()
        assert detail["document"]["page_urls"], "page images rendered"
        assert detail["evidence"]["supplier_gstin"]["bbox"], "evidence carries a bounding box"

        # A reviewer corrects the supplier name → Lekha remembers it for this supplier.
        r = await api.patch(f"/api/invoices/{item['id']}", json={"fields": {"supplier_name": "Correct Name Pvt Ltd"}})
        assert r.status_code == 200, r.text
        assert r.json()["evidence"]["supplier_name"]["source"] == "human"
        firm_id = uuid.UUID((await api.get("/api/auth/me")).json()["firm"]["id"])
        async with tenant_session(firm_id) as db:
            mem = (await db.execute(select(SupplierMemory))).scalars().all()
            assert any(m.right == "Correct Name Pvt Ltd" for m in mem)

        r = await api.post(f"/api/invoices/{item['id']}/status", json={"status": "approved"})
        assert r.status_code == 200 and r.json()["status"] == "approved"
        # Editing an approved invoice is refused.
        r = await api.patch(f"/api/invoices/{item['id']}", json={"fields": {"total": "1"}})
        assert r.status_code == 400
        async with tenant_session(firm_id) as db:
            actions = {a.action for a in (await db.execute(select(AuditLog))).scalars()}
        assert {"document.uploaded", "invoice.extracted", "invoice.updated", "invoice.approved"} <= actions


async def test_photo_with_einvoice_qr_is_verified():
    rng = random.Random(5)
    async with http() as c:
        api = Api(c)
        await api.signup()
        gst = unique_gstin("36")
        client = await api.client(gstin=gst)
        buyer = make_party(rng, "36") | {"gstin": gst}
        inv = make_invoice(rng, buyer, einvoice=True, template=1)
        buf = io.BytesIO()
        render(inv).save(buf, format="PNG")
        await _upload(api, client, buf.getvalue(), "photo.png", "image/png")
        await run_until_empty()
        item = (await api.get("/api/invoices", params={"client_id": client["id"]})).json()["items"][0]
        assert item["qr_status"] == "verified"
        assert item["irn"] == inv.irn


async def test_public_upload_link_without_login():
    rng = random.Random(2)
    async with http() as c:
        api = Api(c)
        await api.signup()
        client = await api.client()
        r = await api.post(f"/api/clients/{client['id']}/upload-links", json={"days": 30})
        token = r.json()["url"].rsplit("/", 1)[1]
    async with http() as anon:
        info = await anon.get(f"/api/public/upload/{token}")
        assert info.status_code == 200 and info.json()["client_name"] == client["name"]
        inv = make_invoice(rng, make_party(rng, "36"))
        r = await anon.post(f"/api/public/upload/{token}", files=[("files", ("x.pdf", to_pdf(inv), "application/pdf"))])
        assert r.status_code == 200 and r.json()["uploaded"] == 1
        assert (await anon.get("/api/public/upload/not-a-real-token")).status_code == 404


async def test_viewer_cannot_write():
    async with http() as c:
        api = Api(c)
        await api.signup()
        firm_id = uuid.UUID((await api.get("/api/auth/me")).json()["firm"]["id"])
        r = await api.post("/api/invites", json={"email": "viewer@example.com", "role": "viewer"})
        token = r.json()["url"].rsplit("/", 1)[1]
    async with http() as c2:
        v = Api(c2)
        r = await c2.post("/api/auth/signup", json={"name": "V", "email": "viewer@example.com",
                                                    "password": "Str0ngPassword", "invite_token": token})
        assert r.status_code == 200, r.text
        v.csrf = r.json()["csrf_token"]
        assert r.json()["firm"]["id"] == str(firm_id) and r.json()["firm"]["role"] == "viewer"
        assert (await v.post("/api/clients", json={"name": "Nope"})).status_code == 403
        assert (await v.get("/api/clients")).status_code == 200
    _ = Invoice
