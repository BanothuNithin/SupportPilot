async def register(client, company, email, pw="password123"):
    r = await client.post("/api/v1/auth/register", json={"company_name": company, "email": email, "password": pw})
    assert r.status_code == 201
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_register_and_me(client):
    h = await register(client, "Acme Inc", "owner@acme.com")
    r = await client.get("/api/v1/auth/me", headers=h)
    assert r.status_code == 200
    assert r.json()["role"] == "owner"


async def test_login(client):
    await register(client, "Acme Inc", "owner@acme.com")
    r = await client.post("/api/v1/auth/login", data={"username": "owner@acme.com", "password": "password123"})
    assert r.status_code == 200
    assert r.json()["refresh_token"]
    bad = await client.post("/api/v1/auth/login", data={"username": "owner@acme.com", "password": "wrong-pass"})
    assert bad.status_code == 401


async def test_refresh_rotates_token_and_rejects_unknown_token(client):
    registered = await client.post(
        "/api/v1/auth/register",
        json={"company_name": "Acme", "email": "owner@acme.com", "password": "password123"},
    )
    old_refresh_token = registered.json()["refresh_token"]

    refreshed = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old_refresh_token}
    )
    assert refreshed.status_code == 200
    new_refresh_token = refreshed.json()["refresh_token"]
    assert new_refresh_token != old_refresh_token

    rejected = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old_refresh_token}
    )
    assert rejected.status_code == 401
    unknown = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "unknown-token"}
    )
    assert unknown.status_code == 401


async def test_logout_revokes_refresh_token(client):
    registered = await client.post(
        "/api/v1/auth/register",
        json={"company_name": "Acme", "email": "owner@acme.com", "password": "password123"},
    )
    refresh_token = registered.json()["refresh_token"]

    logged_out = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": refresh_token}
    )
    assert logged_out.status_code == 204
    rejected = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert rejected.status_code == 401


async def test_duplicate_email(client):
    await register(client, "Acme", "a@acme.com")
    r = await client.post("/api/v1/auth/register", json={"company_name": "Other", "email": "a@acme.com", "password": "password123"})
    assert r.status_code == 409


async def test_requires_token(client):
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_rbac_agent_cannot_invite(client):
    owner = await register(client, "Acme", "owner@acme.com")
    r = await client.post("/api/v1/auth/users", headers=owner, json={"email": "agent@acme.com", "password": "password123", "role": "agent"})
    assert r.status_code == 201
    tok = (await client.post("/api/v1/auth/login", data={"username": "agent@acme.com", "password": "password123"})).json()["access_token"]
    agent = {"Authorization": f"Bearer {tok}"}
    r = await client.post("/api/v1/auth/users", headers=agent, json={"email": "x@acme.com", "password": "password123"})
    assert r.status_code == 403


async def test_tenant_isolation(client):
    a = await register(client, "Acme", "owner@acme.com")
    b = await register(client, "Globex", "owner@globex.com")
    await client.post("/api/v1/auth/users", headers=a, json={"email": "agent@acme.com", "password": "password123"})
    users_a = (await client.get("/api/v1/auth/users", headers=a)).json()
    users_b = (await client.get("/api/v1/auth/users", headers=b)).json()
    assert {u["email"] for u in users_a} == {"owner@acme.com", "agent@acme.com"}
    assert {u["email"] for u in users_b} == {"owner@globex.com"}
