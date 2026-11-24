import uuid

import pytest
from cryptography.fernet import InvalidToken
from sqlalchemy import select, text

from app.core.config import settings

from app.core.crypto import decrypt_json, encrypt_json
from app.db.session import SessionLocal
from app.integrations.service import get_env, is_configured
from app.main import app
from app.models import Integration


async def register(client, company, email, role=None):
    response = await client.post(
        "/api/v1/auth/register",
        json={"company_name": company, "email": email, "password": "password123"},
    )
    assert response.status_code == 201
    owner_headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    if role is not None:
        invited = await client.post(
            "/api/v1/auth/users",
            headers=owner_headers,
            json={"email": f"{role}@{email.split('@')[-1]}", "password": "password123", "role": role},
        )
        assert invited.status_code == 201
        login = await client.post(
            "/api/v1/auth/login",
            data={"username": f"{role}@{email.split('@')[-1]}", "password": "password123"},
        )
        assert login.status_code == 200
        return owner_headers, {"Authorization": f"Bearer {login.json()['access_token']}"}
    return owner_headers


async def test_github_storage_is_encrypted_masked_and_upserts(client):
    owner = await register(client, "Acme", "owner@acme.com")
    token = "ghp_" + ("A" * 24)

    first = await client.put(
        "/api/v1/integrations/github",
        headers=owner,
        json={"repo": "Acme/widgets", "token": token},
    )
    assert first.status_code == 200

    async with SessionLocal() as db:
        stored = (await db.execute(
            text("SELECT config_encrypted FROM integrations WHERE kind = 'github'")
        )).scalar_one_or_none()
    assert stored
    assert token not in stored

    listed = await client.get("/api/v1/integrations", headers=owner)
    assert listed.status_code == 200
    github = next(item for item in listed.json() if item["kind"] == "github")
    assert github["configured"] is True
    assert token not in str(github)
    assert "Acme/widgets" in github["summary"]
    assert "****" + token[-4:] in github["summary"]

    replacement = "ghp_" + ("B" * 24)
    replaced = await client.put(
        "/api/v1/integrations/github",
        headers=owner,
        json={"repo": "Acme/new-repo", "token": replacement},
    )
    assert replaced.status_code == 200
    async with SessionLocal() as db:
        rows = (await db.scalars(select(Integration).where(Integration.kind == "github"))).all()
        assert len(rows) == 1


async def test_slack_storage_is_encrypted_and_summary_is_masked(client):
    owner = await register(client, "Acme", "owner@acme.com")
    webhook = "https://hooks.slack.com/services/T123/B456/secretvalue"

    response = await client.put(
        "/api/v1/integrations/slack",
        headers=owner,
        json={"webhook_url": webhook},
    )
    assert response.status_code == 200
    async with SessionLocal() as db:
        stored = (await db.execute(
            text("SELECT config_encrypted FROM integrations WHERE kind = 'slack'")
        )).scalar_one_or_none()
    assert stored
    assert webhook not in stored
    assert "secretvalue" not in stored

    listed = await client.get("/api/v1/integrations", headers=owner)
    slack = next(item for item in listed.json() if item["kind"] == "slack")
    assert webhook not in str(slack)
    assert slack["summary"].startswith("hooks.slack.com/")


async def test_agent_role_and_tenant_isolation(client):
    owner, agent = await register(client, "Acme", "owner@acme.com", role="agent")
    owner_b = await register(client, "Globex", "owner@globex.com")

    assert (await client.put(
        "/api/v1/integrations/github",
        headers=agent,
        json={"repo": "Acme/widgets", "token": "ghp_" + ("A" * 24)},
    )).status_code == 403
    assert (await client.delete("/api/v1/integrations/github", headers=agent)).status_code == 403
    assert (await client.get("/api/v1/integrations", headers=agent)).status_code == 200

    assert (await client.put(
        "/api/v1/integrations/github",
        headers=owner,
        json={"repo": "Acme/widgets", "token": "ghp_" + ("A" * 24)},
    )).status_code == 200
    assert (await client.put(
        "/api/v1/integrations/github",
        headers=owner_b,
        json={"repo": "Globex/widgets", "token": "ghp_" + ("B" * 24)},
    )).status_code == 200

    listed_b = await client.get("/api/v1/integrations", headers=owner_b)
    assert "Acme/widgets" not in str(listed_b.json())
    assert "Globex/widgets" in str(listed_b.json())
    assert (await client.delete("/api/v1/integrations/github", headers=owner_b)).status_code == 204
    assert (await client.get("/api/v1/integrations", headers=owner)).status_code == 200
    assert (await client.delete("/api/v1/integrations/github", headers=owner)).status_code == 204
    assert (await client.delete("/api/v1/integrations/github", headers=owner)).status_code == 404


