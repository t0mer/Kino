"""/api/auth: login, logout, current identity, first-run setup (CLAUDE.md §13.1, ruling R2)."""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from loguru import logger
from pydantic import BaseModel, Field

from kino.api import auth
from kino.api.deps import AuthInfo, ensure_csrf_cookie, is_https, require_auth, require_csrf
from kino.config import Settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

MAX_FIELD = 1024  # bounds the argon2 input


class LoginBody(BaseModel):
    username: str = Field(max_length=MAX_FIELD)
    password: str = Field(max_length=MAX_FIELD)


class SetupBody(BaseModel):
    password: str = Field(min_length=auth.MIN_PASSWORD_LENGTH, max_length=MAX_FIELD)


class StatusOut(BaseModel):
    setup_required: bool


class MeOut(BaseModel):
    method: str
    user: str


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _client_ip(request: Request) -> str:
    # The direct peer only: forwarding headers can be set by anyone on the LAN.
    return request.client.host if request.client else "unknown"


def _start_session(request: Request, response: Response, user: str, stored_hash: str) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE,
        auth.make_session(request.app.state.secret_key, user, stored_hash),
        max_age=auth.SESSION_MAX_AGE,
        secure=is_https(request),
        httponly=True,
        samesite="lax",
        path="/",
    )
    ensure_csrf_cookie(request, response)


@router.get("/status", response_model=StatusOut)
def status(request: Request, response: Response) -> Any:
    ensure_csrf_cookie(request, response)
    return {"setup_required": auth.setup_required(_settings(request))}


@router.get("/me", response_model=MeOut)
def me(
    request: Request, response: Response, info: Annotated[AuthInfo, Depends(require_auth)]
) -> Any:
    ensure_csrf_cookie(request, response)
    return {"method": info.method, "user": info.user}


@router.post("/login", response_model=MeOut, dependencies=[Depends(require_csrf)])
def login(body: LoginBody, request: Request, response: Response) -> Any:
    settings = _settings(request)
    ip = _client_ip(request)
    wait = auth.limiter.retry_after(ip)
    if wait:
        auth.record("local", "locked")
        raise HTTPException(
            429, "too many failed logins; try again later", headers={"Retry-After": str(wait)}
        )
    stored = auth.password_hash(settings)
    if stored is None:
        raise HTTPException(409, "no admin password set yet; complete the first-run setup")
    user = auth.admin_user(settings)
    password_ok = auth.verify_password(stored, body.password)  # always run: even timing
    if not (password_ok and hmac.compare_digest(body.username.encode(), user.encode())):
        auth.limiter.failure(ip)
        auth.record("local", "invalid")
        logger.info("failed login from {}", ip)
        raise HTTPException(401, "invalid username or password")
    auth.limiter.success(ip)
    auth.record("local", "ok")
    _start_session(request, response, user, stored)
    return {"method": "local", "user": user}


@router.post("/logout", status_code=204, dependencies=[Depends(require_csrf)])
def logout(request: Request) -> Response:
    response = Response(status_code=204)
    response.delete_cookie(
        auth.SESSION_COOKIE, path="/", secure=is_https(request), httponly=True, samesite="lax"
    )
    return response


@router.post("/setup", response_model=MeOut, dependencies=[Depends(require_csrf)])
def setup(body: SetupBody, request: Request, response: Response) -> Any:
    """One-time admin password for a fresh install, allowed only directly on the LAN."""
    settings = _settings(request)
    if any(h in request.headers for h in auth.PROXY_HEADERS):
        raise HTTPException(403, "first-run setup is only allowed from the local network")
    if not auth.setup_required(settings) or not auth.store_first_password(body.password):
        raise HTTPException(409, "the admin password is already set")
    stored = auth.password_hash(settings)
    assert stored is not None
    user = auth.admin_user(settings)
    auth.record("local", "setup")
    logger.info("admin password set through first-run setup from {}", _client_ip(request))
    _start_session(request, response, user, stored)
    return {"method": "local", "user": user}
