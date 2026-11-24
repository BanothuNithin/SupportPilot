# SupportPilot

![CI](https://github.com/BanothuNithin/supportpilot/actions/workflows/ci.yml/badge.svg)

**A multi-tenant, agentic AI customer support platform under development.** Week 5 adds tenant-configured GitHub and Slack integrations, approval-gated MCP actions, staff replies, and live conversation updates to the deterministic support workflow.

> **Status: Week 5 of 8.** The multi-tenant foundation, chat UI and streaming, tenant-isolated knowledge-base retrieval, support workflow, approval-gated integrations, and staff conversation tools are built. The backend test suite reports 184 passing tests. I'm building this in public; see the roadmap below.

![Architecture](docs/architecture.png)

## Why this project
Most AI support bots only answer questions. Real support needs three more things:

1. **Data isolation:** many companies share one platform; their data must never mix.
2. **Reliable answers:** cite sources, and say "I don't know" (and route to a support ticket when needed) instead of guessing.
3. **Action:** support issues can become tickets and proposed external actions, which staff review and approve.

## What works today (Week 5)
- Tenant registration: creating a company also creates its owner
- JWT access tokens (15 min) plus refresh tokens with **rotation**, stored hashed in Redis; logout revokes them
- Role-based access control: `owner`, `admin`, `agent`
- Login rate limiting (5 attempts per minute per IP, Redis)
- Tenant-scoped customers and conversations API (other tenants' objects return 404)
- PostgreSQL schema managed with Alembic migrations
- 184 backend tests, including tenant-isolation, action approval, and live conversation tests
- Docker Compose for PostgreSQL, Redis, Qdrant and MinIO
- Next.js chat UI with SSE replies and a Stop button to cancel generation
- Tenant-scoped knowledge-base upload (up to 5 MB), listing and deletion for `.txt`, `.md` and `.pdf` files; status moves from `indexing` to `ready` or `failed`, with a chunk count and failure error
- FastAPI BackgroundTasks extract text (pypdf for PDFs), create paragraph-aware chunks of 800 characters with 100 characters of overlap, embed them and upsert them into Qdrant
- Ollama `nomic-embed-text` embeddings (768 dimensions) through an OpenAI-compatible client; tests use a deterministic fake hashing embedder
- One Qdrant `chunks` collection uses cosine distance, a `tenant_id` keyword index (`is_tenant=true`), and a mandatory tenant filter for every search
- LangGraph runs deterministic rule-based triage; explicit human requests escalate immediately, while greetings and basic assistant-meta questions skip retrieval and grading
- Normal support questions run through tenant-filtered retrieval, an answerability grader, a routing decision, and then grounded resolution or escalation; triage itself makes no LLM call
- Search queries remove common instruction filler and expand genuine follow-ups with the most recent prior user question, while answer generation receives the original conversation wording
- Chat retrieves up to 4 tenant chunks with an initial minimum score of 0.35; only grounded answers include source citations, and citations are saved with messages
- Grading uses a short JSON response when retrieval finds context; empty retrieval skips the grader and routes to escalation
- Answer prompts require context-only facts, cite sources, treat uploaded content as untrusted reference data, and prevent the assistant from claiming it contacted or handed work to a human
- Escalations create a ticket and a handoff response; the assistant does not independently contact or notify a human
- SSE includes step, triage, decision, citations, token, escalated, done, and error events; completion reports time to first token and per-node timings
- Conversation context is limited to the latest six messages after exact stored handoff responses are filtered out
- The decision event exposes the search query for debugging, but server decision logs omit the query and customer question
- Next.js knowledge-base page with upload, status polling and delete; AI answers show chips only for sources they reference
- Retrieval failures are logged and chat continues without knowledge-base context
- Frontend lint and production build checks in GitHub Actions
- Per-tenant GitHub repository/token and Slack incoming-webhook integrations; credentials are Fernet-encrypted at rest and API responses contain masked summaries only
- Owners and admins configure integrations; any staff member can view integration status
- A custom MCP stdio server exposes `create_github_issue` and `post_slack_alert`; credentials are passed in the subprocess environment, never as tool arguments
- An allow-listed, Pydantic-validated tool registry sanitizes GitHub mentions and escapes Slack markup; Slack webhook hosts are validated from parsed URLs
- Rule-detected bug reports create a ticket and propose actions only for configured integrations; the handoff reply does not claim that GitHub or Slack was contacted
- Owners and admins can edit, approve, reject, and retry proposed actions; an atomic compare-and-set prevents concurrent approvals from executing the same action twice
- Staff can reply in escalated conversations; Redis pub/sub delivers tenant-scoped SSE updates with heartbeats, and clients refetch after reconnecting
- Integrations settings and staff conversation pages support configuration, action approvals, ticket status updates, staff replies, and live human messages

## Tech stack
| Layer | Technology | Status |
|---|---|---|
| API | Python 3.12, FastAPI (async), SQLAlchemy 2, Pydantic | Done |
| Data | PostgreSQL, Redis, Alembic | Done |
| Infra | Docker Compose, GitHub Actions | Done |
| Frontend | Next.js, TypeScript | Done |
| RAG | Qdrant, embeddings, citations | Done |
| Agents | LangGraph rule-based triage, retrieval, grader, resolver and escalation; OpenAI-compatible model provider/router | Built |
| Tools | Custom MCP stdio server using official Python SDK `mcp~=1.30.0` (GitHub issues and Slack alerts), allow-listed tools, human approval | Built |
| Storage and workers | MinIO / S3, background jobs | Week 6 |
| Observability | Langfuse, RAGAS evals in CI | Week 7 |
| Deployment | Kubernetes, Terraform | Week 8 |

## Quickstart
Requirements: Docker Desktop, Python 3.12, Git.

```bash
git clone https://github.com/BanothuNithin/supportpilot.git
cd supportpilot
cp .env.example .env            # Windows: copy .env.example .env
# edit .env: set POSTGRES_PASSWORD (letters and numbers), the same password in DATABASE_URL, and JWT_SECRET
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

docker compose up -d postgres redis qdrant minio

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
alembic upgrade head
uvicorn app.main:app --reload
```

Open http://localhost:8000/docs for the interactive API.

In the staff UI, open **Integrations** and configure GitHub and/or Slack. Only owners and admins can save or remove credentials. Use a private GitHub repository: issue bodies include customer-reported text, marked as untrusted.

For GitHub, create a fine-grained personal access token in **GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens**. Select the resource owner and the single private repository SupportPilot should use; grant **Issues: Read and write** only, choose an appropriate expiration, and paste the token with the repository in `owner/repository` form. The token is encrypted at rest and is not shown again.

For Slack, create or select a Slack app for the intended workspace, enable **Incoming Webhooks**, add a webhook to the intended channel, and paste the generated `https://hooks.slack.com/services/...` URL. Each tenant currently has one Slack webhook/channel.

Run the tests (no Docker needed, they use SQLite and a fake Redis):
```bash
cd backend && pytest -q
```
Tests use `LLM_PROVIDER=fake`, `EMBED_PROVIDER=fake` and `QDRANT_URL=:memory:`; they do not depend on `.env` or Ollama.

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
| `POST /actions/{id}/approve` | Approve a proposal, optionally with edited arguments; execute it through MCP | Owner, Admin |
| `POST /actions/{id}/reject` | Reject a pending proposal | Owner, Admin |
| `POST /documents` | Upload a `.txt`, `.md` or `.pdf` for indexing (returns 202; agents get 403) | Owner, Admin |
| `GET /documents` | List this tenant's documents | Authenticated |
| `DELETE /documents/{id}` | Delete a tenant document and its vectors (agents get 403) | Owner, Admin |

<!-- Add your own screenshot: Swagger showing the 403 for an agent calling POST /auth/users -->
<!-- ![RBAC in action](docs/images/swagger-403.png) -->

## Known limitations
- Original files are not stored; only text chunks are retained. Object storage is planned for Week 6.
- Indexing runs as a FastAPI BackgroundTask. A server restart during indexing can leave a document in `indexing`; a job queue is planned for Week 6.
- Scanned PDFs are not OCR-processed and fail with a clear error.
- Search is dense-only; keyword matching and reranking are not implemented.
- The 0.35 score threshold is an initial guess, not tuned. Further evaluation: TBD (Week 7 evals).
- Rule-based triage is intentionally conservative and may need tuning as real support language is observed.
- The answerability grader is still an LLM judgment; if grading fails, the current implementation falls back to the top retrieval score.
- The search-query cleaner and follow-up detector use rules, so unusual phrasing may not be normalized or expanded as intended.
- Approved actions execute inside the HTTP request; there is no worker or retry queue yet (planned for Week 6). A process crash after an action is marked approved but before it is marked executed can leave it approved.
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
| 6 | Redis workers, S3, Postgres Row-Level Security |
| 7 | Langfuse, RAGAS evals in CI, load tests |
| 8 | Kubernetes, Terraform, live deployment |

## Docs
- [Architecture and data model](docs/ARCHITECTURE.md)
- [Design decisions and trade-offs](DECISIONS.md)

## Author
Banothu Nithin, Full Stack and Applied AI Engineer, Hyderabad..
