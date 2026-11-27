import uuid
from contextvars import ContextVar

from app.core.security import decode_token
from app.db.tenant import set_tenant

_current_tenant_id = ContextVar("test_tenant_id", default=None)


def tenant_id_from_headers(headers):
    token = headers["Authorization"].split(" ", 1)[1]
    return uuid.UUID(decode_token(token)["tid"])


def activate_test_tenant(headers):
    _current_tenant_id.set(tenant_id_from_headers(headers))


async def set_test_tenant(session, tenant_id):
    await set_tenant(session, tenant_id)


class _TenantSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        tenant_id = _current_tenant_id.get()
        if tenant_id is not None:
            await set_test_tenant(self._session, tenant_id)
        return await self._session.__aenter__()

    async def __aexit__(self, exc_type, exc, traceback):
        return await self._session.__aexit__(exc_type, exc, traceback)


def tenant_scoped_session_factory(session_factory):
    def create_session(*args, **kwargs):
        return _TenantSessionContext(session_factory(*args, **kwargs))

    return create_session
