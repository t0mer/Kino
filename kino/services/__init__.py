"""Business logic shared by the CLI and the REST API.

Services take a SQLAlchemy ``Session`` (the caller owns the transaction) and raise
:class:`ServiceError` subclasses whose message is safe to show to the user.
"""

from __future__ import annotations


class ServiceError(RuntimeError):
    """A lookup or edit that cannot be satisfied; the message is shown to the user."""


class NotFoundError(ServiceError):
    """The referenced kid, group, forward, ... does not exist."""


class ConflictError(ServiceError):
    """The request clashes with existing state (duplicate name, slug, ...)."""


class InvalidError(ServiceError):
    """An input value is out of range or malformed."""


class GoneError(ServiceError):
    """The referenced item existed but has expired (e.g. an enrollment upload past its TTL)."""


__all__ = ["ConflictError", "GoneError", "InvalidError", "NotFoundError", "ServiceError"]
