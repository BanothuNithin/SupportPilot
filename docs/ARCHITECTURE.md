# Architecture

Solid boxes are built. Dashed boxes are planned (week in brackets).

```mermaid
flowchart TD
    UI["Next.js chat widget and dashboard (Week 2)"]:::planned
    API["FastAPI: auth, RBAC, tenant scoping"]:::done
    AG["LangGraph agents: Triage, Resolver, Action (Week 4)"]:::planned
    RAG["RAG: Qdrant + citations (Week 3)"]:::planned
    LLM["Model router: Claude / GPT / Ollama (Week 4)"]:::planned
    MCP["MCP tools: GitHub, Slack, browser (Week 5)"]:::planned
    PG[("PostgreSQL")]:::done
    RD[("Redis: refresh tokens, rate limits")]:::done
    S3[("MinIO / S3 (Week 6)")]:::planned
    OBS["Langfuse + RAGAS (Week 7)"]:::planned

    UI --> API
    API --> AG
    AG --> RAG
    AG --> LLM
    AG --> MCP
    API --> PG
    API --> RD
    RAG --> S3
    AG -.-> OBS

    classDef done fill:#14532d,stroke:#4ade80,color:#ffffff;
    classDef planned fill:#1f2937,stroke:#6b7280,color:#d1d5db,stroke-dasharray: 5 5;
```

## Request flow (target design)
1. The customer sends a message from the chat widget.
2. FastAPI verifies the token, resolves the tenant and checks rate limits.
3. The Triage agent classifies intent and priority.
4. The Resolver agent retrieves tenant-filtered chunks from Qdrant and answers with citations, streamed over SSE.
5. The Action agent calls MCP tools. Sensitive actions wait for human approval.
6. Low confidence escalates the conversation to a human agent.
7. Every step is traced for quality, latency and cost.

## Auth flow (built)
```mermaid
sequenceDiagram
    participant C as Client
    participant A as FastAPI
    participant R as Redis
    participant D as PostgreSQL
    C->>A: POST /auth/login (email, password)
    A->>D: find user, verify bcrypt hash
    A->>R: store sha256(refresh token), TTL 7 days
    A-->>C: access token (15 min) + refresh token
    C->>A: POST /auth/refresh (refresh token)
    A->>R: lookup hash and delete it (one use)
    A->>R: store hash of the new refresh token
    A-->>C: new access token + new refresh token
    C->>A: POST /auth/logout (refresh token)
    A->>R: delete hash
```

## Data model
Every business table carries `tenant_id`.

```mermaid
erDiagram
    TENANTS ||--o{ USERS : has
    TENANTS ||--o{ CUSTOMERS : has
    TENANTS ||--o{ CONVERSATIONS : has
    TENANTS ||--o{ DOCUMENTS : owns
    CUSTOMERS ||--o{ CONVERSATIONS : starts
    CONVERSATIONS ||--o{ MESSAGES : contains
    CONVERSATIONS ||--o{ TICKETS : escalates_to
    USERS ||--o{ TICKETS : assigned
    TENANTS {
        uuid id PK
        string name
        string slug
    }
    USERS {
        uuid id PK
        uuid tenant_id FK
        string email
        string role
    }
    CUSTOMERS {
        uuid id PK
        uuid tenant_id FK
        string external_id
        string email
    }
    CONVERSATIONS {
        uuid id PK
        uuid tenant_id FK
        uuid customer_id FK
        string status
    }
    MESSAGES {
        uuid id PK
        uuid conversation_id FK
        string sender
        text content
    }
    TICKETS {
        uuid id PK
        uuid conversation_id FK
        string priority
        string status
    }
    DOCUMENTS {
        uuid id PK
        uuid tenant_id FK
        string filename
        string status
    }
```

## Tenant isolation rules
1. The tenant comes from the verified JWT, never from the request body.
2. Every query filters by `tenant_id`, through the shared `get_owned` helper for lookups by id.
3. Another tenant's object returns 404, not 403, so ids cannot be probed.
4. Vector searches (Week 3) always carry a tenant filter.
5. Week 6 adds PostgreSQL Row-Level Security as a second line of defense.
