# Week 1 checklist

## Day 1-2: Run and understand what exists
- [ ] `docker compose up` infra, run backend, open /docs, register, login, call /me
- [ ] Read every file. Be able to explain: JWT flow, `require_roles`, why tenant_id comes from the token
- [ ] Run `make test`; break tenant isolation on purpose, watch the test fail

## Day 3-4: Extend (this is YOUR code, not mine)
- [ ] Add refresh tokens + logout (Redis denylist)
- [ ] Add rate limiting on /login with Redis (5 attempts/min per IP)
- [ ] Add `/api/v1/customers` and `/api/v1/conversations` CRUD, tenant-scoped, with tests

## Day 5: Infra and polish
- [ ] Add Alembic, generate the first migration, remove create_all
- [ ] Draw the architecture diagram (excalidraw) into the README
- [ ] Push to GitHub, confirm CI is green

## Day 6-7: Interview prep for Week 1 topics
Be able to answer without notes:
1. How do you isolate tenants? Trade-offs of the 3 models?
2. JWT vs sessions? How do you revoke a JWT?
3. Why hash with bcrypt? What is a salt and the cost factor?
4. What is RBAC? How is it implemented here?
5. Sync vs async in FastAPI. When does async NOT help?
6. What is an N+1 query? Where could it appear in this schema?
