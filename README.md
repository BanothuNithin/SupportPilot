# SupportPilot

![CI](https://github.com/BanothuNithin/supportpilot/actions/workflows/ci.yml/badge.svg)

**A multi-tenant, agentic AI customer support platform under development.** Week 4 delivers a deterministic LangGraph support workflow with tenant-scoped retrieval, grounded answers, and ticket-based human escalation. Approved MCP actions (such as GitHub issues and Slack alerts) remain planned.

> **Status: Week 4 of 8.** The multi-tenant foundation, chat UI and streaming, tenant-isolated knowledge-base retrieval, and rule-driven support agent workflow are built and tested. I'm building this in public; see the roadmap below.

![Architecture](docs/architecture.png)

## Why this project
Most AI support bots only answer questions. Real support needs three more things:

1. **Data isolation:** many companies share one platform; their data must never mix.
2. **Reliable answers:** cite sources, and say "I don't know" (and route to a support ticket when needed) instead of guessing.
3. **Action:** support issues should reach the right team; ticket alerts and approval-gated integrations are planned.

## What works today (Week 4)
- Tenant registration: creating a company also creates its owner
- JWT access tokens (15 min) plus refresh tokens with **rotation**, stored hashed in Redis; logout revokes them
- Role-based access control: `owner`, `admin`, `agent`
- Login rate limiting (5 attempts per minute per IP, Redis)
- Tenant-scoped customers and conversations API (other tenants' objects return 404)
- PostgreSQL schema managed with Alembic migrations
- 110 backend tests, including tenant-isolation and agent workflow tests, run in GitHub Actions
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

## Tech stack
| Layer | Technology | Status |
|---|---|---|
| API | Python 3.12, FastAPI (async), SQLAlchemy 2, Pydantic | Done |
| Data | PostgreSQL, Redis, Alembic | Done |
| Infra | Docker Compose, GitHub Actions | Done |
| Frontend | Next.js, TypeScript | Done |
| RAG | Qdrant, embeddings, citations | Done |
| Agents | LangGraph rule-based triage, retrieval, grader, resolver and escalation; OpenAI-compatible model provider/router | Built |
| Tools | Custom MCP server (GitHub, Slack, browser), human approval for actions | Week 5 |
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
alembic upgrade head
uvicorn app.main:app --reload
```

Open http://localhost:8000/docs for the interactive API.

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

## Roadmap
| Week | Focus |
|---|---|
| 1 | Schema, auth, RBAC, tenants, migrations, CI (done) |
| 2 | Next.js chat UI, SSE streaming (done) |
| 3 | RAG ingestion, Qdrant, citations (done) |
| 4 | LangGraph support workflow, deterministic triage and fast paths, follow-up-aware retrieval, grading, escalation, and streaming observability (done) |
| 5 | MCP tools and approval-gated actions |
| 6 | Redis workers, S3, Postgres Row-Level Security |
| 7 | Langfuse, RAGAS evals in CI, load tests |
| 8 | Kubernetes, Terraform, live deployment |

## Docs
- [Architecture and data model](docs/ARCHITECTURE.md)
- [Design decisions and trade-offs](DECISIONS.md)

## Author
Banothu Nithin, Full Stack and Applied AI Engineer, Hyderabad.
