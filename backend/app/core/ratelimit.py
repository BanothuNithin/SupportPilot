from fastapi import HTTPException, status

from app.core import redis as r


async def rate_limit(key: str, limit: int, window_seconds: int) -> None:
    """Fixed-window counter: at most `limit` calls per `window_seconds` for `key`."""
    count = await r.redis_client.incr(key)
    if count == 1:
        await r.redis_client.expire(key, window_seconds)
    if count > limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Try again later.")