@pytest.mark.parametrize(
    "webhook_url",
    [
        "http://hooks.slack.com/services/x",
        "https://hooks.slack.com.evil.com/services/x",
        "https://evil.com/services/x",
        "https://hooks.slack.com/other/x",
    ],
)
async def test_rejects_invalid_slack_hosts(client, webhook_url):
    owner = await register(client, "Acme", "owner@acme.com")
    response = await client.put(
        "/api/v1/integrations/slack",
        headers=owner,
        json={"webhook_url": webhook_url},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("repo", "token"),
    [
        ("ownerrepo", "ghp_" + ("A" * 24)),
        ("owner/repo/extra", "ghp_" + ("A" * 24)),
        ("../repo", "ghp_" + ("A" * 24)),
        ("owner/repo", "short"),
        ("owner/repo", "ghp_" + ("A" * 252)),
        ("owner/repo", "contains whitespace"),
    ],
)
async def test_rejects_invalid_github_shapes(client, repo, token):
    owner = await register(client, "Acme", "owner@acme.com")
    response = await client.put(
        "/api/v1/integrations/github",
        headers=owner,
        json={"repo": repo, "token": token},
    )
    assert response.status_code == 422


async def test_delete_missing_integration_returns_404(client):
    owner = await register(client, "Acme", "owner@acme.com")
    response = await client.delete("/api/v1/integrations/github", headers=owner)
    assert response.status_code == 404


async def test_get_env_and_is_configured(client):
    owner = await register(client, "Acme", "owner@acme.com")
    async with SessionLocal() as db:
        assert await get_env(db, uuid.UUID((await client.get("/api/v1/auth/me", headers=owner)).json()["tenant_id"]), "github") is None

    github_token = "ghp_" + ("Z" * 24)
    await client.put(
        "/api/v1/integrations/github",
        headers=owner,
        json={"repo": "Acme/widgets", "token": github_token},
    )
    me = (await client.get("/api/v1/auth/me", headers=owner)).json()
    tenant_id = uuid.UUID(me["tenant_id"])
    async with SessionLocal() as db:
        env = await get_env(db, tenant_id, "github")
        assert env == {
            "GITHUB_TOKEN": github_token,
            "GITHUB_REPO": "Acme/widgets",
            "GITHUB_API_BASE": "https://api.github.com",
        }
        assert await is_configured(db, tenant_id, "github") is True
        assert await is_configured(db, tenant_id, "slack") is False

    await client.put(
        "/api/v1/integrations/slack",
        headers=owner,
        json={"webhook_url": "https://hooks.slack.com/services/T1/B2/value"},
    )
    async with SessionLocal() as db:
        env = await get_env(db, tenant_id, "slack")
        assert env == {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T1/B2/value"}


def test_crypto_round_trip_and_tamper_detection():
    data = {"token": "supersecret", "repo": "owner/repo"}
    encrypted = encrypt_json(data)
    assert "supersecret" not in encrypted
    assert decrypt_json(encrypted) == data

    tampered = encrypted[:-1] + ("A" if encrypted[-1] != "A" else "B")
    with pytest.raises(InvalidToken):
        decrypt_json(tampered)


async def test_missing_integrations_key_returns_503(client, monkeypatch):
    owner = await register(client, "Acme", "owner@acme.com")
    monkeypatch.setattr(settings, "integrations_key", "")
    response = await client.put(
        "/api/v1/integrations/github",
        headers=owner,
        json={"repo": "Acme/widgets", "token": "ghp_" + ("A" * 24)},
    )
    assert response.status_code == 503
    assert "INTEGRATIONS_KEY" in response.text
