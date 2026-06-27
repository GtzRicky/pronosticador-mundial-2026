from __future__ import annotations


class DomainError(Exception):
    """Base error for domain-level failures."""


class DomainValidationError(DomainError, ValueError):
    """Raised when a value object receives invalid data."""


class InvalidIdentifierError(DomainValidationError):
    """Raised when a domain identifier is empty or missing."""
