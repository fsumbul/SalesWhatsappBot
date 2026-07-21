"""Security primitives: password hashing and JWT encoding/decoding.

Kept intentionally small; real auth logic lives in `modules.auth` (Phase 1).
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import get_settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

TokenType = Literal["access", "refresh"]


def hash_password(plain: str) -> str:
    return cast(str, _pwd_context.hash(plain))


def verify_password(plain: str, hashed: str) -> bool:
    return cast(bool, _pwd_context.verify(plain, hashed))


def create_token(
    *,
    subject: str | UUID,
    tenant_id: str | UUID | None,
    token_type: TokenType,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    if token_type == "access":  # noqa: S105 - JWT token category, not a secret
        exp = now + timedelta(minutes=settings.jwt_access_ttl_minutes)
    else:
        exp = now + timedelta(days=settings.jwt_refresh_ttl_days)

    payload: dict[str, Any] = {
        "sub": str(subject),
        "tid": str(tenant_id) if tenant_id else None,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)

    return cast(str, jwt.encode(payload, settings.app_secret_key, algorithm=settings.jwt_algorithm))


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT. Raises `JWTError` on invalid/expired tokens."""
    settings = get_settings()
    return cast(
        dict[str, Any],
        jwt.decode(token, settings.app_secret_key, algorithms=[settings.jwt_algorithm]),
    )


__all__ = [
    "JWTError",
    "TokenType",
    "create_token",
    "decode_token",
    "hash_password",
    "verify_password",
]
