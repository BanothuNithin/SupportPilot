# Design decisions

Each entry: what I chose, what I rejected, and what it costs.

## 1. Shared schema with `tenant_id` on tenant-owned tables
- **Chose:** one database, one schema, a `tenant_id` column on tenant-owned rows; the `tenants` table is scoped by its `id`.
- **Rejected:** schema-per-tenant and database-per-tenant (stronger isolation, much higher operational cost).
- **Why:** shared tables keep migrations and operations manageable while explicit tenant scoping remains part of every business query.
- **Cost:** one missing filter can expose another tenant's rows. The shared `get_owned` helper, isolation tests, and completed PostgreSQL RLS provide defense in depth. RLS uses an application-settable custom GUC; it does not protect against arbitrary SQL execution under a compromised application role.

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

## 9. Hermetic SQLite tests plus a PostgreSQL CI job
- **Chose:** keep the default SQLite suite with fake Redis, embeddings, Qdrant, and storage, and run a separate PostgreSQL 16 CI job for PostgreSQL-specific behavior and migrations.
- **Rejected:** treating SQLite as proof of PostgreSQL RLS, role, locking, or migration behavior.
- **Why:** the fast suite remains convenient while PostgreSQL tests verify database-specific tenant isolation and role boundaries.
- **Cost:** the verified results are 257 passed and 11 skipped on SQLite, and 268 passed on PostgreSQL. SQLite does not emulate PostgreSQL RLS, role privileges, enums, UUID behavior, or locking.

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
- **Chose:** grade retrieved context before generation, answer only when it is judged answerable and confident enough, and include citations only on the grounded resolution path. If context is missing or insufficient, escalate with a clear handoff response.
- **Rejected:** generating a policy answer from model knowledge when the tenant's documents do not support it.
- **Cost:** the grader is still a model judgment and can be wrong. If grading itself fails, the current fallback uses the top retrieval score; prompt instructions also cannot guarantee perfect adherence to context.

## 20. Treat uploaded documents as untrusted data
- **Chose:** label retrieved context as reference data and instruct the model never to follow instructions inside it.
- **Rejected:** treating text from an uploaded document as system instructions.
- **Cost:** this is a prompt-level safeguard; uploaded text remains part of the model's context.

## 21. Continue chat when retrieval fails
- **Chose:** log retrieval failures and continue the chat without knowledge-base context.
- **Rejected:** failing the whole chat request when embeddings or vector search are unavailable.
- **Cost:** without retrieval, the model has no company-document context for its answer.

## 22. FastAPI BackgroundTasks (superseded by the durable worker)
- **Original choice:** document indexing first ran as a FastAPI `BackgroundTask`.
- **Superseded by:** a separate worker that claims PostgreSQL-backed jobs, with Redis used only for wake-up notifications.
- **Rejected:** relying on request-process memory to retain indexing work.
- **Why:** an API restart could interrupt work without durable recovery. PostgreSQL status, atomic claims, leases, retries, and reconciliation now let the worker recover indexing jobs independently of the API process.
- **Cost:** deployments must run and monitor the worker, PostgreSQL, and Redis; Redis messages themselves are not durable.

## 23. Hermetic tests with fake embeddings
- **Chose:** use a deterministic fake hashing embedder and force `LLM_PROVIDER=fake`, `EMBED_PROVIDER=fake`, and `QDRANT_URL=:memory:` in tests.
- **Rejected:** tests that depend on `.env`, Ollama, or external model services.
- **Cost:** the fake embedder makes tests repeatable but does not measure production embedding quality; evaluation is TBD (Week 7 evals).

## 24. Deterministic triage and fast paths
- **Chose:** classify intent and priority with local rules, and use the human-request regex as the sole signal for an explicit request to speak to a person. Greetings and supported assistant-meta questions take a chitchat fast path.
- **Rejected:** an LLM triage call before every message; small-model false positives previously caused greetings to be escalated.
- **Cost:** rules are less flexible than a model and may need tuning as customer phrasing changes. A fast path intentionally bypasses document retrieval and grading.

