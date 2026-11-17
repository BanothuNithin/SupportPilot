import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.security import create_access_token, hash_password, verify_password
from app.core.tokens import consume_refresh_token, issue_refresh_token, revoke_refresh_token
from app.db.session import get_db
from app.models import Role, Tenant, User
from app.schemas.auth import RefreshIn, RegisterIn, TokenOut, UserCreateIn, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "tenant"
    return f"{base}-{uuid.uuid4().hex[:6]}"


async def _email_taken(db: AsyncSession, email: str) -> bool:
    return (await db.scalar(select(User.id).where(User.email == email))) is not None


async def _token_pair(user: User) -> TokenOut:
    return TokenOut(
        access_token=create_access_token(str(user.id), str(user.tenant_id), user.role.value),
        refresh_token=await issue_refresh_token(str(user.id)),
    )


@router.post("/register", response_model=TokenOut, status_code=201)
async def register(body: RegisterIn, db: AsyncSession = Depends(get_db)):
    """Create a new tenant (company) and its owner in one step."""
    if await _email_taken(db, body.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    tenant = Tenant(name=body.company_name, slug=_slugify(body.company_name))
    db.add(tenant)
    await db.flush()
    user = User(
        tenant_id=tenant.id,
        email=body.email,
        hashed_password=hash_password(body.password),
        role=Role.owner,
    )
    db.add(user)
    await db.commit()
    return await _token_pair(user)


@router.post("/login", response_model=TokenOut)
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.email == form.username))
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    return await _token_pair(user)


@router.post("/refresh", response_model=TokenOut)
async def refresh(body: RefreshIn, db: AsyncSession = Depends(get_db)):
    user_id = await consume_refresh_token(body.refresh_token)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")
    user = await db.get(User, user_uuid)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")
    return await _token_pair(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshIn):
    await revoke_refresh_token(body.refresh_token)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


@router.post("/users", response_model=UserOut, status_code=201)
async def invite_user(
    body: UserCreateIn,
    admin: User = Depends(require_roles(Role.owner, Role.admin)),
    db: AsyncSession = Depends(get_db),
):
    """Admins add staff to THEIR OWN tenant only (tenant_id comes from the token, never the body)."""
    if body.role == Role.owner:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot create another owner")
    if await _email_taken(db, body.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(
        tenant_id=admin.tenant_id,
        email=body.email,
        hashed_password=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    await db.commit()
    return user


@router.get("/users", response_model=list[UserOut])
async def list_users(
    admin: User = Depends(require_roles(Role.owner, Role.admin)),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.scalars(select(User).where(User.tenant_id == admin.tenant_id))
    return list(rows)
