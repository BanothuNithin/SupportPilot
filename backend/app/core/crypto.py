import json

from cryptography.fernet import Fernet

from app.core.config import settings


class IntegrationsNotConfigured(Exception):
    pass


def _fernet() -> Fernet:
    if not settings.integrations_key:
        raise IntegrationsNotConfigured("INTEGRATIONS_KEY is not configured")
    try:
        return Fernet(settings.integrations_key.encode())
    except (TypeError, ValueError) as exc:
        raise IntegrationsNotConfigured("INTEGRATIONS_KEY is invalid") from exc


def encrypt_json(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data, separators=(",", ":")).encode()).decode()


def decrypt_json(token: str) -> dict:
    decrypted = _fernet().decrypt(token.encode())
    return json.loads(decrypted.decode())
