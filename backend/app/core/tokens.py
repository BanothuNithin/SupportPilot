import hashlib
import secrets

from app.core import redis as r

REFRESH_TTL_SECONDS = 7 * 24 * 3600


def _key(token: str) -> str:
    # Store only the hash, so a Redis leak does not leak usable tokens.
    return "refresh:" + hashlib.sha256(token.encode()).hexdigest()


async def issue_refresh_token(user_id: str) -> str:
    token = secrets.token_urlsafe(48)
    await r.redis_client.set(_key(token), user_id, ex=REFRESH_TTL_SECONDS)
    return token


async def consume_refresh_token(token: str) -> str | None:
    """Return the user id and delete the token (rotation: one use only)."""
    user_id = await r.redis_client.get(_key(token))
    if user_id:
        await r.redis_client.delete(_key(token))
    return user_id


async def revoke_refresh_token(token: str) -> None:
    await r.redis_client.delete(_key(token))