## 25. Sequential LangGraph routing
- **Chose:** route from triage first; direct human requests to escalation, chitchat to resolution, and other questions through retrieval, grading, and a final decision.
- **Rejected:** running retrieval alongside triage for every message.
- **Why:** unnecessary retrieval and model work is avoided for fast paths, and only the grader and answer generation use the LLM on a normal grounded request.
- **Cost:** retrieval and grading are sequential for normal questions. A no-hit result is treated as unanswerable rather than asking the model to answer from general knowledge.

## 26. Search-only query cleanup and follow-up expansion
- **Chose:** strip common instruction filler and expand a recognized follow-up with the most recent earlier user question that is not itself a follow-up. The original conversation and customer wording are still given to the answering model.
- **Rejected:** rewriting the customer's actual message or adding prior questions to every search.
- **Cost:** the cleaner and follow-up detector are rule-based; unusual phrasing may be missed or classified incorrectly. The decision SSE event exposes the generated search query for debugging, but server decision logs omit it.

## 27. Short, structured answerability grading
- **Chose:** request JSON from the grader with a small `max_tokens` limit (80), validate its result, and avoid calling it when retrieval returns no hits.
- **Rejected:** spending a full generation-sized response on the grading step or grading an empty context.
- **Cost:** the grader may still add latency on normal questions with hits, and provider JSON-mode support varies. The implementation falls back to retrieval confidence if grading raises an error.

## 28. Stream decisions and latency measurements
- **Chose:** send an SSE decision event with outcome, reason, grading fields, retrieval scores, and search query; report TTFT and per-node elapsed time in the final completion event.
- **Rejected:** relying on manual stopwatch measurements or inferring why an escalation happened from the final handoff alone.
- **Cost:** telemetry adds fields to the stream contract. Customer question text and search query are excluded from decision logs.

## 29. Filter handoff history and constrain answer claims
- **Chose:** omit exact stored assistant handoff messages from the recent history sent to answer generation, and instruct both grounded and no-context prompts that the assistant cannot claim to have contacted, notified, or handed work to a human. Grounded prompts also prohibit adding facts absent from context.
- **Rejected:** allowing a later answer to repeat a stored handoff as if a new escalation had occurred, or expanding a short source into unsupported detail.
- **Cost:** history filtering relies on exact matches to the configured handoff text. Prompt rules reduce unsupported claims but are not a formal guarantee; citations and escalation routing remain controlled by the application.

## 30. AI proposes actions; staff approve them
- **Chose:** create pending GitHub or Slack proposals for configured integrations when rules detect a bug report. Owners and admins can edit, approve, reject, or re-approve a failed action; approval queues the work for the separate worker.
- **Rejected:** letting the AI or an MCP tool execute a side effect without explicit staff approval.
- **Why:** human authorization remains the gate for external side effects while durable execution no longer depends on the API request lifetime.
- **Cost:** an issue or alert is not sent until a staff member approves it, so action completion depends on human review and a running worker.

## 31. Run one MCP stdio subprocess per approved action
- **Chose:** use the official Python MCP SDK (`mcp~=1.30.0`) and start the custom stdio server from the worker for each claimed, approved action. Decrypted credentials are supplied only in the subprocess environment, never in MCP tool arguments.
- **Rejected:** putting secrets in tool messages or maintaining a long-lived in-process tool server.
- **Why:** worker-side execution isolates external side effects from HTTP request handling and keeps credentials out of MCP arguments.
- **Cost:** process startup adds latency and there is no subprocess pool. If a worker stops after claiming an action, reconciliation marks an expired execution failed with an outcome-unknown message instead of automatically retrying it.

## 32. Allow-list and validate executable tools
- **Chose:** expose only `create_github_issue` and `post_slack_alert` through a registry, validate their arguments with Pydantic, and sanitize text before execution.
- **Rejected:** dispatching arbitrary tool names or accepting unvalidated model-generated arguments.
- **Cost:** adding an integration action requires an explicit registry entry, schema, sanitizer decision, server implementation, and tests.

