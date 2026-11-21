from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

Money = Numeric(14, 2)

# Tables listed here get row-level security keyed on firm_id (see migrations).
TENANT_TABLES: list[str] = []


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSONB, list: JSONB, Decimal: Money}


def _now() -> datetime:
    return datetime.now(UTC)


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Timestamps are set in Python (not only by the server) so they are never expired after a flush,
    # which would force a lazy reload — not allowed under asyncio.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, server_default=func.now()
    )


class TenantMixin(IdMixin):
    @declared_attr
    def firm_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(UUID(as_uuid=True), ForeignKey("firms.id", ondelete="CASCADE"), index=True)

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        name = getattr(cls, "__tablename__", None)
        if name and name not in TENANT_TABLES:
            TENANT_TABLES.append(name)
