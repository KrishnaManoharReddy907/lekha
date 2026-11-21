from __future__ import annotations

import csv
import io
import json
import random
from datetime import date

import httpx
import pytest

from lekha.bench.catalog import make_party
from lekha.bench.generator import make_invoice
from lekha.bench.gstr2b_gen import generate
from lekha.jobs.queue import run_until_empty
from lekha.main import app
from tests.integration.helpers import Api, unique_gstin

pytestmark = [pytest.mark.db, pytest.mark.asyncio(loop_scope="session")]

import lekha.jobs.handlers  # noqa: E402,F401


def http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def register_csv(invs: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Supplier GSTIN", "Supplier Name", "Invoice No", "Invoice Date", "Taxable Value", "IGST", "CGST",
                "SGST", "Cess", "Invoice Value", "Place of Supply"])
    for i in invs:
        w.writerow([i["supplier_gstin"], i["supplier_name"], i["number"], date.fromisoformat(i["date"]).strftime("%d/%m/%Y"),
                    i["taxable_value"], i["igst"], i["cgst"], i["sgst"], "0", i["total"], i["place_of_supply"]])
    return buf.getvalue().encode()


async def test_full_gst_cycle():
    rng = random.Random(21)
    gst = unique_gstin("36")
    buyer = make_party(rng, "36") | {"gstin": gst}
    suppliers = [make_party(rng) for _ in range(6)]
    invs, seen = [], set()
    for _ in range(40):
        t = make_invoice(rng, buyer, on=date(2025, 10, rng.randint(1, 28)), supplier=rng.choice(suppliers),
                         seq=rng.randint(1, 9999)).truth()
        if (t["supplier_gstin"], t["number"]) not in seen:
            seen.add((t["supplier_gstin"], t["number"]))
            invs.append(t)
    forced = {0: "missing", 1: "value_diff", 2: "tax_head", 3: "number_format", 4: "itc_unavailable", 5: "split"}
    doc, labels, _carry = generate(gst, "102025", invs, rng, extra_records=2, forced=forced)

    async with http() as c:
        api = Api(c)
        await api.signup()
        client = await api.client("Deccan FurnitureWorks", gstin=gst)
        cid = client["id"]
        r = await api.post(f"/api/clients/{cid}/register-import",
                           files=[("file", ("register.csv", register_csv(invs), "text/csv"))])
        assert r.status_code == 200, r.text
        assert r.json()["created"] == len(invs)
        # Re-importing the same register creates nothing new.
        r2 = await api.post(f"/api/clients/{cid}/register-import",
                            files=[("file", ("register.csv", register_csv(invs), "text/csv"))])
        assert r2.json()["created"] == 0 and r2.json()["skipped_duplicates"] == len(invs)

        r = await api.post(f"/api/clients/{cid}/gstr2b", files=[("file", ("2b.json", json.dumps(doc).encode(), "application/json"))])
        assert r.status_code == 200, r.text
        await run_until_empty()

        rec = (await api.get("/api/reconciliation", params={"client_id": cid, "period": "102025"})).json()
        assert rec["counts"].get("missing_in_books", 0) >= 2
        by_number = {it["invoice"]["number"]: it for it in rec["items"] if it["invoice"]}
        wrong = []
        for lab in labels:
            got = by_number[invs[lab["index"]]["number"]]["status"]
            if got != lab["expect"]:
                wrong.append((lab["scenario"], lab["expect"], got))
        assert not wrong, wrong
        assert all(it["explanation"]["summary"] for it in rec["items"])

        # IMS: every record has a recommendation; the tax-head record is recommended for rejection.
        ims = (await api.get("/api/ims", params={"client_id": cid, "period": "102025"})).json()
        assert all(it["decision"] for it in ims["items"]) and ims["deadline"] == "2025-11-13"
        th = by_number[invs[2]["number"]]
        assert th["ims"]["recommendation"] == "reject"
        ids = [it["id"] for it in ims["items"]]
        r = await api.post("/api/ims/decide", json={"record_ids": ids, "action": "use_recommendation"})
        assert r.json()["updated"] == len(ids)
        csv_out = await api.get("/api/ims/export", params={"client_id": cid, "period": "102025"})
        assert csv_out.status_code == 200 and "REJECT" in csv_out.text

        # Risk ledger: missing invoice and value mismatch are at risk with rupee amounts.
        risk = (await api.get("/api/risk", params={"client_id": cid})).json()
        kinds = {x["kind"] for x in risk["items"]}
        assert {"NOT_IN_2B", "WRONG_TAX_HEAD"} <= kinds and float(risk["totals"]["itc_at_risk"]) > 0

        # Forecast exists for the missing invoice.
        fc = (await api.get("/api/forecasts", params={"client_id": cid})).json()
        assert any(f["invoice"]["number"] == invs[0]["number"] for f in fc)

        # Table 4.
        t4 = (await api.get("/api/table4", params={"client_id": cid, "gstin": gst, "period": "102025"})).json()
        cells = t4["cells"]
        assert float(cells["4A5"]["total"]) > 0 and cells["4C"]["total"]
        assert abs(float(cells["4C"]["total"]) - (float(cells["4A5"]["total"]) + float(cells["4A3"]["total"])
                   - float(cells["4B1"]["total"]) - float(cells["4B2"]["total"]))) < 0.05
        assert float(cells["4D2"]["total"]) > 0  # the ITC-unavailable record
        fin = await api.post(f"/api/clients/{cid}/table4/finalise", json={"gstin": gst, "period": "102025"})
        assert fin.status_code == 200 and fin.json()["status"] == "final"

        # Supplier hub: follow-up for the supplier of the missing invoice, in Telugu, with a self-service link.
        sups = (await api.get("/api/suppliers", params={"client_id": cid})).json()
        target = next(s for s in sups if s["gstin"] == invs[0]["supplier_gstin"])
        r = await api.post(f"/api/suppliers/{target['id']}/followups", json={"client_id": cid, "language": "te"})
        assert r.status_code == 200, r.text
        f = r.json()
        assert "GSTR-1" in f["body"] and "/s/" in f["body"]
        token = f["body"].split("/s/")[1].split()[0]
        r = await api.patch(f"/api/followups/{f['id']}", json={"status": "sent"})
        assert r.json()["status"] == "sent"

    async with http() as anon:
        page = (await anon.get(f"/api/public/supplier/{token}")).json()
        assert page["supplier_gstin"] == invs[0]["supplier_gstin"] and page["issues"]
        r = await anon.post(f"/api/public/supplier/{token}", json={"item_ref": page["issues"][0]["risk_id"],
                                                                   "action": "will_file", "message": "Filing tomorrow",
                                                                   "expected_by": "2025-11-12"})
        assert r.status_code == 200

    async with http() as c:
        api2 = Api(c)
        r = await c.post("/api/auth/login", json={"email": api.email, "password": "Str0ngPassword"})
        api2.csrf = r.json()["csrf_token"]
        det = (await api2.get(f"/api/suppliers/{target['id']}")).json()
        assert det["responses"][0]["action"] == "will_file"
        assert det["followups"][0]["status"] == "responded"
        notes = (await api2.get("/api/notifications")).json()
        assert any("replied" in n["title"] for n in notes)
