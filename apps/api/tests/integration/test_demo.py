from __future__ import annotations

import time
from datetime import date

import httpx
import pytest
from sqlalchemy import func, select

from lekha.core.db import system_session, tenant_session
from lekha.jobs.queue import run_until_empty
from lekha.main import app
from lekha.models import Forecast, GraphSignal, Invoice, Match, RiskItem, Supplier
from lekha.services.demo import create_demo

pytestmark = [pytest.mark.db, pytest.mark.asyncio(loop_scope="session")]

import lekha.jobs.handlers  # noqa: E402,F401


async def test_demo_workspace_has_every_feature():
    t0 = time.perf_counter()
    async with system_session() as db:
        _user, firm = await create_demo(db, seed=7, today=date(2026, 11, 21))
    elapsed = time.perf_counter() - t0
    assert elapsed < 90, f"demo took {elapsed:.0f}s"
    await run_until_empty()
    async with tenant_session(firm.id) as db:
        n_inv = (await db.execute(select(func.count()).select_from(Invoice))).scalar_one()
        assert n_inv > 500
        statuses = dict((await db.execute(select(Match.status, func.count()).group_by(Match.status))).all())
        assert statuses.get("matched", 0) > 300 and statuses.get("missing_in_2b", 0) > 0 and statuses.get("mismatch", 0) > 0
        kinds = set((await db.execute(select(RiskItem.kind).where(RiskItem.status == "open"))).scalars())
        assert {"NOT_IN_2B", "MSME_43BH"} <= kinds, kinds
        assert (await db.execute(select(func.count()).select_from(Forecast))).scalar_one() > 0
        sig = set((await db.execute(select(GraphSignal.kind))).scalars())
        assert {"cycle", "shell", "template_reuse"} <= sig, sig
        scored = (await db.execute(select(func.count()).select_from(Supplier).where(Supplier.score.is_not(None)))).scalar_one()
        assert scored > 10
        review = (await db.execute(select(func.count()).select_from(Invoice).where(Invoice.status == "review"))).scalar_one()
        assert review >= 15


async def test_demo_endpoint_logs_visitor_in():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=120) as c:
        r = await c.post("/api/demo/start")
        assert r.status_code == 200, r.text
        assert r.json()["firm"]["is_demo"] is True
        # Sign-in is instant; data is built by the background worker.
        assert (await c.get("/api/demo/status")).json()["status"] == "building"
        await run_until_empty()
        assert (await c.get("/api/demo/status")).json()["status"] == "ready"
        assert len((await c.get("/api/clients")).json()) == 3


async def test_demo_leaves_no_duplicate_reconciliations_and_interactive_jobs_go_first():
    from sqlalchemy import delete

    from lekha.jobs.queue import claim_one, enqueue
    from lekha.models import Job

    async with system_session() as db:
        _user, firm = await create_demo(db, seed=11, today=date(2026, 11, 21))
    async with system_session() as db:
        recon = (await db.execute(select(func.count()).select_from(Job).where(
            Job.firm_id == firm.id, Job.kind == "reconcile.run"))).scalar_one()
    assert recon == 0  # the demo reconciles inline; a queued re-run per month would only delay the visitor
    await run_until_empty()

    async with system_session() as db:
        await db.execute(delete(Job).where(Job.status == "queued"))
        await enqueue(db, "graph.analyse", {}, firm_id=firm.id)
        await enqueue(db, "notice.pack", {"notice_id": "00000000-0000-0000-0000-000000000000"}, firm_id=firm.id)
    first = await claim_one()
    assert first is not None and first.kind == "notice.pack"
    async with system_session() as db:
        await db.execute(delete(Job).where(Job.firm_id == firm.id, Job.kind.in_(["graph.analyse", "notice.pack"])))
