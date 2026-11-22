# SupportPilot

![CI](https://github.com/BanothuNithin/supportpilot/actions/workflows/ci.yml/badge.svg)

**A multi-tenant, agentic AI customer support platform under development.** Week 3 delivers tenant-scoped knowledge-base uploads and cited AI chat. Agent workflows, approved MCP actions (GitHub issue, Slack alert), and human escalation remain planned.

> **Status: Week 3 of 8.** The multi-tenant foundation, chat UI and streaming, and tenant-isolated knowledge-base retrieval are built and tested. I'm building this in public; see the roadmap below.

![Architecture](docs/architecture.png)

## Why this project
Most AI support bots only answer questions. Real support needs three more things:

1. **Data isolation:** many companies share one platform; their data must never mix.
2. **Reliable answers:** cite sources, and say "I don't know" (and hand over to a human) instead of guessing.
3. **Action:** a bug report should create a ticket and alert the team, with human approval for risky steps.

## What works today (Week 3)
- Tenant registration: creating a company also creates its owner
- JWT access tokens (15 min) plus refresh tokens with **rotation**, stored hashed in Redis; logout revokes them
- Role-based access control: `owner`, `admin`, `agent`
- Login rate limiting (5 attempts per minute per IP, Redis)
- Tenant-scoped customers and conversations API (other tenants' objects return 404)
- PostgreSQL schema managed with Alembic migrations
- 33 backend tests, including tenant-isolation tests, run in GitHub Actions
- Docker Compose for PostgreSQL, Redis, Qdrant and MinIO
- Next.js chat UI with SSE replies and a Stop button to cancel generation
- Tenant-scoped knowledge-base upload (up to 5 MB), listing and deletion for `.txt`, `.md` and `.pdf` files; status moves from `indexing` to `ready` or `failed`, with a chunk count and failure error
- FastAPI BackgroundTasks extract text (pypdf for PDFs), create paragraph-aware chunks of 800 characters with 100 characters of overlap, embed them and upsert them into Qdrant
- Ollama `nomic-embed-text` embeddings (768 dimensions) through an OpenAI-compatible client; tests use a deterministic fake hashing embedder
- One Qdrant `chunks` collection uses cosine distance, a `tenant_id` keyword index (`is_tenant=true`), and a mandatory tenant filter for every search
- Chat retrieves up to 4 tenant chunks with an initial minimum score of 0.35 and streams citations before answer tokens; citations are saved with messages
- Prompts require answers from retrieved context, citations, and an offer to connect a human agent when the answer is not found; uploaded content is untrusted reference data and its instructions must not be followed
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
| Agents | LangGraph (Triage, Resolver, Action), model router (Claude / GPT / Ollama) | Week 4 |
| Tools | Custom MCP server (GitHub, Slack, browser) | Week 5 |
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
| `POST /conversations/{id}/messages` | Stream chat events: citations, tokens, done or error | Authenticated |
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
- A source chip can still appear next to a refusal if the model cites a document while saying it does not know. Structured output is planned for Week 4.

## Roadmap
| Week | Focus |
|---|---|
| 1 | Schema, auth, RBAC, tenants, migrations, CI (done) |
| 2 | Next.js chat UI, SSE streaming (done) |
| 3 | RAG ingestion, Qdrant, citations (done) |
| 4 | LangGraph agents, memory, model router |
| 5 | MCP tools, human approval, escalation |
| 6 | Redis workers, S3, Postgres Row-Level Security |
| 7 | Langfuse, RAGAS evals in CI, load tests |
| 8 | Kubernetes, Terraform, live deployment |

## Docs
- [Architecture and data model](docs/ARCHITECTURE.md)
- [Design decisions and trade-offs](DECISIONS.md)

## Author
Banothu Nithin, Full Stack and Applied AI Engineer, Hyderabad.
