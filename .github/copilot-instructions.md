# SupportPilot: working agreement for Copilot

Multi-tenant agentic AI customer-support platform. Backend: FastAPI (async), SQLAlchemy 2, PostgreSQL, Redis, Qdrant, LangGraph. Frontend: Next.js App Router, TypeScript, Tailwind. Dev on Windows (PowerShell, conda environment "supportpilot", Python 3.12). CI on Linux (GitHub Actions).

## Layout
- backend/app/main.py: FastAPI app, CORS for http://localhost:3000, `/api/v1` router registration, and logging setup
- backend/app/api/deps.py: `get_current_user` and `require_roles(*roles)`
- backend/app/api/tenant.py: `get_owned(db, model, obj_id, tenant_id, *options)` tenant-scoped fetch that raises 404
- backend/app/api/routes/: `auth`, `customers`, `conversations`, `chat` (SSE streaming), `documents`, `tickets`, `integrations`, and `actions`
- backend/app/core/: Pydantic settings, password/JWT security, call-time Redis access, refresh-token helpers, and login/IP rate limiting
- backend/app/db/: `Base`, async engine, `SessionLocal`, and `get_db`
- backend/app/models/core.py: all SQLAlchemy models using `Mapped`/`mapped_column`; `backend/app/schemas/` contains Pydantic request/response schemas
- backend/app/agents/: LangGraph graph, nodes, state, deterministic triage rules, follow-up-aware query handling, and prompts
- backend/app/llm/: `LLMProvider` protocol, fake/OpenAI-compatible providers, `ModelRouter`, and `get_llm()`
- backend/app/rag/: text/chunk extraction, deterministic or OpenAI-compatible embeddings, Qdrant access, ingestion, retrieval, and prompts
- backend/alembic/: migrations; `backend/alembic/env.py` loads model metadata and settings
- backend/tests/: pytest, conftest.py
- frontend/src/lib/api.ts: token storage, login/register/logout, `authFetch` (Bearer token plus one shared refresh in flight), data helpers, and `streamMessage`
- frontend/src/lib/sse.ts: `parseSse`
- frontend/src/app/: login, chat page, documents, and inbox

## Non-negotiable rules
1. Tenant isolation: tenant_id comes from the verified JWT (`current_user.tenant_id`), never from a request body, path, or header. Every business query must filter by tenant_id or use `get_owned`. Another tenant's object returns 404, never 403. Redis keys holding tenant data include the tenant_id where applicable.
2. Roles: owner > admin > agent. Enforce `owner`/`admin` with `require_roles` for staff invitations and external-system or credential-like operations; agents should not gain those privileges. Customer and conversation operations use the authenticated user plus tenant scoping unless a route is public.
3. Secrets are never logged, never returned by the API, never placed in prompts, SSE events, MCP tool arguments, or error messages. Encrypt secrets at rest.
4. LLM output is untrusted input. The AI never executes a side effect. It may only create a proposal that a human approves.
5. Customer text is untrusted: delimit it, length-limit it, and neutralize mentions before sending it to external systems.
6. New tables or columns: update the model, run `alembic revision --autogenerate`, read the generated migration, and give non-null columns a server default. The application schema is managed through Alembic migrations. Do not add runtime schema creation or treat `Base.metadata.create_all()` as the migration system. The test suite recreates metadata through its test fixture; this is test isolation, not application startup behavior.
7. Use async operations in request paths and the `get_db` dependency for request database access. Background tasks that need database access should manage their own `SessionLocal()` sessions. Long-lived SSE event streams must not retain the request database session.
8. Anything tests must swap is accessed at call time, e.g. `from app.core import redis as r; r.redis_client`. Do not capture Redis in a test-local import at module scope. RAG store tests patch `app.rag.store.upsert_chunks/search/delete_document`; Qdrant client access is available as `store.client` for focused swaps.
9. Async generators stopped early are closed with `contextlib.aclosing`. Streaming reply persistence that must survive client cancellation uses `anyio.CancelScope(shield=True)`; the disconnect cleanup path also uses a shielded close and `move_on_after(...)` for a bounded final save.
10. Never log customer message text or secrets.

