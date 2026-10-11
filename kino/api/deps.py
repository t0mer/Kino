"""FastAPI dependencies for authentication and CSRF.

Routers protect routes with ``Depends(require_auth)`` and, for state-changing methods,
``Depends(require_csrf)``.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, Response

from kino.api import auth
from kino.api.auth import AuthInfo
from kino.config import Settings

__all__ = ["AuthInfo", "get_auth", "require_auth", "require_csrf"]

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
            raise HTTPException(401, f"Cloudflare Access: {e}") from None
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
