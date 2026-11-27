"""enforce tenant row-level security

Revision ID: c54e2f1d0a6b
Revises: 0e1fd6b17b2d
Create Date: 2026-11-27
"""
import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c54e2f1d0a6b"
down_revision: Union[str, Sequence[str], None] = "0e1fd6b17b2d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = {
    "users": "tenant_id",
    "customers": "tenant_id",
    "conversations": "tenant_id",
    "messages": "tenant_id",
    "tickets": "tenant_id",
    "documents": "tenant_id",
    "integrations": "tenant_id",
    "pending_actions": "tenant_id",
    "document_index_generations": "tenant_id",
    "tenants": "id",
}


def _quoted_current_role(bind) -> str:
    role = bind.scalar(sa.text("SELECT current_user"))
    return bind.dialect.identifier_preparer.quote(role)


def _provision_app_user(bind) -> None:
    exists = bind.scalar(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = 'app_user'")
    )
    if exists:
        op.execute(
            "ALTER ROLE app_user WITH LOGIN NOSUPERUSER NOBYPASSRLS "
            "NOCREATEDB NOCREATEROLE"
        )
        return

    password = os.environ.get("APP_DB_PASSWORD")
    if not password:
        raise RuntimeError(
            "APP_DB_PASSWORD must be set to create the app_user database role"
        )
    quoted_password = bind.scalar(
        sa.text("SELECT quote_literal(:password)"),
        {"password": password},
    )
    op.execute(
        "CREATE ROLE app_user LOGIN NOSUPERUSER NOBYPASSRLS "
        f"NOCREATEDB NOCREATEROLE PASSWORD {quoted_password}"
    )


def _apply_rls(table: str, column: str) -> None:
    table_name = f"public.{table}"
    tenant_condition = (
        f"{column} = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table_name} FOR ALL "
        f"USING ({tenant_condition}) WITH CHECK ({tenant_condition})"
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    _provision_app_user(bind)

    database_name = bind.scalar(sa.text("SELECT current_database()"))
    quoted_database = bind.dialect.identifier_preparer.quote(database_name)
    op.execute(f"GRANT CONNECT ON DATABASE {quoted_database} TO app_user")
    op.execute("GRANT USAGE ON SCHEMA public TO app_user")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
        "TO app_user"
    )
    op.execute(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user"
    )

    owner = _quoted_current_role(bind)
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO app_user"
    )

    if bind.scalar(sa.text("SELECT to_regclass('public.alembic_version')")):
        op.execute(
            "REVOKE ALL PRIVILEGES ON TABLE public.alembic_version FROM app_user"
        )

    for table, tenant_column in TENANT_TABLES.items():
        _apply_rls(table, tenant_column)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in TENANT_TABLES:
        op.execute(
            f"DROP POLICY IF EXISTS tenant_isolation ON public.{table}"
        )
        op.execute(f"ALTER TABLE public.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")

    owner = _quoted_current_role(bind)
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM app_user"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        "REVOKE USAGE, SELECT ON SEQUENCES FROM app_user"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM app_user"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM app_user"
    )
    op.execute("REVOKE USAGE ON SCHEMA public FROM app_user")
    database_name = bind.scalar(sa.text("SELECT current_database()"))
    quoted_database = bind.dialect.identifier_preparer.quote(database_name)
    op.execute(f"REVOKE CONNECT ON DATABASE {quoted_database} FROM app_user")
    op.execute("DROP ROLE IF EXISTS app_user")
