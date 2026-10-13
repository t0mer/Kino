"""/api/openwa/setup: the OpenWA setup wizard (connect, pick a session, register the webhook).

Secrets are write-only: responses carry ``has_*`` flags and a URL without credentials, never
the API key or the webhook secret. Fields set in the environment or config.yaml are locked (409).
"""

from __future__ import annotations

import secrets
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from loguru import logger

from kino.api.deps import current_openwa, protected_router, reset_openwa
from kino.api.schemas import (
    OpenWAConnectIn,
    OpenWAConnectOut,
    OpenWAOutgoingIn,
    OpenWAOutgoingResult,
    OpenWASessionIn,
    OpenWASessionOut,
    OpenWASessionResult,
    OpenWASetupStatus,
)
from kino.config import Settings
from kino.db.session import get_session
from kino.openwa.client import OpenWAError, list_sessions
from kino.openwa.register import RegisterError, register_webhook
from kino.services import ConflictError, InvalidError, NotFoundError, settings_svc
from kino.services.openwa_cfg import (
    OpenWAConfig,
    OpenWANotConfiguredError,
    clear_openwa_session,
    clear_outgoing_session,
    ensure_unlocked,
    env_overrides,
    get_openwa_config,
    load_openwa_config,
    promote_webhook_secret,
    save_openwa_config,
    save_outgoing_session,
    save_webhook_secret,
    url_changed,
)

router = protected_router(prefix="/api/openwa/setup", tags=["openwa-setup"])


