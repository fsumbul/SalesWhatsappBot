"""Common HTTP exceptions and error codes."""

from fastapi import HTTPException, status


class DomainError(HTTPException):
    """Base for domain-level errors returned to clients."""


class NotFoundError(DomainError):
    def __init__(self, entity: str, entity_id: str | None = None) -> None:
        detail = f"{entity} not found"
        if entity_id:
            detail += f" (id={entity_id})"
        super().__init__(status.HTTP_404_NOT_FOUND, detail=detail)


class ConflictError(DomainError):
    def __init__(self, detail: str) -> None:
        super().__init__(status.HTTP_409_CONFLICT, detail=detail)


class ForbiddenError(DomainError):
    def __init__(self, detail: str = "Forbidden") -> None:
        super().__init__(status.HTTP_403_FORBIDDEN, detail=detail)


class UnauthorizedError(DomainError):
    def __init__(self, detail: str = "Unauthorized") -> None:
        super().__init__(
            status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class ValidationError(DomainError):
    def __init__(self, detail: str) -> None:
        super().__init__(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


class ComplianceBlockError(DomainError):
    def __init__(self, reason: str) -> None:
        super().__init__(
            status.HTTP_412_PRECONDITION_FAILED,
            detail=f"Compliance block: {reason}",
        )


class ServiceUnavailableError(DomainError):
    def __init__(self, detail: str) -> None:
        super().__init__(status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


class BadGatewayError(DomainError):
    """An upstream service responded, but with something we can't use —
    distinct from ServiceUnavailableError (we know it's not configured/down)."""

    def __init__(self, detail: str) -> None:
        super().__init__(status.HTTP_502_BAD_GATEWAY, detail=detail)
