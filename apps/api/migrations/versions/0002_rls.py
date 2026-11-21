"""Row-level security: app role + per-firm policies on every tenant table.

Revision ID: 0002
Revises: 0001
"""
from collections.abc import Sequence

from alembic import op

from lekha.core.config import get_settings
from lekha.models import TENANT_TABLES

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = (
    "current_setting('app.bypass_rls', true) = 'on' "
    "OR firm_id = NULLIF(current_setting('app.firm_id', true), '')::uuid"
)


def upgrade() -> None:
    role = get_settings().db_app_role
    op.execute(
        f"""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            CREATE ROLE "{role}" NOLOGIN NOBYPASSRLS;
          END IF;
        END $$;
        """
    )
    op.execute(f'GRANT "{role}" TO CURRENT_USER')
    op.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"')
    op.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{role}"')
    # Audit log is append-only for the application.
    op.execute(f'REVOKE UPDATE, DELETE ON audit_log FROM "{role}"')
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({POLICY}) WITH CHECK ({POLICY})")


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
