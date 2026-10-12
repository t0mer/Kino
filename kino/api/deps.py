"""FastAPI dependencies for authentication and CSRF.

Routers protect routes with ``Depends(require_auth)`` and, for state-changing methods,
``Depends(require_csrf)``.
"""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from loguru import logger

from kino.api import auth
from kino.api.auth import AuthInfo
from kino.api.errors import OpenWANotConfigured
from kino.config import Settings
from kino.openwa.client import OpenWAClient
from kino.services.openwa_cfg import NOT_CONFIGURED_HINT, OpenWAConfig, load_openwa_config

__all__ = [
    "AuthInfo",
    "current_openwa",
    "get_auth",
    "openwa_client",
    "protected_router",
    "reset_openwa",
    "require_auth",
    "require_csrf",
]

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_auth(request: Request) -> AuthInfo | None:
    """The authenticated identity, or None without credentials.

    A present but invalid Cloudflare Access JWT raises 401 immediately: it never falls back
    to the local session cookie.
    """
    settings = _settings(request)
    token = request.headers.get(auth.CF_JWT_HEADER)
    if token is not None:
        try:
            info = auth.verify_cf_jwt(settings, token)
        except auth.AuthError as e:
            auth.record("cloudflare", "invalid")
            logger.debug("Cloudflare Access token rejected: {}", e)
            raise HTTPException(401, "invalid Cloudflare Access token") from None
        auth.record("cloudflare", "ok")
        return info
    cookie = request.cookies.get(auth.SESSION_COOKIE)
    if not cookie:
        return None
    return auth.read_session(request.app.state.secret_key, cookie, settings)


def require_auth(request: Request) -> AuthInfo:
    info = get_auth(request)
    if info is None:
        raise HTTPException(401, "not authenticated")
    return info


def require_csrf(request: Request) -> None:
    """Double-submit check for state-changing requests (cookie ``kino_csrf`` == header)."""
    if request.method in SAFE_METHODS:
        return
    if not auth.csrf_ok(
        request.cookies.get(auth.CSRF_COOKIE), request.headers.get(auth.CSRF_HEADER)
    ):
        raise HTTPException(403, "missing or invalid CSRF token")


def is_https(request: Request) -> bool:
    return (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").lower() == "https"
    )


def ensure_csrf_cookie(request: Request, response: Response) -> None:
    """Issue the CSRF cookie if the browser has none (readable by the SPA, not HttpOnly)."""
    if request.cookies.get(auth.CSRF_COOKIE):
        return
    response.set_cookie(
        auth.CSRF_COOKIE,
        auth.new_csrf_token(),
        max_age=auth.SESSION_MAX_AGE,
        secure=is_https(request),
        httponly=False,
        samesite="lax",
        path="/",
    )


def protected_router(**kwargs: Any) -> APIRouter:
    """An APIRouter whose every route requires authentication and, for state-changing
    methods, the CSRF token. Use it for every router outside ``/api/auth`` and the webhook."""
    deps = [Depends(require_auth), Depends(require_csrf), *kwargs.pop("dependencies", [])]
    return APIRouter(dependencies=deps, **kwargs)


_client_lock = threading.Lock()


def current_openwa(app: FastAPI, cfg: OpenWAConfig | None = None) -> OpenWAClient | None:
    """The shared OpenWA client, built from the resolved config on first use and cached on
    ``app.state.openwa`` until :func:`reset_openwa` (the setup wizard) drops it.

    Blocking (reads the database): call it from a threadpool in async code."""
    cached: OpenWAClient | None = app.state.openwa
    if cached is not None:
        return cached
    settings: Settings = app.state.settings
    resolved = cfg if cfg is not None else load_openwa_config(settings)
    if not resolved.client_ready:
        return None
    with _client_lock:
        cached = app.state.openwa
        if cached is None:
            cached = OpenWAClient(*resolved.client_args())
            app.state.openwa = cached
        return cached


async def reset_openwa(app: FastAPI) -> None:
    """Forget the cached client so the next call rebuilds it from the saved config."""
    with _client_lock:
        old: OpenWAClient | None = app.state.openwa
        app.state.openwa = None
    if old is not None:
        await old.aclose()


def openwa_client(request: Request) -> OpenWAClient:
    """The app's shared OpenWA client; 503 when OpenWA is not configured."""
    client = current_openwa(request.app)
    if client is None:
        raise OpenWANotConfigured(f"OpenWA is not configured: {NOT_CONFIGURED_HINT}")
    return client
