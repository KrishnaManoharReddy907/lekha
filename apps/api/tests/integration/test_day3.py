"""Day 3 features end to end on a demo workspace: close workflow, notice pack, rules simulator,
copilot (offline mode), exports, dashboards."""

from __future__ import annotations

import io
import zipfile
from datetime import date

import httpx
import pytest
from lxml import etree

from lekha.jobs.queue import run_until_empty
from lekha.main import app

pytestmark = [pytest.mark.db, pytest.mark.asyncio(loop_scope="session")]

import lekha.jobs.handlers  # noqa: E402,F401


@pytest.fixture(scope="module")
async def demo():
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=300)
    r = await c.post("/api/demo/start")
    assert r.status_code == 200, r.text
    csrf = r.json()["csrf_token"]
    await run_until_empty()
    clients = (await c.get("/api/clients")).json()
    yield c, {"x-csrf-token": csrf}, clients
    await c.aclose()


async def test_close_workflow_with_approvals(demo):
    c, h, clients = demo
    cid = clients[0]["id"] if clients[0]["name"].startswith("Deccan") else next(x["id"] for x in clients if x["name"].startswith("Deccan"))
    runs = (await c.get("/api/close-runs", params={"client_id": cid})).json()
    assert runs, "demo starts a close run for the latest period"
    run = (await c.get(f"/api/close-runs/{runs[0]['id']}")).json()
    steps = {s["code"]: s for s in run["steps"]}
    assert run["status"] == "awaiting_approval"
    assert steps["reconcile"]["status"] == "done" and steps["summary"]["status"] == "done"
    assert steps["summary"]["output"]["headline"].startswith("Net ITC")
    for code in ("ims", "followups", "table4"):
        if steps[code]["status"] == "needs_approval":
            r = await c.post(f"/api/close-runs/{run['id']}/steps/{code}/approve", headers=h)
            assert r.status_code == 200, r.text
    final = (await c.get(f"/api/close-runs/{run['id']}")).json()
    assert final["status"] == "approved"
    t4 = (await c.get("/api/table4", params={"client_id": cid, "gstin": final["client_gstin"], "period": final["period"]})).json()
    assert t4["finalised"] is not None


async def test_notice_defense_pack(demo):
    c, h, clients = demo
    cid = clients[0]["id"]
    periods = (await c.get(f"/api/clients/{cid}/periods")).json()
    with2b = sorted([p["period"] for p in periods if p["has_2b"]], key=lambda p: (p[2:], p[:2]))
    r = await c.post("/api/notices", headers=h, json={"client_id": cid, "form": "DRC-01C", "reference": "ZD3611250012345",
                                                     "period_from": with2b[-3], "period_to": with2b[-1], "amount": "125000"})
    assert r.status_code == 200, r.text
    await run_until_empty()
    n = (await c.get(f"/api/notices/{r.json()['id']}")).json()
    pack = n["packs"][0]
    assert "Reply to DRC-01C" in pack["reply_text"] and pack["analysis"]["totals"]["claimed"]
    z = await c.get(pack["zip_url"].replace("http://test", ""))
    assert z.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert {"01-reply-draft.txt", "02-reply-draft.pdf", "03-annexure-A-evidence.csv"} <= set(names)


async def test_rules_simulator(demo):
    c, h, _clients = demo
    rules = (await c.get("/api/rules")).json()
    assert any(r["code"] == "GST_RATE_SLABS" for r in rules)
    # Simulate shortening the Rule 37 payment window to 120 days from 1 Apr 2026.
    d = await c.post("/api/rules/drafts", headers=h, json={"code": "ITC_PAYMENT_DAYS", "params": {"value": 120, "interest_pct": 18},
                                                           "effective_from": "2026-04-01", "source": "What-if"})
    assert d.status_code == 200, d.text
    rep = await c.post("/api/rules/impact", headers=h, json={"draft_ids": [d.json()["id"]]})
    await run_until_empty()
    out = (await c.get(f"/api/rules/impact/{rep.json()['id']}")).json()
    assert out["status"] == "done" and out["summary"]["invoices_checked"] > 0
    assert float(out["summary"]["risk_change"]) >= 0
    bad = await c.post("/api/rules/drafts", headers=h, json={"code": "GST_RATE_SLABS", "params": {"value": "x"},
                                                             "effective_from": "2026-04-01"})
    assert bad.status_code == 400
    pub = await c.post(f"/api/rules/drafts/{d.json()['id']}/publish", headers=h)
    assert pub.json()["status"] == "published"


async def test_copilot_offline(demo):
    c, h, clients = demo
    r = await c.post("/api/copilot", headers=h, json={"client_id": clients[0]["id"], "message": "What deadlines are coming up?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] and body["tool_calls"][0]["tool"] == "deadlines"
    hist = (await c.get(f"/api/copilot/{body['conversation_id']}")).json()
    assert [m["role"] for m in hist] == ["user", "assistant"]


async def test_exports_and_dashboards(demo):
    c, _h, clients = demo
    cid = clients[0]["id"]
    periods = [p["period"] for p in (await c.get(f"/api/clients/{cid}/periods")).json() if p["has_2b"]]
    p = sorted(periods, key=lambda x: (x[2:], x[:2]))[-1]
    x = await c.get("/api/exports/tally", params={"client_id": cid, "period": p})
    assert x.status_code == 200
    root = etree.fromstring(x.content)
    vouchers = root.findall(".//VOUCHER")
    assert vouchers
    for v in vouchers:
        total = sum(float(a.text) for a in v.findall("./ALLLEDGERENTRIES.LIST/AMOUNT"))
        assert abs(total) < 0.02, "every Tally voucher must balance (credits = debits)"
    wb = await c.get("/api/exports/workbook.xlsx", params={"client_id": cid, "period": p})
    assert wb.status_code == 200 and wb.content[:2] == b"PK"
    pdf = await c.get("/api/exports/summary.pdf", params={"client_id": cid, "period": p})
    assert pdf.content[:5] == b"%PDF-"
    firm = (await c.get("/api/dashboard/firm")).json()
    assert len(firm["clients"]) == 3 and firm["calendar"]
    trend = (await c.get("/api/dashboard/client", params={"client_id": cid})).json()
    assert len(trend["months"]) == 12 and float(trend["working_capital_trapped"]) >= 0
    # Each bar has one meaning: matched + not-yet-matched = the month's total ITC.
    for m in trend["months"]:
        assert abs(float(m["matched"]) + float(m["missing"]) - float(m["itc"])) < 0.01, m
    _ = date
