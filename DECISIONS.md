# Design decisions (interview gold: keep adding to this)

## 1. Tenant isolation via `tenant_id` on every table
- tenant_id is read from the verified JWT, never from request bodies.
- Shared DB, shared schema: cheapest and simplest. Alternatives: schema-per-tenant, DB-per-tenant (stronger isolation, higher ops cost).
- Upgrade path: Postgres Row-Level Security (Week 6).

## 2. FastAPI + async SQLAlchemy
- Chat streaming and LLM calls are I/O-bound, so async fits.
- Python keeps the backend in the same language as the AI stack.

## 3. JWT (stateless) access tokens
- Trade-off: cannot revoke before expiry. Mitigation later: short expiry + Redis denylist/refresh tokens.

## 4. bcrypt directly (not passlib)
- passlib is unmaintained and breaks with newer bcrypt versions.

## 5. create_all at startup (temporary)
- Replaced by Alembic migrations in Week 2.
