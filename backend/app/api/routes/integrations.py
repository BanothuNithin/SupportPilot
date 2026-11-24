from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.crypto import IntegrationsNotConfigured, encrypt_json
from app.db.session import get_db
from app.models import Integration, Role, User
from app.schemas.integration import (
    GithubIntegrationIn,
    IntegrationOut,
    SlackIntegrationIn,
)

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _key_error(exc: IntegrationsNotConfigured) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))


@router.get("", response_model=list[IntegrationOut])
async def list_integrations(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = list(await db.scalars(
        select(Integration).where(Integration.tenant_id == user.tenant_id)
    ))
    output = []
    for integration in rows:
        try:
            from app.core.crypto import decrypt_json

            config = decrypt_json(integration.config_encrypted)
        except IntegrationsNotConfigured as exc:
            raise _key_error(exc)
        if integration.kind == "github":
            token = config["token"]
            summary = f"{config['repo']}, token ****{token[-4:]}"
        else:
            parsed = urlparse(config["webhook_url"])
            path = parsed.path
            summary = f"hooks.slack.com/...{path[-6:]}"
        output.append(IntegrationOut(
            kind=integration.kind,
            configured=True,
            summary=summary,
        ))
    for kind in ("github", "slack"):
        if kind not in {item.kind for item in output}:
            output.append(IntegrationOut(kind=kind, configured=False, summary=""))
    return sorted(output, key=lambda item: item.kind)


@router.put("/github", response_model=IntegrationOut)
async def put_github(
    body: GithubIntegrationIn,
    user: User = Depends(require_roles(Role.owner, Role.admin)),
    db: AsyncSession = Depends(get_db),
):
    try:
        encrypted = encrypt_json({"repo": body.repo, "token": body.token})
    except IntegrationsNotConfigured as exc:
        raise _key_error(exc)
    integration = await db.scalar(
        select(Integration).where(
            Integration.tenant_id == user.tenant_id,
            Integration.kind == "github",
        )
    )
    if integration is None:
        integration = Integration(
            tenant_id=user.tenant_id,
            kind="github",
            config_encrypted=encrypted,
        )
        db.add(integration)
    else:
        integration.config_encrypted = encrypted
    await db.commit()
    return IntegrationOut(
        kind="github",
        configured=True,
        summary=f"{body.repo}, token ****{body.token[-4:]}",
    )


@router.put("/slack", response_model=IntegrationOut)
async def put_slack(
    body: SlackIntegrationIn,
    user: User = Depends(require_roles(Role.owner, Role.admin)),
    db: AsyncSession = Depends(get_db),
):
    parsed = urlparse(body.webhook_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "hooks.slack.com"
        or not parsed.path.startswith("/services/")
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "webhook_url must be an HTTPS hooks.slack.com /services/ URL",
        )
    try:
        encrypted = encrypt_json({"webhook_url": body.webhook_url})
    except IntegrationsNotConfigured as exc:
        raise _key_error(exc)
    integration = await db.scalar(
        select(Integration).where(
            Integration.tenant_id == user.tenant_id,
            Integration.kind == "slack",
        )
    )
    if integration is None:
        integration = Integration(
            tenant_id=user.tenant_id,
            kind="slack",
            config_encrypted=encrypted,
        )
        db.add(integration)
    else:
        integration.config_encrypted = encrypted
    await db.commit()
    return IntegrationOut(
        kind="slack",
        configured=True,
        summary=f"hooks.slack.com/...{parsed.path[-6:]}",
    )


@router.delete("/{kind}", status_code=204)
async def delete_integration(
    kind: str,
    user: User = Depends(require_roles(Role.owner, Role.admin)),
    db: AsyncSession = Depends(get_db),
):
    if kind not in {"github", "slack"}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    integration = await db.scalar(
        select(Integration).where(
            Integration.tenant_id == user.tenant_id,
            Integration.kind == kind,
        )
    )
    if integration is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    await db.delete(integration)
    await db.commit()
