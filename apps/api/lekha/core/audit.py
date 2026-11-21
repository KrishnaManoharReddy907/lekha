from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from lekha.models import AuditLog


def jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    return v


def snapshot(obj: Any, fields: list[str]) -> dict[str, Any]:
    return {f: jsonable(getattr(obj, f, None)) for f in fields}


async def audit(
    session: AsyncSession,
    firm_id: uuid.UUID,
    action: str,
    entity: str,
    entity_id: Any = None,
    *,
    actor_id: uuid.UUID | None = None,
    actor_kind: str = "user",
    before: dict | None = None,
    after: dict | None = None,
    **meta: Any,
) -> None:
    session.add(
        AuditLog(
            firm_id=firm_id,
            actor_id=actor_id,
            actor_kind=actor_kind,
            action=action,
            entity=entity,
            entity_id=str(entity_id) if entity_id is not None else None,
            before=jsonable(before) if before else None,
            after=jsonable(after) if after else None,
            meta=jsonable(meta),
        )
    )
