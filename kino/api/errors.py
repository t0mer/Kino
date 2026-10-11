"""Map service-layer exceptions to HTTP responses with sanitized, user-facing messages."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger

from kino.forward import ForwardError
from kino.openwa.client import OpenWAError
from kino.openwa.register import RegisterError
from kino.services import (
    ConflictError,
    GoneError,
    InvalidError,
    NotFoundError,
    ServiceError,
)


class OpenWANotConfigured(Exception):
    """OpenWA URL, API key or session id is missing."""


_STATUS: list[tuple[type[Exception], int]] = [
    (NotFoundError, 404),
    (ConflictError, 409),
    (InvalidError, 422),
    (GoneError, 410),
    (ServiceError, 400),
    (ForwardError, 409),  # destination guard (watched group <-> forward destination)
    (OpenWANotConfigured, 503),
    (RegisterError, 503),  # server-side configuration (webhook secret too short)
]


def _detail(request: Request, exc: Exception) -> JSONResponse:
    for cls, status in _STATUS:
        if isinstance(exc, cls):
            return JSONResponse({"detail": str(exc)}, status_code=status)
    raise exc  # pragma: no cover - only registered classes reach here


def _openwa(request: Request, exc: Exception) -> JSONResponse:
    # OpenWAError carries only a status code and a masked path; still, keep the reply generic.
    status = exc.status if isinstance(exc, OpenWAError) else None
    logger.warning("OpenWA request failed: {}", exc)
    detail = "OpenWA is unreachable" if status is None else f"OpenWA returned HTTP {status}"
    return JSONResponse({"detail": detail}, status_code=502)


def install_error_handlers(app: FastAPI) -> None:
    for cls, _ in _STATUS:
        app.add_exception_handler(cls, _detail)
    app.add_exception_handler(OpenWAError, _openwa)
