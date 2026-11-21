"""Test configuration.

Integration tests (marked `db`) run against a throwaway `lekha_test` database that is recreated
and migrated once per session. AI is forced offline so tests are free and deterministic.
"""

from __future__ import annotations

import os
import tempfile

_TEST_DB = os.environ.get("LEKHA_TEST_DATABASE_URL", "postgresql+asyncpg://lekha:lekha@localhost:5433/lekha_test")
os.environ.update({
    "LEKHA_ENV": "test",
    "DATABASE_URL": _TEST_DB,
    "LEKHA_AI_OFFLINE": "true",
    "LEKHA_WORKER_IN_API": "false",
    "LEKHA_STORAGE": "local",
    "LEKHA_STORAGE_DIR": tempfile.mkdtemp(prefix="lekha-test-storage-"),
    "LEKHA_SECRET_KEY": "test-secret-key-test-secret-key-test-secret",
    "LEKHA_PUBLIC_API_URL": "http://test",
})

import pytest  # noqa: E402

_db_ready: bool | None = None


def _prepare_database() -> bool:
    import psycopg

    from lekha.core.config import get_settings

    url = get_settings().sync_database_url.replace("postgresql+psycopg://", "postgresql://")
    admin = url.rsplit("/", 1)[0] + "/postgres"
    name = url.rsplit("/", 1)[1]
    try:
        with psycopg.connect(admin, autocommit=True, connect_timeout=3) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            conn.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError:
        return False
    from lekha.cli import _migrate, _seed_rules

    _migrate()
    import asyncio

    asyncio.run(_seed_rules())
    from lekha.core import db as dbmod

    dbmod._engine = None  # the seeding loop is closed; the test loop creates a fresh engine
    dbmod._sessionmaker = None
    return True


def pytest_collection_modifyitems(config, items):
    global _db_ready
    if not any("db" in item.keywords for item in items):
        return
    _db_ready = _prepare_database()
    if not _db_ready:
        skip = pytest.mark.skip(reason="Postgres not reachable (start infra/docker-compose.yml)")
        for item in items:
            if "db" in item.keywords:
                item.add_marker(skip)
