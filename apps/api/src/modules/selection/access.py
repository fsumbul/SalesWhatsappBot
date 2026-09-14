"""Customer link grants access to exactly one request, never team review APIs."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from jose import JWTError, jwt

from src.core.config import get_settings


def form_token(row: Any) -> str:
    settings = get_settings()
    return str(
        jwt.encode(
            {
                "type": "customer_form",
                "sub": str(row.id),
                "tid": str(row.tenant_id),
                "exp": datetime.now(UTC) + timedelta(days=7),
            },
            settings.app_secret_key,
            algorithm=settings.jwt_algorithm,
        )
    )


def form_claims(token: str) -> tuple[UUID, UUID]:
    settings = get_settings()
    try:
        claims = jwt.decode(token, settings.app_secret_key, algorithms=[settings.jwt_algorithm])
        if claims.get("type") != "customer_form":
            raise ValueError("Wrong grant")
        return UUID(claims["tid"]), UUID(claims["sub"])
    except (JWTError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            401, "Form bağlantısı geçersiz veya süresi dolmuş. WhatsApp üzerinden yeni bağlantı isteyin."
        ) from exc
