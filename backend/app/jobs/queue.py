import json
import logging

from app.core import redis as r

log = logging.getLogger(__name__)
QUEUE_KEY = "jobs:main"


async def enqueue(job_type: str, job_id, tenant_id) -> bool:
    job = {
        "type": job_type,
        "id": str(job_id),
        "tenant_id": str(tenant_id),
    }
    try:
        await r.redis_client.lpush(QUEUE_KEY, json.dumps(job))
        return True
    except Exception:
        log.warning("job enqueue failed job_type=%s id=%s", job_type, job_id)
        return False


async def dequeue(timeout_seconds: int) -> dict | None:
    item = await r.redis_client.brpop(QUEUE_KEY, timeout=timeout_seconds)
    if item is None:
        return None
    try:
        job = json.loads(item[1])
        if not isinstance(job, dict):
            raise ValueError("job must be an object")
        return job
    except (TypeError, ValueError):
        log.warning("invalid job message dropped")
        return None