## Testing rules
- Tests are hermetic. Importing `backend/tests/conftest.py` sets environment values before `app.db.session`, `app.main`, and settings are imported: `DATABASE_URL=sqlite+aiosqlite:///./test.db`, `RAG_MIN_SCORE=0.35`, `MIN_CONFIDENCE=0.5`, `RAG_TOP_K=4`, and empty optional LLM model values.
- The autouse `fresh_db` fixture drops and recreates all SQLAlchemy metadata around every test using the test engine; this is test isolation, not an app migration.
- The autouse `fake_redis` fixture patches `app.core.redis.redis_client` to `fakeredis.aioredis.FakeRedis(decode_responses=True)`.
- `conftest.py` sets `LLM_PROVIDER=fake`, `EMBED_PROVIDER=fake`, and `QDRANT_URL=:memory:` before application imports. It also configures the test database and integration encryption key. The autouse `fresh_db` fixture recreates SQLAlchemy metadata for each test, and `fake_redis` replaces the Redis client with fakeredis. Tests should not depend on real LLM providers, external Qdrant, Redis, GitHub, or Slack services.
- No real network and no real GitHub, Slack, Ollama, Redis, or external Qdrant in unit tests.
- Every behavior gets a test that FAILS if the behavior is removed. Security rules (tenant isolation, role checks, secret masking) always get explicit tests.
- Test first: show the failing test before the fix. Report real pytest output with exact counts.
- Use `cd backend && pytest -q` for the backend suite. For frontend changes, from `frontend/` run `npx tsc --noEmit` and `npm run lint`; use `npm run build` for production-build verification when relevant.

## Frontend rules
- Keep frontend API traffic in `frontend/src/lib/api.ts`. Authenticated resource calls use `authFetch`; login, register, and logout call `fetch` directly in that module, and SSE uses `authFetch` plus manual response-body/SSE parsing.
- `authFetch` prefixes paths with `${NEXT_PUBLIC_API_URL}/api/v1`, attaches `Authorization: Bearer <access_token>`, refreshes once on a 401, retries once, and shares one in-flight refresh promise. A failed refresh or missing tokens throws `AuthError`.
- Tokens are stored in `localStorage` under `supportpilot.tokens`; do not move token storage or duplicate refresh logic without a separate design decision.
- Do not call setState synchronously inside `useEffect`; start async work from an async IIFE or an async callback. Existing chat initialization follows this pattern.
- `frontend/src/lib/sse.ts` splits buffered text into complete SSE events and returns the unfinished tail for the next chunk.
- `streamMessage` reads the fetch response body, handles `token`, `citations`, and `error` events, and forwards other events through `onEvent`.
- Match the existing dark Tailwind style (slate-950 background, slate-900 surfaces, violet accents).

## Process
- Before coding, read the files named in the task and state which conventions you will follow.
- Minimal diffs. No unrelated refactors. Do not touch files outside the task's list.
- At the end: run the relevant whole suite, show `git diff --stat`, and list anything suspicious you noticed but did not change.

## Week 6 conventions (partial implementation)

Document storage and indexing jobs are implemented: uploads use S3-compatible storage, Redis is a best-effort wake-up queue, `app/worker.py` claims and indexes queued documents, and the reconciler repairs stuck document jobs. Approved actions still execute in `backend/app/api/routes/actions.py` inside the HTTP request; there is no worker or at-most-once side-effect queue. PostgreSQL RLS, `app_user`, `after_begin` tenant-context setup, `system_scope()`, and RLS migrations are not present.

Continue following these conventions when extending the system:
- Source of truth is PostgreSQL. Redis jobs are wake-up hints only. A job message is {"type","id","tenant_id"}. Handlers reload the row, never trust the payload, and claim work with an atomic UPDATE ... WHERE status = <expected> (compare-and-set). Every job type has a reconciler rule that repairs stuck rows from database state alone.
- Delivery semantics: idempotent jobs (document indexing) are at-least-once and may be retried automatically. Jobs with external side effects (GitHub, Slack) are at-most-once: if the outcome is unknown the action becomes failed with a message saying so, and a human decides about a retry. Never auto-retry a side effect after it may have been sent.
- Worker: `python -m app.worker`, plain asyncio, no signal handlers (Windows dev). Ctrl+C is a graceful stop. Handlers are plain async functions callable directly from tests. Logs contain job type, id, duration and outcome, never payload content, filenames or secrets.
- Storage: all file I/O goes through app/storage.py (a Storage Protocol; S3Storage uses boto3 inside anyio.to_thread). Object keys are tenants/<tenant_id>/documents/<document_id>/<uuid>.<ext>, never built from the user-supplied filename. Tests use FakeStorage or moto, never a real server. Access the storage instance at call time (module attribute), never import it by name.
- RLS: every table with a tenant_id has row-level security enabled and forced. The app connects as the non-superuser role app_user. The tenant context is set per transaction with set_config('app.tenant_id', ..., true) from a SQLAlchemy after_begin hook. system_scope() (sets app.system='on') may be used ONLY in: login, register, refresh, the worker's scan queries and the reconciler. Request handlers never use it. New tenant tables MUST get an RLS policy in the same migration. SQLite tests skip RLS; RLS tests run on PostgreSQL (marker postgres).
- Every new setting (S3, queue, rate limits, cache) is forced to a safe test value in tests/conftest.py before app imports.