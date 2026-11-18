# Design decisions

Each entry: what I chose, what I rejected, and what it costs.

## 1. Shared schema with `tenant_id` on every table
- **Chose:** one database, one schema, a `tenant_id` column everywhere.
- **Rejected:** schema-per-tenant and database-per-tenant (stronger isolation, much higher operational cost).
- **Cost:** one missing filter leaks data. **Mitigations:** a shared `get_owned` helper, isolation tests, and Row-Level Security in Week 6.

## 2. Tenant identity comes from the JWT, never the request
- A client cannot claim to be another company. `tenant_id` and role are read from the verified token and database row.

## 3. 404 instead of 403 for other tenants' objects
- A 403 confirms the id exists. A 404 reveals nothing.

## 4. FastAPI with async SQLAlchemy
- **Why:** chat streaming and LLM calls are I/O-bound, and Python keeps the API in the same language as the AI stack.
- **Cost:** async does not speed up CPU-bound work.

## 5. Short-lived JWT plus rotating refresh tokens in Redis
- **Chose:** 15-minute access tokens, 7-day refresh tokens, one use each (rotation).
- **Rejected:** long-lived JWTs (cannot be revoked) and server-side sessions (need a lookup on every request).
- **Detail:** only the SHA-256 hash of a refresh token is stored, so a Redis leak gives an attacker nothing usable.
- **Cost:** a stolen access token works until it expires (at most 15 minutes).

## 6. bcrypt used directly (not passlib)
- passlib is unmaintained and breaks with newer bcrypt releases.

## 7. Fixed-window login rate limit with Redis `INCR`
- **Why:** simple and atomic.
- **Cost:** bursts across the window edge. A sliding window or token bucket is the upgrade path. Behind a proxy the client IP must come from a trusted header.

## 8. Alembic migrations instead of `create_all`
- `create_all` cannot alter existing tables. Migrations give a versioned, reversible history.
- Tests still use `create_all` on a throwaway database.

## 9. Tests run on SQLite with a fake Redis
- **Why:** fast, no services needed, same result locally and in CI.
- **Cost:** SQLite differs from PostgreSQL (enums, UUIDs, locking). A CI job that runs migrations on real PostgreSQL is planned.

## 10. Bug lesson: patch a name where it is looked up
- `from module import client` copies the name at import time, so monkeypatching the module did not affect code that had already imported it, and tests silently hit a real Redis.
- **Fix:** modules read `redis_module.redis_client` at call time, so a single patch covers every user.
