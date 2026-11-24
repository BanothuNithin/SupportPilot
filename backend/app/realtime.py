import json
import logging
import uuid
from datetime import datetime
from typing import AsyncIterator

import anyio

from app.core import redis as r


log = logging.getLogger(__name__)


def channel(tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> str:
    return f"conv:{tenant_id}:{conversation_id}"


def _json_default(value):
    if isinstance(value, (uuid.UUID, datetime)):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


async def publish_message(
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    payload: dict,
) -> None:
    try:
        encoded = json.dumps(payload, default=_json_default)
        with anyio.move_on_after(1):
            await r.redis_client.publish(channel(tenant_id, conversation_id), encoded)
    except Exception:
        log.warning("Realtime publish failed")


async def conversation_events(
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    heartbeat_seconds: int | float = 15,
) -> AsyncIterator[str]:
    pubsub = r.redis_client.pubsub()
    name = channel(tenant_id, conversation_id)
    try:
        await pubsub.subscribe(name)
        while True:
            with anyio.move_on_after(heartbeat_seconds):
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=heartbeat_seconds,
                )
            if "message" in locals() and message is not None:
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode()
                yield f"event: message\ndata: {data}\n\n"
                message = None
            else:
                yield ": ping\n\n"
    finally:
        await pubsub.unsubscribe(name)
        await pubsub.aclose()
