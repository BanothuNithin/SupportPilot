import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import SessionLocal, WorkerSessionLocal, engine
from app.models import (
    Conversation,
    Customer,
    Document,
    DocumentIndexGeneration,
    Integration,
    Message,
    PendingAction,
    Role,
    Tenant,
    Ticket,
    User,
)

pytestmark = pytest.mark.postgres

TENANT_TABLES = {
    "tenants": "id",
    "users": "tenant_id",
    "customers": "tenant_id",
    "conversations": "tenant_id",
    "messages": "tenant_id",
    "tickets": "tenant_id",
    "documents": "tenant_id",
    "integrations": "tenant_id",
    "pending_actions": "tenant_id",
    "document_index_generations": "tenant_id",
}


@pytest.fixture
async def rls_rows():
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    ids = {"tenant_a": tenant_a, "tenant_b": tenant_b}
    owner_engine = create_async_engine(
        settings.migration_database_url,
        poolclass=NullPool,
    )
    owner_sessions = async_sessionmaker(owner_engine, expire_on_commit=False)
    async with owner_sessions() as db:
        for key, tenant_id in (("a", tenant_a), ("b", tenant_b)):
            await db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            user_id = uuid.uuid4()
            customer_id = uuid.uuid4()
            conversation_id = uuid.uuid4()
            ticket_id = uuid.uuid4()
            document_id = uuid.uuid4()
            db.add(Tenant(id=tenant_id, name=f"Tenant {key}", slug=f"tenant-{key}"))
            await db.flush()
            db.add_all(
                [
                    User(
                        id=user_id,
                        tenant_id=tenant_id,
                        email=f"user-{key}@example.test",
                        hashed_password="test-hash",
                        role=Role.owner,
                    ),
                    Customer(
                        id=customer_id,
                        tenant_id=tenant_id,
                        email=f"customer-{key}@example.test",
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    Conversation(
                        id=conversation_id,
                        tenant_id=tenant_id,
                        customer_id=customer_id,
                    ),
                    Document(
                        id=document_id,
                        tenant_id=tenant_id,
                        filename=f"document-{key}.txt",
                        storage_key=f"test/{key}",
                        status="ready",
                    ),
                    Integration(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        kind="github",
                        config_encrypted="test",
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    Message(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        sender="customer",
                        content=f"message {key}",
                    ),
                    Ticket(
                        id=ticket_id,
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        title=f"ticket {key}",
                        priority="medium",
                        status="open",
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    PendingAction(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        ticket_id=ticket_id,
                        tool="create_github_issue",
                        arguments="{}",
                    ),
                    DocumentIndexGeneration(
                        tenant_id=tenant_id,
                        document_id=document_id,
                        generation_id=uuid.uuid4(),
                        status="active",
                    ),
                ]
            )
            await db.flush()
        await db.commit()
    await owner_engine.dispose()
    return ids


async def _apply_raw_context(connection, tenant_id=None, system=False):
    await connection.execute(
        text(
            "SELECT set_config('app.tenant_id', :tenant_id, true), "
            "set_config('app.system', :system, true)"
        ),
        {
            "tenant_id": str(tenant_id) if tenant_id is not None else "",
            "system": "on" if system else "",
        },
    )


async def test_unfiltered_selects_only_current_tenant_from_every_table(rls_rows):
    from app.db.tenant import set_tenant

    async with engine.connect() as connection:
        async with connection.begin():
            await _apply_raw_context(connection, rls_rows["tenant_a"])
            for table, tenant_column in TENANT_TABLES.items():
                rows = (
                    await connection.execute(text(f"SELECT * FROM {table}"))
                ).mappings().all()
                assert len(rows) == 1, table
                assert str(rows[0][tenant_column]) == str(rls_rows["tenant_a"]), table

    async with SessionLocal() as db:
        await set_tenant(db, rls_rows["tenant_a"])
        customers = list((await db.scalars(select(Customer))).all())
        assert len(customers) == 1
        assert customers[0].tenant_id == rls_rows["tenant_a"]


async def test_no_context_returns_no_rows_from_every_tenant_table(rls_rows):
    async with engine.connect() as connection:
        async with connection.begin():
            for table in TENANT_TABLES:
                rows = (
                    await connection.execute(text(f"SELECT * FROM {table}"))
                ).all()
                assert rows == [], table


async def test_inserting_another_tenant_row_is_rejected(rls_rows):
    async with engine.connect() as connection:
        async with connection.begin():
            await _apply_raw_context(connection, rls_rows["tenant_a"])
            with pytest.raises(DBAPIError, match="row-level security"):
                await connection.execute(
                    text(
                        "INSERT INTO customers (id, tenant_id, email) "
                        "VALUES (:id, :tenant_id, :email)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "tenant_id": str(rls_rows["tenant_b"]),
                        "email": "cross-tenant@example.test",
                    },
                )


async def test_updates_and_deletes_cannot_affect_another_tenant(rls_rows):
    async with engine.connect() as connection:
        async with connection.begin():
            await _apply_raw_context(connection, rls_rows["tenant_a"])
            updated = await connection.execute(
                text("UPDATE customers SET email = 'changed' WHERE tenant_id = :tenant"),
                {"tenant": str(rls_rows["tenant_b"])},
            )
            deleted = await connection.execute(
                text("DELETE FROM customers WHERE tenant_id = :tenant"),
                {"tenant": str(rls_rows["tenant_b"])},
            )
            assert updated.rowcount == 0
            assert deleted.rowcount == 0


async def test_app_system_setting_does_not_bypass_rls(rls_rows):
    async with engine.connect() as connection:
        async with connection.begin():
            await _apply_raw_context(connection, system=True)
            for table in TENANT_TABLES:
                rows = (
                    await connection.execute(text(f"SELECT * FROM {table}"))
                ).all()
                assert rows == [], table


async def test_tenant_context_is_reapplied_after_commit(rls_rows):
    from app.db.tenant import set_tenant

    async with SessionLocal() as db:
        await set_tenant(db, rls_rows["tenant_a"])
        before_commit = await db.scalar(text("SELECT count(*) FROM customers"))
        await db.commit()
        after_commit = await db.scalar(text("SELECT count(*) FROM customers"))

    assert before_commit == 1
    assert after_commit == 1


async def test_sequential_sessions_on_single_connection_do_not_leak_context(rls_rows):
    from app.db.tenant import set_tenant

    single_connection_engine = create_async_engine(
        settings.database_url,
        pool_size=1,
        max_overflow=0,
    )
    sessions = async_sessionmaker(single_connection_engine, expire_on_commit=False)
    try:
        async with sessions() as first:
            await set_tenant(first, rls_rows["tenant_a"])
            assert await first.scalar(text("SELECT count(*) FROM customers")) == 1
        async with sessions() as second:
            assert await second.scalar(text("SELECT count(*) FROM customers")) == 0
    finally:
        await single_connection_engine.dispose()


async def test_application_role_cannot_bypass_rls(rls_rows):
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT current_user, role.rolsuper, role.rolbypassrls, "
                    "EXISTS (SELECT 1 FROM pg_class AS owned "
                    "JOIN pg_namespace AS ns ON ns.oid = owned.relnamespace "
                    "WHERE ns.nspname = 'public' AND owned.relname IN "
                    "('tenants', 'users', 'customers', 'conversations', "
                    "'messages', 'tickets', 'documents', 'integrations', "
                    "'pending_actions', 'document_index_generations') "
                    "AND pg_get_userbyid(owned.relowner) = current_user) "
                    "AS owns_tenant_table "
                    "FROM pg_roles AS role "
                    "CROSS JOIN pg_class AS cls "
                    "WHERE role.rolname = current_user "
                    "AND cls.oid = 'public.customers'::regclass"
                )
            )
        ).one()
    assert row.current_user == "app_user"
    assert row.rolsuper is False
    assert row.rolbypassrls is False
    assert row.owns_tenant_table is False


