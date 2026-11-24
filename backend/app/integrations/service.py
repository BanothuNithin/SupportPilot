import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_json
from app.models import Integration


async def get_env(db: AsyncSession, tenant_id: uuid.UUID, kind: str) -> dict[str, str] | None:
    integration = await db.scalar(
        select(Integration).where(
            Integration.tenant_id == tenant_id,
            Integration.kind == kind,
        )
    )
    if integration is None:
        return None

    config = decrypt_json(integration.config_encrypted)
    if kind == "github":
        return {
            "GITHUB_TOKEN": config["token"],
            "GITHUB_REPO": config["repo"],
            "GITHUB_API_BASE": settings.github_api_base,
        }
    if kind == "slack":
        return {"SLACK_WEBHOOK_URL": config["webhook_url"]}
    return None


async def is_configured(db: AsyncSession, tenant_id: uuid.UUID, kind: str) -> bool:
    integration = await db.scalar(
        select(Integration.id).where(
            Integration.tenant_id == tenant_id,
            Integration.kind == kind,
        )
    )
    return integration is not None
