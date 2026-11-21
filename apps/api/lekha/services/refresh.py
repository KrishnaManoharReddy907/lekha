"""Recompute everything derived for a client after its data changes.

Runs as a debounced background job (`client.refresh`): anomaly signals, the ITC risk ledger,
recovery forecasts and supplier scores. Each step is independent and idempotent.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


async def refresh_client(db: AsyncSession, firm_id: uuid.UUID, client_id: uuid.UUID) -> dict[str, Any]:
    out: dict[str, Any] = {}
    from lekha.services import anomalies

    out["anomalies"] = await anomalies.scan_client(db, firm_id, client_id)
    try:
        from lekha.services import risk
    except ModuleNotFoundError:
        return out
    out["risk"] = await risk.recompute_client(db, firm_id, client_id)
    from lekha.services import forecast, suppliers

    out["forecast"] = await forecast.forecast_client(db, firm_id, client_id)
    out["suppliers"] = await suppliers.rescore_suppliers(db, firm_id)
    return out