async def test_worker_tenant_listing_is_not_available_to_app_user():
    async with engine.connect() as connection:
        with pytest.raises(DBAPIError, match="permission denied"):
            await connection.execute(
                text("SELECT * FROM public.worker_tenant_ids()")
            )


async def test_worker_role_is_separate_and_rls_restricted():
    async with WorkerSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    "SELECT current_user, role.rolsuper, role.rolbypassrls, "
                    "EXISTS (SELECT 1 FROM pg_class AS cls "
                    "JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace "
                    "WHERE ns.nspname = 'public' "
                    "AND cls.relname IN "
                    "('tenants', 'users', 'customers', 'conversations', "
                    "'messages', 'tickets', 'documents', 'integrations', "
                    "'pending_actions', 'document_index_generations') "
                    "AND pg_get_userbyid(cls.relowner) = current_user) "
                    "AS owns_tenant_table "
                    "FROM pg_roles AS role WHERE role.rolname = current_user"
                )
            )
        ).one()
    assert row.current_user == "worker_user"
    assert row.rolsuper is False
    assert row.rolbypassrls is False
    assert row.owns_tenant_table is False


async def test_security_definer_functions_have_fixed_search_path():
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT proc.proname, proc.prosecdef, proc.proconfig, "
                    "pg_get_userbyid(proc.proowner) AS function_owner "
                    "FROM pg_proc AS proc "
                    "JOIN pg_namespace AS ns ON ns.oid = proc.pronamespace "
                    "WHERE ns.nspname = 'public' AND proc.proname IN "
                    "('auth_email_exists', 'auth_user_for_login', "
                    "'auth_user_for_refresh', 'create_tenant_owner', "
                    "'worker_tenant_ids')"
                )
            )
        ).all()
        owner = (
            await connection.execute(
                text(
                    "SELECT rolcanlogin, rolbypassrls "
                    "FROM pg_roles WHERE rolname = 'supportpilot_function_owner'"
                )
            )
        ).one()
    assert len(rows) == 5
    assert all(row.prosecdef for row in rows)
    assert all(row.proconfig == ["search_path=pg_catalog"] for row in rows)
    assert all(row.function_owner == "supportpilot_function_owner" for row in rows)
    assert owner.rolcanlogin is False
    assert owner.rolbypassrls is True
