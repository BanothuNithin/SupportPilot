async def register(client, company, email, password="password123"):
    response = await client.post(
        "/api/v1/auth/register",
        json={"company_name": company, "email": email, "password": password},
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_create_and_list_customers_is_tenant_scoped(client):
    headers_a = await register(client, "Acme", "owner@acme.com")
    headers_b = await register(client, "Globex", "owner@globex.com")

    created = await client.post(
        "/api/v1/customers",
        headers=headers_a,
        json={"external_id": "customer-1", "email": "person@example.com"},
    )
    assert created.status_code == 201
    assert len((await client.get("/api/v1/customers", headers=headers_a)).json()) == 1
    assert (await client.get("/api/v1/customers", headers=headers_b)).json() == []


async def test_other_tenant_cannot_read_update_or_delete_customer(client):
    headers_a = await register(client, "Acme", "owner@acme.com")
    headers_b = await register(client, "Globex", "owner@globex.com")
    created = await client.post(
        "/api/v1/customers",
        headers=headers_a,
        json={"email": "person@example.com"},
    )
    customer_id = created.json()["id"]

    read = await client.get(f"/api/v1/customers/{customer_id}", headers=headers_b)
    update = await client.patch(
        f"/api/v1/customers/{customer_id}",
        headers=headers_b,
        json={"email": "changed@example.com"},
    )
    delete = await client.delete(
        f"/api/v1/customers/{customer_id}", headers=headers_b
    )
    assert read.status_code == update.status_code == delete.status_code == 404


async def test_customer_update_changes_only_submitted_field(client):
    headers = await register(client, "Acme", "owner@acme.com")
    created = await client.post(
        "/api/v1/customers",
        headers=headers,
        json={"external_id": "customer-1", "email": "person@example.com"},
    )

    updated = await client.patch(
        f"/api/v1/customers/{created.json()['id']}",
        headers=headers,
        json={"external_id": "customer-2"},
    )
    assert updated.status_code == 200
    assert updated.json()["external_id"] == "customer-2"
    assert updated.json()["email"] == "person@example.com"


async def test_agent_cannot_delete_customer(client):
    owner_headers = await register(client, "Acme", "owner@acme.com")
    created = await client.post(
        "/api/v1/customers", headers=owner_headers, json={"email": "person@example.com"}
    )
    invited = await client.post(
        "/api/v1/auth/users",
        headers=owner_headers,
        json={"email": "agent@acme.com", "password": "password123", "role": "agent"},
    )
    assert invited.status_code == 201
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": "agent@acme.com", "password": "password123"},
    )
    agent_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    deleted = await client.delete(
        f"/api/v1/customers/{created.json()['id']}", headers=agent_headers
    )
    assert deleted.status_code == 403
