"""remove system GUC RLS bypass

Revision ID: 71a08f2c9d4e
Revises: c54e2f1d0a6b
Create Date: 2026-11-27
"""
import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "71a08f2c9d4e"
down_revision: Union[str, Sequence[str], None] = "c54e2f1d0a6b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = (
    "users",
    "customers",
    "conversations",
    "messages",
    "tickets",
    "documents",
    "integrations",
    "pending_actions",
    "document_index_generations",
    "tenants",
)
FUNCTION_OWNER = "supportpilot_function_owner"


def _role_password(env_name: str) -> str:
    password = os.environ.get(env_name)
    if not password:
        raise RuntimeError(f"{env_name} must be set for the database role")
    return password


def _set_role_password(bind, role: str, password: str) -> None:
    quoted_password = bind.scalar(
        sa.text("SELECT quote_literal(:password)"),
        {"password": password},
    )
    exists = bind.scalar(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
        {"role": role},
    )
    if exists:
        op.execute(
            f"ALTER ROLE {role} WITH LOGIN NOSUPERUSER NOBYPASSRLS "
            f"NOCREATEDB NOCREATEROLE PASSWORD {quoted_password}"
        )
    else:
        op.execute(
            f"CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS "
            f"NOCREATEDB NOCREATEROLE PASSWORD {quoted_password}"
        )


def _quoted_current_role(bind) -> str:
    role = bind.scalar(sa.text("SELECT current_user"))
    return bind.dialect.identifier_preparer.quote(role)


def _create_function_owner(bind) -> None:
    exists = bind.scalar(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
        {"role": FUNCTION_OWNER},
    )
    if exists:
        op.execute(
            f"ALTER ROLE {FUNCTION_OWNER} WITH NOLOGIN NOSUPERUSER BYPASSRLS "
            "NOCREATEDB NOCREATEROLE"
        )
    else:
        op.execute(
            f"CREATE ROLE {FUNCTION_OWNER} NOLOGIN NOSUPERUSER BYPASSRLS "
            "NOCREATEDB NOCREATEROLE"
        )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {FUNCTION_OWNER}")
    op.execute(
        f"GRANT SELECT ON public.users, public.tenants TO {FUNCTION_OWNER}"
    )
    op.execute(
        f"GRANT INSERT ON public.users, public.tenants TO {FUNCTION_OWNER}"
    )


def _create_auth_functions() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.auth_email_exists(p_email text)
        RETURNS boolean
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
            SELECT EXISTS (
                SELECT 1 FROM public.users WHERE email = p_email
            )
        $function$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.auth_user_for_login(p_email text)
        RETURNS TABLE(id uuid, tenant_id uuid, hashed_password text, role text)
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
            SELECT u.id, u.tenant_id, u.hashed_password, u.role::text
            FROM public.users AS u
            WHERE u.email = p_email
            LIMIT 1
        $function$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.auth_user_for_refresh(p_user_id uuid)
        RETURNS TABLE(id uuid, tenant_id uuid, role text)
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
            SELECT u.id, u.tenant_id, u.role::text
            FROM public.users AS u
            WHERE u.id = p_user_id
            LIMIT 1
        $function$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.create_tenant_owner(
            p_name text,
            p_slug text,
            p_email text,
            p_hashed_password text
        )
        RETURNS TABLE(id uuid, tenant_id uuid, role text)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            new_tenant_id uuid;
            new_user_id uuid;
        BEGIN
            INSERT INTO public.tenants AS tenant_row (id, name, slug, created_at)
            VALUES (gen_random_uuid(), p_name, p_slug, now())
            RETURNING tenant_row.id INTO new_tenant_id;

            INSERT INTO public.users AS user_row (
                id, tenant_id, email, hashed_password, role, created_at
            )
            VALUES (
                gen_random_uuid(), new_tenant_id, p_email, p_hashed_password,
                'owner', now()
            )
            RETURNING user_row.id INTO new_user_id;

            RETURN QUERY
            SELECT new_user_id, new_tenant_id, 'owner'::text;
        END
        $function$
        """
    )
    for signature in (
        "public.auth_email_exists(text)",
        "public.auth_user_for_login(text)",
        "public.auth_user_for_refresh(uuid)",
        "public.create_tenant_owner(text, text, text, text)",
    ):
        op.execute(f"ALTER FUNCTION {signature} OWNER TO {FUNCTION_OWNER}")
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO app_user")


def _create_worker_function() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.worker_tenant_ids()
        RETURNS SETOF uuid
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
            SELECT id FROM public.tenants ORDER BY id
        $function$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.worker_tenant_ids() FROM PUBLIC"
    )
    op.execute(
        f"ALTER FUNCTION public.worker_tenant_ids() OWNER TO {FUNCTION_OWNER}"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.worker_tenant_ids() TO worker_user"
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    _set_role_password(bind, "app_user", _role_password("APP_DB_PASSWORD"))
    _set_role_password(
        bind, "worker_user", _role_password("APP_WORKER_DB_PASSWORD")
    )
    _create_function_owner(bind)

    database_name = bind.scalar(sa.text("SELECT current_database()"))
    quoted_database = bind.dialect.identifier_preparer.quote(database_name)
    op.execute(f"GRANT CONNECT ON DATABASE {quoted_database} TO worker_user")
    op.execute("GRANT USAGE ON SCHEMA public TO worker_user")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
        "TO worker_user"
    )
    op.execute(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
        "TO worker_user"
    )
    owner = _quoted_current_role(bind)
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO worker_user"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO worker_user"
    )
    if bind.scalar(sa.text("SELECT to_regclass('public.alembic_version')")):
        op.execute(
            "REVOKE ALL PRIVILEGES ON TABLE public.alembic_version "
            "FROM worker_user"
        )

    for table in TENANT_TABLES:
        condition = (
            "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
            if table != "tenants"
            else "id = nullif(current_setting('app.tenant_id', true), '')::uuid"
        )
        op.execute(f"DROP POLICY tenant_isolation ON public.{table}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON public.{table} FOR ALL "
            f"USING ({condition}) WITH CHECK ({condition})"
        )

    _create_auth_functions()
    _create_worker_function()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for signature in (
        "public.auth_email_exists(text)",
        "public.auth_user_for_login(text)",
        "public.auth_user_for_refresh(uuid)",
        "public.create_tenant_owner(text, text, text, text)",
        "public.worker_tenant_ids()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute(
        f"REVOKE SELECT, INSERT ON public.users, public.tenants "
        f"FROM {FUNCTION_OWNER}"
    )
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {FUNCTION_OWNER}")
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY tenant_isolation ON public.{table}")
        condition = (
            "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
            if table != "tenants"
            else "id = nullif(current_setting('app.tenant_id', true), '')::uuid"
        )
        op.execute(
            f"CREATE POLICY tenant_isolation ON public.{table} FOR ALL "
            f"USING ({condition}) WITH CHECK ({condition})"
        )
    owner = _quoted_current_role(bind)
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM worker_user"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        "REVOKE USAGE, SELECT ON SEQUENCES FROM worker_user"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM worker_user"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM worker_user"
    )
    op.execute("REVOKE USAGE ON SCHEMA public FROM worker_user")
    database_name = bind.scalar(sa.text("SELECT current_database()"))
    quoted_database = bind.dialect.identifier_preparer.quote(database_name)
    op.execute(
        f"REVOKE CONNECT ON DATABASE {quoted_database} FROM worker_user"
    )
    op.execute("DROP ROLE IF EXISTS worker_user")
    op.execute(f"DROP ROLE IF EXISTS {FUNCTION_OWNER}")
