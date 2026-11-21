"""Recovery forecasts for invoices missing from GSTR-2B (N1), trained on the whole firm's history."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from lekha.engines import forecast as F
from lekha.engines.dates import fy_start_year, months_between, period_to_date
from lekha.models import Forecast, Gstr2bImport, Invoice, Match, Supplier


def _latest_2b(imports: list[Gstr2bImport]) -> dict[str, date]:
    out: dict[str, date] = {}
    for imp in imports:
        d = period_to_date(imp.period)
        if imp.client_gstin not in out or d > out[imp.client_gstin]:
            out[imp.client_gstin] = d
    return out


async def build_forecaster(db: AsyncSession) -> tuple[F.Forecaster, dict[uuid.UUID, int], dict[str, date]]:
    """Observations from every invoice in the firm that a GSTR-2B could have covered."""
    imports = list((await db.execute(select(Gstr2bImport).where(Gstr2bImport.superseded.is_(False)))).scalars())
    latest = _latest_2b(imports)
    first_match: dict[uuid.UUID, str] = {}
    for m in (await db.execute(select(Match.invoice_id, Match.period, Match.status).where(
            Match.invoice_id.is_not(None), Match.status.in_(("matched", "mismatch"))))).all():
        if m[0] not in first_match or period_to_date(m[1]) < period_to_date(first_match[m[0]]):
            first_match[m[0]] = m[1]
    obs: list[F.Observation] = []
    exposure: dict[uuid.UUID, int] = {}
    for inv_id, sup, period, gstin, status in (await db.execute(select(
            Invoice.id, Invoice.supplier_gstin, Invoice.period, Invoice.client_gstin, Invoice.status))).all():
        if not period or not sup or status not in ("approved", "review") or gstin not in latest:
            continue
        p = period_to_date(period)
        if p > latest[gstin]:
            continue
        if inv_id in first_match:
            obs.append(F.Observation(sup, max(0, months_between(p, period_to_date(first_match[inv_id]))), 0))
        else:
            e = months_between(p, latest[gstin])
            exposure[inv_id] = e
            obs.append(F.Observation(sup, None, e + 1))
    return F.Forecaster(obs), exposure, latest


async def forecast_client(db: AsyncSession, firm_id: uuid.UUID, client_id: uuid.UUID) -> dict[str, Any]:
    model, exposure, _latest = await build_forecaster(db)
    invoices = [i for i in (await db.execute(select(Invoice).where(Invoice.client_id == client_id))).scalars()
                if i.id in exposure and i.invoice_date]
    names = {s.gstin: (s.trade_name or s.legal_name or s.gstin) for s in (await db.execute(select(Supplier))).scalars()}
    await db.execute(delete(Forecast).where(Forecast.invoice_id.in_([i.id for i in (await db.execute(
        select(Invoice).where(Invoice.client_id == client_id))).scalars()])))
    by_rec: dict[str, int] = defaultdict(int)
    today = date.today()
    for i in invoices:
        p = period_to_date(i.period)
        # The last 2B usable for a claim under Sec 16(4) is the one for October of the next FY.
        deadline_period = date(fy_start_year(i.invoice_date) + 1, 10, 1)
        pred = model.predict(i.supplier_gstin, exposure[i.id], months_between(p, deadline_period))
        hist = pred["history"]
        note = (f"Based on {hist} earlier invoice(s) from this supplier across your clients." if hist
                else "New supplier — based on how all your suppliers behave.")
        db.add(Forecast(firm_id=firm_id, invoice_id=i.id, supplier_gstin=i.supplier_gstin, p_next=pred["p_next"],
                        p_by_deadline=pred["p_by_deadline"], p_never=pred["p_never"],
                        expected_lag=pred["expected_lag"], recommendation=pred["recommendation"],
                        explanation=F.explain(pred, names.get(i.supplier_gstin, i.supplier_gstin), note),
                        model_version=F.MODEL_VERSION, as_of=today))
        by_rec[pred["recommendation"]] += 1
    return {"forecasts": len(invoices), **by_rec}