## 33. Encrypt integration configuration with Fernet
- **Chose:** encrypt each tenant's GitHub repository/token and Slack webhook URL with Fernet using `INTEGRATIONS_KEY`; API responses return configured state and masked summaries, not the credentials.
- **Rejected:** storing credentials in plaintext or returning them to prefill the settings UI.
- **Cost:** the application must retain the environment-provided key to decrypt stored credentials. There is no key rotation or KMS-backed key management yet; losing the key makes the stored credentials unreadable.

## 34. Use atomic compare-and-set for approval and worker claim
- **Chose:** the API conditionally changes `pending` or `failed` to `approved`; after enqueueing a Redis wake-up, the worker conditionally changes `approved` to `executing` with a lease. Each transition proceeds only when exactly one row changed.
- **Rejected:** read-then-update checks that could race, and treating a Redis message as the action's durable state.
- **Why:** PostgreSQL serializes approval and execution claims; duplicate deliveries find no `approved` row to claim. The reconciler requeues stale approved actions, while an expired executing action is marked failed as outcome-unknown.
- **Cost:** an uncertain external result is not automatically retried, which avoids automatic duplicate side effects but can require human investigation. A human-approved retry can intentionally cause a new attempt.

## 35. Validate Slack webhook destinations from parsed URLs
- **Chose:** parse the submitted URL and require HTTPS, the exact `hooks.slack.com` hostname, and a path beginning with `/services/`.
- **Rejected:** raw-string prefix or substring checks that can accept attacker-controlled lookalike hosts.
- **Cost:** the integration is limited to Slack's incoming-webhook host and path; other Slack-compatible endpoints are not supported.

## 36. Sanitize and fence customer-reported text
- **Chose:** insert a zero-width space after GitHub `@` mentions, escape `&`, `<`, and `>` for Slack, strip control characters, and place customer text in a code fence longer than any backtick run in the text, labeled untrusted.
- **Rejected:** forwarding customer text unchanged or treating it as instructions for the AI or action executor.
- **Cost:** sanitization changes the displayed or transmitted text and is a defense-in-depth measure, not a guarantee against every downstream interpretation.

## 37. Use tenant-scoped Redis pub/sub for live conversation updates
- **Chose:** publish message events to channels containing both tenant and conversation IDs, stream them to authenticated subscribers over SSE, and send heartbeats. The frontend refetches messages after reconnecting.
- **Rejected:** a global conversation channel or adding a persistent bidirectional transport for staff replies.
- **Cost:** publication is best-effort and events are not retained or replayed; reconnect recovery depends on refetching persisted messages.

## 38. Keep handoff replies truthful and out of later model history
- **Chose:** tell bug reporters that a ticket was created and the team will follow up here, without claiming that GitHub, Slack, or a person was already contacted. Exclude exact stored handoff replies from later model history.
- **Rejected:** wording that implies an external notification was sent, or feeding a previous handoff back to the model as ordinary assistant content.
- **Cost:** handoff history filtering relies on exact configured reply text, and the reply cannot guarantee when the team will follow up.

## 39. Use RustFS for local S3-compatible document storage
- **Chose:** store original document bytes through the S3 storage protocol; Docker Compose runs RustFS as the local S3-compatible service.
- **Rejected:** keeping uploads only in the API process or tying storage code to one vendor-specific API.
- **Why:** object storage separates original files from API/worker lifecycles and supports S3-compatible deployments.
- **Cost/trade-off:** RustFS must be configured and available for local Compose use; production storage choice and operations remain deployment concerns.

## 40. Keep durable job state in PostgreSQL and use Redis for wake-ups
- **Chose:** PostgreSQL document/action rows are authoritative; Redis carries `type`, `id`, and `tenant_id` wake-up messages.
- **Rejected:** treating Redis queue contents as the only record that work exists.
- **Why:** reconciliation can recover queued or stale work from database state if a Redis hint is lost.
- **Cost/trade-off:** the worker and reconciler must scan database state and coordinate through conditional updates; Redis availability affects promptness, not durable job state.

