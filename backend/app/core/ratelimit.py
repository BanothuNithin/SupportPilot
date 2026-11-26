import logging

from fastapi import HTTPException, status
from redis.exceptions import WatchError

from app.core import redis as r

log = logging.getLogger(__name__)


async def _increment_with_expiry(key: str, window_seconds: int) -> int:
    async with r.redis_client.pipeline(transaction=True) as pipe:
        while True:
            try:
                await pipe.watch(key)
                current = await pipe.get(key)
                pipe.multi()
                if current is None:
                    pipe.set(key, 1, ex=window_seconds)
                    await pipe.execute()
                    return 1
                pipe.incr(key)
                result = await pipe.execute()
                return int(result[0])
            except WatchError:
                await pipe.reset()


async def rate_limit(
    key: str,
    limit: int,
    window_seconds: int,
    *,
    fail_open: bool = False,
) -> None:
    """Fixed-window counter: at most `limit` calls per `window_seconds` for `key`."""
    try:
        count = await _increment_with_expiry(key, window_seconds)
        retry_after = (
            max(1, int(await r.redis_client.ttl(key))) if count > limit else None
        )
    except Exception:
        if not fail_open:
            raise
        log.warning("Chat rate limiter unavailable; allowing request")
        return
    if count > limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )