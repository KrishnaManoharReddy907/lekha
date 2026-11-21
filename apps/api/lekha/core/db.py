"""Database engine, sessions and the tenant (row-level security) context.

Every request/job runs inside a transaction where `app.firm_id` is set with SET LOCAL.
Postgres RLS policies on tenant tables compare rows' firm_id to that setting, so even a
buggy query cannot read another firm's data. System tasks (migrations, workers picking
jobs, public-token lookups) set `app.bypass_rls = 'on'` explicitly and narrowly.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from lekha.core.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        s = get_settings()
        _engine = create_async_engine(s.database_url, pool_size=10, max_overflow=10, pool_pre_ping=True)
        role = s.db_app_role

        if role:

            @event.listens_for(_engine.sync_engine, "connect")
            def _set_role(dbapi_conn, _record):  # pragma: no cover - exercised in integration tests
                cur = dbapi_conn.cursor()
                cur.execute(f'SET ROLE "{role}"')
                cur.close()

        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def set_tenant(session: AsyncSession, firm_id: uuid.UUID | None, bypass: bool = False) -> None:
    await session.execute(text("SELECT set_config('app.firm_id', :f, true)"), {"f": str(firm_id) if firm_id else ""})
    await session.execute(text("SELECT set_config('app.bypass_rls', :b, true)"), {"b": "on" if bypass else "off"})


@asynccontextmanager
async def tenant_session(firm_id: uuid.UUID | None, bypass: bool = False) -> AsyncIterator[AsyncSession]:
    """A transaction scoped to one firm. Commits on success, rolls back on error."""
    async with sessionmaker()() as session, session.begin():
        await set_tenant(session, firm_id, bypass)
        yield session


@asynccontextmanager
async def system_session() -> AsyncIterator[AsyncSession]:
    async with tenant_session(None, bypass=True) as s:
        yield s