## 41. Publish document indexes by generation
- **Chose:** give each indexing claim a UUID token and isolated Qdrant generation; publish it only when the worker still owns the claim and lease. Keep prior active generations searchable until publication, then clean stale generations asynchronously.
- **Rejected:** writing replacement points into the active generation or trusting Qdrant writes without a PostgreSQL publication pointer.
- **Why:** concurrent, late, or failed indexing attempts must not replace a valid current document version or make partial vectors searchable.
- **Cost/trade-off:** generation metadata, leases, and repeated cleanup are required; stale vectors can remain temporarily but are excluded from retrieval until cleanup succeeds.

## 42. Execute approved external actions in the worker at most once automatically
- **Chose:** approval queues a job; the worker atomically claims it before invoking the GitHub or Slack MCP tool. If execution may have reached the external system but the result is unknown, mark the action failed and require a human decision.
- **Rejected:** executing external effects in the HTTP request or automatically retrying an uncertain action.
- **Why:** a retry after an unknown outcome could create duplicate issues or alerts.
- **Cost/trade-off:** staff may need to inspect GitHub or Slack before re-approving a failed action. A deliberate human retry is a new attempt and may repeat an effect.

## 43. Separate API and worker database roles
- **Chose:** use `app_user` for the API and `worker_user` for the worker, each with a separate URL/password and without table ownership or `BYPASSRLS`.
- **Rejected:** running both processes as the migration/database-owner role or sharing one runtime credential.
- **Why:** least privilege limits each process to its runtime responsibilities while RLS remains active.
- **Cost/trade-off:** deployments must provision and manage multiple credentials; the worker requires `WORKER_DATABASE_URL` on PostgreSQL.

## 44. Force PostgreSQL RLS with transaction-local tenant context
- **Chose:** enable and force RLS on all ten tenant tables; set `app.tenant_id` from the verified JWT using transaction-local `set_config`.
- **Rejected:** relying only on application query filters or using a persistent connection-level tenant setting.
- **Why:** RLS catches accidentally omitted tenant filters, and transaction-local context avoids leaking tenant state across pooled transactions.
- **Cost/trade-off:** the tenant GUC is application-settable. This is defence-in-depth, not protection against arbitrary SQL execution under a compromised application role.

## 45. Restrict cross-tenant operations to SECURITY DEFINER functions
- **Chose:** use fixed-`search_path` functions owned by `supportpilot_function_owner` for cross-tenant authentication lookups and worker tenant enumeration. The owner is `NOLOGIN` and not a tenant-table owner; authentication functions are granted to `app_user` and enumeration only to `worker_user`.
- **Rejected:** an `app.system` RLS policy bypass or granting runtime roles broad unscoped table access for these operations.
- **Why:** authentication must find a user before a tenant is known, and a worker must enumerate tenants before per-tenant work; narrow function grants constrain these cases.
- **Cost/trade-off:** function definitions, ownership, and grants must be carefully maintained in migrations. The function owner has `BYPASSRLS`, so its exposed function surface must remain narrowly scoped.

## 46. Tenant-key the embedding cache and distinguish chat/auth rate limits
- **Chose:** include tenant ID and query/model digest in embedding-cache keys; apply user and tenant chat limits that fail open on Redis errors, while login rate limiting uses the default fail-closed behavior.
- **Rejected:** sharing cached vectors across tenants or allowing authentication attempts without rate limiting when Redis is unavailable.
- **Why:** tenant-keyed caching prevents cross-tenant cache reuse; chat availability and authentication-abuse resistance have different failure trade-offs.
- **Cost/trade-off:** chat requests can exceed configured limits during Redis outages, and cache misses increase embedding work. Login can be unavailable if its Redis-backed limiter fails.

## 47. Run migrations through a dedicated privileged Compose service
- **Chose:** use the `migrate` service with `MIGRATION_DATABASE_URL` and `APP_DB_PASSWORD`/`APP_WORKER_DB_PASSWORD` to apply schema changes and provision runtime roles.
- **Rejected:** passing migration-owner credentials to the API or worker, or running migrations during application startup.
- **Why:** schema/role changes need elevated database privileges that request and job handlers should not have.
- **Cost/trade-off:** migrations must be run as a separate deployment step, and privileged credentials require tighter handling than runtime URLs.
