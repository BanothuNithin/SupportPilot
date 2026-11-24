import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.executor import ToolExecutionError, get_tool_executor, redact, ToolExecutor
from app.actions.registry import TOOLS, UnknownTool, validate_arguments
from app.api.deps import get_current_user, require_roles
from app.api.tenant import get_owned
from app.db.session import get_db
from app.integrations.service import get_env
from app.models import PendingAction, Role, Ticket, User
from app.schemas.action import ActionApproveIn, ActionOut

router = APIRouter(prefix="/actions", tags=["actions"])


def _action_out(action: PendingAction) -> ActionOut:
    return ActionOut(
        id=action.id,
        ticket_id=action.ticket_id,
        conversation_id=action.conversation_id,
        tool=action.tool,
        arguments=json.loads(action.arguments),
        status=action.status,
        result=json.loads(action.result) if action.result else None,
        error=action.error,
        edited=action.edited,
        created_at=action.created_at,
        decided_at=action.decided_at,
    )


@router.get("", response_model=list[ActionOut])
async def list_actions(
    status: str | None = None,
    ticket_id: uuid.UUID | None = None,
    conversation_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(PendingAction).where(PendingAction.tenant_id == user.tenant_id)
    if status:
        stmt = stmt.where(PendingAction.status == status)
    if ticket_id:
        stmt = stmt.where(PendingAction.ticket_id == ticket_id)
    if conversation_id is not None:
        stmt = stmt.where(PendingAction.conversation_id == conversation_id)
    rows = await db.scalars(stmt.order_by(PendingAction.created_at.desc()))
    return [_action_out(action) for action in rows]


@router.post("/{action_id}/approve", response_model=ActionOut)
async def approve_action(
    action_id: uuid.UUID,
    body: ActionApproveIn,
    user: User = Depends(require_roles(Role.owner, Role.admin)),
    db: AsyncSession = Depends(get_db),
    executor: ToolExecutor = Depends(get_tool_executor),
):
    action = await get_owned(db, PendingAction, action_id, user.tenant_id)
    if action.tool not in TOOLS:
        raise HTTPException(status.HTTP_409_CONFLICT, "Tool is not allowed")

    if body.arguments is not None:
        try:
            validated = validate_arguments(action.tool, body.arguments)
        except (UnknownTool, ValidationError, ValueError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
        action.arguments = json.dumps(validated)
        action.edited = True
        await db.flush()

    now = datetime.now(timezone.utc)
    cas = await db.execute(
        update(PendingAction)
        .where(
            PendingAction.id == action.id,
            PendingAction.tenant_id == action.tenant_id,
            PendingAction.status.in_(["pending", "failed"]),
        )
        .values(status="approved", decided_by=user.id, decided_at=now)
    )
    if cas.rowcount != 1:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Action is no longer pending")
    await db.commit()

    spec = TOOLS[action.tool]
    try:
        env = await get_env(db, action.tenant_id, spec.integration_kind)
    except Exception:
        await db.rollback()
        action = await get_owned(db, PendingAction, action_id, user.tenant_id)
        action.status = "failed"
        action.error = "Integration credentials could not be loaded"
        await db.commit()
        return _action_out(action)
    if env is None:
        action.status = "failed"
        action.error = "Integration not configured"
        await db.commit()
        return _action_out(action)

    try:
        result = await executor.call(action.tool, json.loads(action.arguments), env)
    except ToolExecutionError as exc:
        action.status = "failed"
        action.error = redact(str(exc), env)[:500]
        await db.commit()
        return _action_out(action)
    except Exception as exc:
        action.status = "failed"
        action.error = redact(str(exc), env)[:500]
        await db.commit()
        return _action_out(action)

    action.status = "executed"
    action.result = json.dumps(result)
    action.executed_at = datetime.now(timezone.utc)
    if action.tool == "create_github_issue" and action.ticket_id:
        url = result.get("url")
        if isinstance(url, str) and url.startswith("https://"):
            ticket = await get_owned(db, Ticket, action.ticket_id, action.tenant_id)
            ticket.external_issue_url = url
    await db.commit()
    return _action_out(action)


@router.post("/{action_id}/reject", response_model=ActionOut)
async def reject_action(
    action_id: uuid.UUID,
    user: User = Depends(require_roles(Role.owner, Role.admin)),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)

    result = await db.execute(
        update(PendingAction)
        .where(
            PendingAction.id == action_id,
            PendingAction.tenant_id == user.tenant_id,
            PendingAction.status == "pending",
        )
        .values(
            status="rejected",
            decided_by=user.id,
            decided_at=now,
        )
    )

    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Action is no longer pending",
        )

    await db.commit()

    action = await get_owned(db, PendingAction, action_id, user.tenant_id)
    return _action_out(action)
