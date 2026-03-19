from __future__ import annotations


class DomainError(RuntimeError):
    """Base class for product-domain errors."""


class ValidationError(DomainError):
    pass


class ConflictError(DomainError):
    pass


class NotFoundError(DomainError):
    pass


class AccessDeniedError(DomainError):
    pass
