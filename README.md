# SupportPilot

![CI](https://github.com/BanothuNithin/supportpilot/actions/workflows/ci.yml/badge.svg)

**A multi-tenant, agentic AI customer support platform under development.** Week 6 adds S3-compatible document storage, durable background workers, PostgreSQL Row-Level Security, and tenant-isolated processing to the support workflow.

> **Status: Week 6 of 8.** The tenant-scoped support workflow, document indexing worker, approved-action worker, S3-compatible storage, and PostgreSQL RLS implementation are built. Verified backend results are 268 PostgreSQL tests passed and 257 SQLite tests passed with 11 skipped. I'm building this in public; see the roadmap below.

## Why this project
Most AI support bots only answer questions. Real support needs three more things:

1. **Data isolation:** many companies share one platform; their data must never mix.
2. **Reliable answers:** cite sources, and say "I don't know" (and route to a support ticket when needed) instead of guessing.
3. **Action:** support issues can become tickets and proposed external actions, which staff review and approve.

## What works today (Week 6)
- Tenant registration and role-based access (`owner`, `admin`, `agent`); verified JWTs establish tenant identity, tenant-scoped lookups return 404 for other tenants, access/refresh tokens use Redis-backed rotation, and login is rate-limited per IP.
- LangGraph support workflow with deterministic triage, follow-up-aware retrieval, answerability grading, grounded citations, SSE streaming, and ticket escalation. Staff replies and live conversation updates use tenant-scoped Redis pub/sub; clients refetch persisted messages after reconnecting.
- Qdrant retrieval filters by tenant and published active document generation; results are revalidated against PostgreSQL.

### Week 6 deliverables
1. **S3-compatible storage:** original `.txt`, `.md`, and `.pdf` files (up to 5 MB) are stored through the storage protocol; Compose uses RustFS.
2. **Durable document worker:** PostgreSQL stores job state; Redis provides best-effort wake-ups. The worker claims jobs atomically and uses leases, retries, reconciliation, and generation-specific indexing/cleanup.
3. **Worker-executed approved actions:** staff approval queues GitHub/Slack MCP actions for the worker. Uncertain external outcomes are not automatically retried; staff can check the target system before approving another attempt.
4. **Document UI:** queued/indexing/ready/failed states, polling while work is pending, reindexing, downloads, and deletion.
5. **Rate limits, cache, readiness:** user/tenant chat rate limits with frontend retry feedback, tenant-keyed query-embedding cache, and `/health/ready` checks for PostgreSQL, Redis, Qdrant, storage, and runtime-role safety.
6. **PostgreSQL tenant isolation:** forced RLS on all 10 tenant tables; `app_user` and `worker_user` runtime roles; restricted `SECURITY DEFINER` functions for authentication lookups and worker tenant enumeration.

- Bug reports create tickets and approval-gated proposals only for configured integrations. Credentials are Fernet-encrypted at rest and passed to MCP subprocesses through their environment, not tool arguments.
- Docker Compose includes PostgreSQL, Redis, Qdrant, RustFS (`s3`), the API, the worker, and a dedicated `migrate` service. CI tests both SQLite and PostgreSQL and checks the frontend.
- Verified backend tests: **PostgreSQL: 268 passed; SQLite: 257 passed, 11 skipped.**

## Tech stack
| Layer | Technology | Status |
|---|---|---|
| API | Python 3.12, FastAPI (async), SQLAlchemy 2, Pydantic | Done |
| Data | PostgreSQL with forced tenant RLS, Redis, Alembic | Done |
| Infra | Docker Compose (including RustFS, worker and migrate services), GitHub Actions | Done |
| Frontend | Next.js, TypeScript | Done |
| RAG | Qdrant, embeddings, citations | Done |
| Agents | LangGraph rule-based triage, retrieval, grader, resolver and escalation; OpenAI-compatible model provider/router | Built |
| Tools | Custom MCP stdio server using official Python SDK `mcp~=1.30.0` (GitHub issues and Slack alerts), allow-listed tools, human approval | Built |
| Storage and workers | RustFS S3-compatible object storage, PostgreSQL-backed jobs, Redis wake-up queue, indexing/action worker and reconciler | Built |
| Observability | Langfuse, RAGAS evals in CI | Week 7 |
| Deployment | Kubernetes, Terraform | Week 8 |

## Quickstart
Requirements: Docker Desktop, Python 3.12, Git.

```bash
git clone https://github.com/BanothuNithin/supportpilot.git
cd supportpilot
cp .env.example .env            # Windows: copy .env.example .env
# edit .env: set unique POSTGRES_PASSWORD, APP_DB_PASSWORD,
# APP_WORKER_DB_PASSWORD, JWT_SECRET and S3 credentials; keep the role
# passwords in the matching DATABASE_URL/WORKER_DATABASE_URL values
```

Add these Ollama provider settings to `.env`:
```env
LLM_PROVIDER=openai_compat
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=llama3.2
EMBED_PROVIDER=openai_compat
EMBED_BASE_URL=http://localhost:11434/v1
EMBED_API_KEY=ollama
EMBED_MODEL=nomic-embed-text
```

```bash
ollama pull llama3.2
ollama pull nomic-embed-text

docker compose up -d postgres redis qdrant s3
docker compose run --rm migrate

cd backend
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Configure integrations

Integration credentials require `INTEGRATIONS_KEY`. After installing backend dependencies, generate a Fernet key from the `backend` directory:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output into the repository-root `.env`:

```env
INTEGRATIONS_KEY=<generated Fernet key>
```

Keep this key backed up and stable across restarts. If it is lost, stored integration credentials cannot be decrypted. The key is not a GitHub token or `JWT_SECRET`.

Start the backend after adding `INTEGRATIONS_KEY` to `.env`:

```bash
uvicorn app.main:app --reload
```

For local Python development, keep the Compose infrastructure running, run the API from `backend/`, and start the worker in a second terminal with `python -m app.worker`. The API uses `DATABASE_URL` (`app_user`); the worker requires its separate `WORKER_DATABASE_URL` (`worker_user`). Run migrations through the Compose `migrate` service, which uses the privileged `MIGRATION_DATABASE_URL` separately from the API and worker credentials. To run both application processes in Compose instead, set model-provider URLs reachable from the containers and run `docker compose up -d backend worker`; do not run a second local API/worker against the same environment.

Open http://localhost:8000/docs for the interactive API.

In the staff UI, open **Integrations** and configure GitHub and/or Slack. Only owners and admins can save or remove credentials. Use a private GitHub repository: issue bodies include customer-reported text, marked as untrusted.

For GitHub, create a fine-grained personal access token in **GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens**. Select the resource owner and the single private repository SupportPilot should use; grant **Issues: Read and write** only, choose an appropriate expiration, and paste the token with the repository in `owner/repository` form. The token is encrypted at rest and is not shown again.

For Slack, create or select a Slack app for the intended workspace, enable **Incoming Webhooks**, add a webhook to the intended channel, and paste the generated `https://hooks.slack.com/services/...` URL. Each tenant currently has one Slack webhook/channel.

Run the default SQLite suite (no external services needed; tests use fake Redis, fake embeddings, in-memory Qdrant, and fake storage):
```bash
cd backend && pytest -q
```

The SQLite suite reports **257 passed, 11 skipped**. The PostgreSQL-only tests are skipped unless `TEST_DB=postgres`. CI runs both configurations on PostgreSQL 16 and SQLite; the verified PostgreSQL result is **268 passed**.

To run the PostgreSQL suite locally, use a dedicated isolated PostgreSQL test instance/database, not the development database. The test setup supports these exact variables: `TEST_DB=postgres`, `TEST_DATABASE_URL`, `TEST_WORKER_DATABASE_URL`, `TEST_MIGRATION_DATABASE_URL`, `APP_DB_PASSWORD`, and `APP_WORKER_DB_PASSWORD`. The test URLs must target the isolated test database; their embedded app/worker passwords must match the two `APP_*_PASSWORD` values. Example PowerShell setup (replace placeholders locally; do not reuse development credentials):

```powershell
$env:TEST_DB = "postgres"
$env:TEST_DATABASE_URL = "postgresql+asyncpg://app_user:<app-password>@localhost:5432/supportpilot_test"
$env:TEST_WORKER_DATABASE_URL = "postgresql+asyncpg://worker_user:<worker-password>@localhost:5432/supportpilot_test"
$env:TEST_MIGRATION_DATABASE_URL = "postgresql+asyncpg://<migration-owner>:<migration-password>@localhost:5432/supportpilot_test"
$env:APP_DB_PASSWORD = "<app-password>"
$env:APP_WORKER_DB_PASSWORD = "<worker-password>"
Set-Location backend
pytest -q
```

In tests, `TEST_MIGRATION_DATABASE_URL` is used to apply migrations and create the test database if needed. CI runs `cd backend && pytest -q` with the PostgreSQL URLs and role passwords set in the job environment.

## API overview
Base path `/api/v1`

| Endpoint | Description | Access |
|---|---|---|
| `POST /auth/register` | Create tenant and owner | Public |
| `POST /auth/login` | Get access and refresh tokens (rate limited) | Public |
| `POST /auth/refresh` | Rotate refresh token, get a new pair | Public |
| `POST /auth/logout` | Revoke a refresh token | Public |
| `GET /auth/me` | Current user | Authenticated |
| `POST/GET /auth/users` | Add or list staff of your tenant | Owner, Admin |
| `POST/GET/PATCH/DELETE /customers` | Tenant-scoped customers (delete: owner, admin) | Authenticated |
| `POST/GET /conversations`, `GET /conversations/{id}` | Tenant-scoped conversations with messages and saved citations | Authenticated |
| `POST /conversations/{id}/messages` | Stream step, triage, decision, citations, tokens, escalated, done and error events | Authenticated |
| `GET /conversations/{id}/events` | Subscribe to tenant-scoped live message events and heartbeats over SSE | Authenticated |
| `POST /conversations/{id}/human-reply` | Add a staff reply to an escalated conversation | Authenticated staff |
| `GET /integrations` | List configured integration kinds and masked summaries | Authenticated staff |
| `PUT /integrations/github`, `PUT /integrations/slack` | Configure per-tenant credentials | Owner, Admin |
| `DELETE /integrations/{kind}` | Remove a per-tenant integration | Owner, Admin |
| `GET /actions?status=&ticket_id=&conversation_id=` | List tenant-scoped proposed actions; filters can be combined | Authenticated staff |
| `POST /actions/{id}/approve` | Approve a proposal, optionally edit arguments, and enqueue it for worker execution through MCP | Owner, Admin |
| `POST /actions/{id}/reject` | Reject a pending proposal | Owner, Admin |
| `POST /documents` | Upload a `.txt`, `.md` or `.pdf` for indexing (returns 202; agents get 403) | Owner, Admin |
| `GET /documents` | List this tenant's documents | Authenticated |
| `GET /documents/{id}/download` | Download a tenant-scoped original file | Authenticated |
| `POST /documents/{id}/reindex` | Queue a ready or failed document for reindexing | Owner, Admin |
| `DELETE /documents/{id}` | Delete a tenant document and its vectors (agents get 403) | Owner, Admin |

`GET /health` is a liveness endpoint; `GET /health/ready` reports readiness checks for PostgreSQL, Redis, Qdrant, storage, and PostgreSQL runtime-role safety.

<!-- Add your own screenshot: Swagger showing the 403 for an agent calling POST /auth/users -->
<!-- ![RBAC in action](docs/images/swagger-403.png) -->

## Known limitations
- Configure durable storage and database/worker services for deployments. RustFS is the Compose development S3-compatible service; other S3-compatible endpoints can be configured through the S3 settings.
- The PostgreSQL tenant GUC is application-settable. RLS is defence-in-depth against accidentally omitted tenant filters; it does not protect against arbitrary SQL execution under a compromised application role.
- Migration credentials are privileged and must remain separate from `app_user` and `worker_user`. The Compose `migrate` service uses them only for Alembic migrations and role provisioning.
- PostgreSQL RLS applies to the ten tenant tables; readiness verifies database connectivity and runtime-role safety but does not inspect every table policy.
- Scanned PDFs are not OCR-processed and fail with a clear error.
- Search is dense-only; keyword matching and reranking are not implemented.
- The 0.35 score threshold is an initial guess, not tuned. Further evaluation: TBD (Week 7 evals).
- Rule-based triage is intentionally conservative and may need tuning as real support language is observed.
- The answerability grader is still an LLM judgment; if grading fails, the current implementation falls back to the top retrieval score.
- The search-query cleaner and follow-up detector use rules, so unusual phrasing may not be normalized or expanded as intended.
- Retrieval failures are logged and chat continues without knowledge-base context.
- Indexing and approved-action execution depend on the separate worker being available. PostgreSQL retains the job state and reconciliation can restore lost queue notifications.
- External actions are not automatically retried after execution may have started and the outcome is unknown. A human must check GitHub or Slack before deciding whether to approve another attempt; this avoids automatic duplicate side effects but cannot guarantee the result of a remote operation.
- Each approved action starts a separate MCP subprocess; there is no process pool.
- Each tenant can configure one GitHub repository and one Slack webhook/channel.
- Redis pub/sub is fire-and-forget and has no event replay. The frontend refetches conversation messages after reconnecting.
- Customers are still simulated through the staff chat; customer-widget authentication is not implemented.
- Bug detection is regex-based and may miss or misclassify reports.
- The Fernet encryption key is supplied through an environment variable; key rotation and KMS integration are not implemented.

## Roadmap
| Week | Focus |
|---|---|
| 1 | Schema, auth, RBAC, tenants, migrations, CI (done) |
| 2 | Next.js chat UI, SSE streaming (done) |
| 3 | RAG ingestion, Qdrant, citations (done) |
| 4 | LangGraph support workflow, deterministic triage and fast paths, follow-up-aware retrieval, grading, escalation, and streaming observability (done) |
| 5 | Per-tenant GitHub and Slack integrations, MCP tools, approval-gated actions, staff replies and live conversation updates (done) |
| 6 | RustFS S3-compatible storage, durable indexing and action workers, frontend document workflow, rate limiting/cache/readiness, PostgreSQL RLS (done) |
| 7 | Langfuse, RAGAS evals in CI, load tests |
| 8 | Kubernetes, Terraform, live deployment |

## Docs
- [Week 6 architecture, tenant isolation, flows and data model](docs/ARCHITECTURE.md)
- [Design decisions and trade-offs](DECISIONS.md)

## Author
Banothu Nithin, Full Stack and Applied AI Engineer, Hyderabad..
