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

## 11. Server-Sent Events for chat streaming
- **Chose:** stream assistant output over SSE. The browser sends an authenticated HTTP POST and reads its event stream.
- **Rejected:** WebSockets, which add a persistent bidirectional connection that chat replies do not need.
- **Cost:** SSE is one-way; future client-to-server live events would need another request or a different transport.

## 12. OpenAI-compatible model provider
- **Chose:** one provider implementation for servers that expose the OpenAI chat-completions API, including Ollama, OpenAI, vLLM, and LM Studio.
- **Why:** a common request/streaming format makes it easy to change model servers without coupling chat code to one vendor.
- **Cost:** compatibility is limited to the API features each server implements.

## 13. Fake provider for tests
- **Chose:** a deterministic fake LLM provider for tests and local development without model credentials or network access.
- **Cost:** canned responses verify integration and streaming behavior, not answer quality or model performance.

## 14. Store frontend tokens in `localStorage` for now
- **Chose:** keep access and refresh tokens in `localStorage` to make the current browser client simple.
- **Trade-off:** JavaScript can read these tokens, so an XSS vulnerability could expose them. Secure, HttpOnly cookies would reduce that exposure, but require a cookie and CSRF design.

## 15. Share one in-flight refresh across requests
- **Chose:** all concurrent API calls that encounter an expired access token await the same refresh promise.
- **Why:** refresh tokens rotate and are single-use; parallel refreshes with the same token could invalidate the session.
- **Cost:** callers wait for the refresh already in progress instead of starting their own refresh request.

## 16. Paragraph-aware chunks with overlap
- **Chose:** split extracted text into paragraph-aware chunks of 800 characters with 100 characters of overlap.
- **Rejected:** non-overlapping chunks that can separate a relevant passage at a boundary.
- **Cost:** overlapping text is embedded and stored more than once.

## 17. One Qdrant collection with tenant filtering
- **Chose:** store all tenants' chunks in one `chunks` collection and include a mandatory `tenant_id` filter on every search.
- **Rejected:** a separate collection per tenant.
- **Cost:** every search path must preserve the tenant filter; omitting it risks cross-tenant retrieval.

## 18. Initial retrieval score threshold
- **Chose:** use a minimum similarity score of 0.35 for retrieved chunks.
- **Rejected:** returning results below this initial relevance threshold.
- **Cost:** the threshold is not tuned; evaluation is TBD (Week 7 evals).

## 19. Citations and document-grounded refusal
- **Chose:** instruct the model to answer from retrieved context, cite sources as `[1]`, `[2]`, and say it could not find the answer in company documents when context is insufficient, while offering a human agent.
- **Rejected:** answers without source attribution or a clear response when the documents do not contain the answer.
- **Cost:** the model can still cite a document next to a refusal; structured output is planned for Week 4.

## 20. Treat uploaded documents as untrusted data
- **Chose:** label retrieved context as reference data and instruct the model never to follow instructions inside it.
- **Rejected:** treating text from an uploaded document as system instructions.
- **Cost:** this is a prompt-level safeguard; uploaded text remains part of the model's context.

## 21. Continue chat when retrieval fails
- **Chose:** log retrieval failures and continue the chat without knowledge-base context.
- **Rejected:** failing the whole chat request when embeddings or vector search are unavailable.
- **Cost:** without retrieval, the model has no company-document context for its answer.

## 22. FastAPI BackgroundTasks before a job queue
- **Chose:** run document indexing as a FastAPI BackgroundTask for now.
- **Rejected:** adding a separate job queue at this stage.
- **Cost:** a server restart during indexing can leave a document stuck in `indexing`; a job queue is planned for Week 6.

## 23. Hermetic tests with fake embeddings
- **Chose:** use a deterministic fake hashing embedder and force `LLM_PROVIDER=fake`, `EMBED_PROVIDER=fake`, and `QDRANT_URL=:memory:` in tests.
- **Rejected:** tests that depend on `.env`, Ollama, or external model services.
- **Cost:** the fake embedder makes tests repeatable but does not measure production embedding quality; evaluation is TBD (Week 7 evals).