def _env(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def display_url(url: str | None) -> str | None:
    """Scheme, host and path only: a URL may carry ``user:password@`` or a token query."""
    if not url:
        return None
    parts = urlsplit(url)
    host = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _connect_error(e: OpenWAError) -> HTTPException:
    if e.status is None:
        return HTTPException(
            502, "Cannot reach OpenWA at this URL. Check the address and that OpenWA is running."
        )
    if e.status in (401, 403):
        return HTTPException(
            400,
            f"OpenWA refused the request (HTTP {e.status}): check the API key, or whether a "
            "proxy in front of OpenWA blocks Kino.",
        )
    return HTTPException(
        502, f"OpenWA answered HTTP {e.status}. Check that the URL points at the OpenWA API."
    )


def _register_error(e: Exception) -> str:
    if isinstance(e, OpenWAError):
        if e.status is None:
            return "Session saved, but OpenWA could not be reached to register the webhook. Retry."
        return (
            f"Session saved, but OpenWA refused the webhook (HTTP {e.status}). "
            "Retry, or check the OpenWA logs."
        )
    return f"Session saved, but the webhook was not registered: {e}"


@router.get("/status", response_model=OpenWASetupStatus)
def setup_status(request: Request) -> OpenWASetupStatus:
    env = _env(request)
    with get_session() as s:
        cfg = get_openwa_config(s, env)
        webhook_url = settings_svc.default_webhook_url(s, env)
    return OpenWASetupStatus(
        configured=cfg.configured,
        url=display_url(cfg.url),
        session_id=cfg.session_id,
        session_name=cfg.session_name,
        has_api_key=cfg.api_key is not None,
        has_webhook_secret=cfg.webhook_secret is not None,
        webhook_secret_pending=cfg.pending_webhook_secret is not None,
        webhook_url=webhook_url,
        env_overrides=list(env_overrides(env)),
        outgoing_session_id=cfg.outgoing_session_id,
        outgoing_session_name=cfg.outgoing_session_name,
        same_as_incoming=cfg.same_as_incoming,
    )


@router.post("/connect", response_model=OpenWAConnectOut)
async def connect(request: Request, body: OpenWAConnectIn) -> OpenWAConnectOut:
    """Test URL + key against ``GET /api/sessions``; only then store them (and the secret)."""
    env = _env(request)
    ensure_unlocked(
        env,
        [
            name
            for name, value in (
                ("url", body.url),
                ("api_key", body.api_key),
                ("webhook_secret", body.webhook_secret),
            )
            if value is not None
        ],
    )
    cfg = await run_in_threadpool(load_openwa_config, env)
    new_host = url_changed(cfg, body.url)
    if new_host and "api_key" in env_overrides(env):
        # never send a key from the environment to a host the operator did not configure
        raise HTTPException(
            400,
            "The API key is set in the environment or config.yaml (KINO_OPENWA_API_KEY): "
            "the OpenWA URL can only change together with it there.",
        )
    if new_host and not body.api_key:
        raise HTTPException(400, "Enter the API key for the new URL.")
    url = body.url or cfg.url
    # a new host only ever gets the key typed with it, never the saved one
    api_key = body.api_key if new_host else (body.api_key or cfg.api_key)
    if not url:
        raise InvalidError("Enter the OpenWA URL.")
    if not api_key:
        raise InvalidError("Enter the OpenWA API key.")
    try:
        sessions = await list_sessions(url, api_key)
    except OpenWAError as e:
        logger.info("OpenWA connection test failed: {}", e)
        raise _connect_error(e) from None
    generate = body.webhook_secret is None and not cfg.webhook_secret
    secret = body.webhook_secret or (secrets.token_hex(32) if generate else None)

    def save() -> None:
        with get_session() as s:
            save_openwa_config(s, env, url=body.url, api_key=body.api_key)
            if new_host:  # the picked session lives on the old OpenWA
                clear_openwa_session(s, env)
            if secret is not None:  # pending while OpenWA may still sign with the old one
                save_webhook_secret(s, env, secret)

    await run_in_threadpool(save)
    await reset_openwa(request.app)
    return OpenWAConnectOut(
        sessions=[OpenWASessionOut.model_validate(x) for x in sessions],
        generated_webhook_secret=generate,
    )


@router.post("/session", response_model=OpenWASessionResult)
async def pick_session(request: Request, body: OpenWASessionIn) -> OpenWASessionResult:
    """Save the session (validated against the live list) and register Kino's webhook."""
    env = _env(request)
    ensure_unlocked(env, ["session_id"])
    cfg = await run_in_threadpool(load_openwa_config, env)
    url, api_key = cfg.url, cfg.api_key
    if not url or not api_key:
        raise ConflictError("Connect to OpenWA first (URL and API key).")
    try:
        sessions = await list_sessions(url, api_key)
    except OpenWAError as e:
        raise _connect_error(e) from None
    picked = next((x for x in sessions if x["id"] == body.session_id), None)
    if picked is None:
        raise NotFoundError(
            "That session no longer exists in OpenWA. Reload the list and pick again."
        )
    name = str(picked["name"]) if picked["name"] else ""

    def save() -> tuple[str, str | None]:
        with get_session() as s:
            previous = settings_svc.stored_webhook_url(s)
            save_openwa_config(s, env, session_id=body.session_id, session_name=name)
            return body.webhook_url or settings_svc.default_webhook_url(s, env), previous

    hook_url, previous = await run_in_threadpool(save)
    await reset_openwa(request.app)
    try:
        secret = (await run_in_threadpool(load_openwa_config, env)).registration_secret()
        client = await run_in_threadpool(current_openwa, request.app)
        if client is None:
            raise OpenWANotConfiguredError("OpenWA is not configured")
        action, hook_id = await register_webhook(client, hook_url, secret, previous_url=previous)
    except (OpenWAError, RegisterError, OpenWANotConfiguredError) as e:
        logger.warning("webhook registration after picking the session failed: {}", e)
        return OpenWASessionResult(
            session_id=body.session_id,
            registered=False,
            webhook_url=hook_url,
            error=_register_error(e),
        )

    def remember() -> None:
        with get_session() as s:
            settings_svc.remember_webhook_url(s, hook_url)
            promote_webhook_secret(s, env, secret)

    await run_in_threadpool(remember)
    return OpenWASessionResult(
        session_id=body.session_id,
        registered=True,
        action=action,
        webhook_id=hook_id,
        webhook_url=hook_url,
    )


@router.get("/sessions", response_model=list[OpenWASessionOut])
async def sessions(request: Request) -> list[OpenWASessionOut]:
    """The live OpenWA session list (to pick the outgoing session), via the saved URL and key."""
    cfg = await run_in_threadpool(load_openwa_config, _env(request))
    if not cfg.url or not cfg.api_key:
        raise ConflictError("Connect to OpenWA first (URL and API key).")
    try:
        found = await list_sessions(cfg.url, cfg.api_key)
    except OpenWAError as e:
        raise _connect_error(e) from None
    return [OpenWASessionOut.model_validate(x) for x in found]


@router.put("/outgoing-session", response_model=OpenWAOutgoingResult)
async def set_outgoing_session(request: Request, body: OpenWAOutgoingIn) -> OpenWAOutgoingResult:
    """Send forwards, the test photo and the digest with another session (null = the incoming
    one). Registers no webhook and leaves the incoming session alone."""
    env = _env(request)
    name: str | None = None
    if body.session_id is not None:
        cfg = await run_in_threadpool(load_openwa_config, env)
        if not cfg.url or not cfg.api_key:
            raise ConflictError("Connect to OpenWA first (URL and API key).")
        try:
            found = await list_sessions(cfg.url, cfg.api_key)
        except OpenWAError as e:
            raise _connect_error(e) from None
        picked = next((x for x in found if x["id"] == body.session_id), None)
        if picked is None:
            raise NotFoundError(
                "That session no longer exists in OpenWA. Reload the list and pick again."
            )
        name = str(picked["name"]) if picked["name"] else ""
    session_id = body.session_id

    def save() -> OpenWAConfig:
        with get_session() as s:
            if session_id is None:
                clear_outgoing_session(s)
            else:
                save_outgoing_session(s, session_id, name)
            s.flush()
            return get_openwa_config(s, env)

    cfg = await run_in_threadpool(save)
    await reset_openwa(request.app)
    return OpenWAOutgoingResult(
        outgoing_session_id=cfg.outgoing_session_id,
        outgoing_session_name=cfg.outgoing_session_name,
        same_as_incoming=cfg.same_as_incoming,
    )
