# SupportPilot
Multi-tenant agentic customer-support platform: RAG, LangGraph agents, MCP tools, evals, Kubernetes.

> Status: Week 1 (foundation). Architecture diagram, demo GIF and metrics table are added as the build progresses.

## Run locally
```bash
cp .env.example .env
make infra                      # postgres, redis, qdrant, minio
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload   # http://localhost:8000/docs
make test
```
Or run everything: `make up`.

## Roadmap
| Week | Focus |
|---|---|
| 1 | Schema, JWT auth, RBAC, tenants, Docker Compose ✅ |
| 2 | Next.js chat UI, streaming (SSE), Alembic |
| 3 | RAG: ingestion, Qdrant, citations |
| 4 | LangGraph agents, memory, model router |
| 5 | MCP server and tools, human approval |
| 6 | Redis, S3, workers, Postgres RLS |
| 7 | Langfuse, RAGAS, CI evals |
| 8 | Kubernetes, Terraform, live deploy |
