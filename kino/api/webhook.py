"""POST /api/webhook/openwa (CLAUDE.md section 8.1)."""

from __future__ import annotations

import asyncio
import json
import time
from functools import partial
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from loguru import logger
from sqlalchemy import select

from kino import metrics
from kino.api.deps import current_openwa, openwa_generation
from kino.config import Settings
from kino.db.models import Group
from kino.db.session import get_session
from kino.ingest import ingest_message, rename_group
from kino.openwa.client import OpenWAError
from kino.openwa.schemas import (
    EVENT_MESSAGE_RECEIVED,
    SIGNATURE_HEADER,
    parse_webhook,
    verify_signature,
)
from kino.services.openwa_cfg import load_openwa_config

router = APIRouter()

MAX_BODY_BYTES = 40 * 1024 * 1024
GROUP_NAME_TTL = 24 * 3600.0
GROUP_NAME_FAILURE_TTL = 300.0
NAME_FETCH_TIMEOUT = 2.0

# chat_id -> (expires_monotonic, name or None when the lookup failed)
_name_cache: dict[str, tuple[float, str | None]] = {}


def _reply(result: str, status: int = 200) -> Response:
    metrics.webhook_requests.labels(result=result).inc()
    return Response(
        content=json.dumps({"result": result}), status_code=status, media_type="application/json"
    )


_refresh_tasks: set[asyncio.Task[None]] = set()


def _update_group_name(chat_id: str, name: str) -> None:
    with get_session() as s:
        g = s.scalar(select(Group).where(Group.chat_id == chat_id))
        if g is not None and (g.name != name or not g.slug):
            rename_group(g, name)


async def _refresh_group_name(client: Any, chat_id: str) -> None:
    """Fetch the name off the request path (short timeout) and update cache + DB."""
    ttl = GROUP_NAME_FAILURE_TTL
    name: str | None = None
    try:
        name = await asyncio.wait_for(client.get_group_name(chat_id), NAME_FETCH_TIMEOUT)
        if name:
            ttl = GROUP_NAME_TTL
            await run_in_threadpool(_update_group_name, chat_id, name)
    except (OpenWAError, TimeoutError) as e:
        logger.warning("group name lookup failed: {}", type(e).__name__)
    except Exception as e:  # never let a background refresh crash noisily
        logger.error("group name refresh error: {}", type(e).__name__)
    _name_cache[chat_id] = (time.monotonic() + ttl, name)


def _cached_group_name(request: Request, chat_id: str) -> tuple[str | None, bool]:
    """Return (cached name or None, whether a background refresh should be started)."""
    now = time.monotonic()
    hit = _name_cache.get(chat_id)
    if hit and hit[0] > now:
        return hit[1], False
    previous = hit[1] if hit else None
    # placeholder entry so concurrent requests do not each start a refresh
    _name_cache[chat_id] = (now + GROUP_NAME_FAILURE_TTL, previous)
    return previous, request.app.state.openwa is not None


def _start_refresh(request: Request, chat_id: str) -> None:
    task = asyncio.create_task(_refresh_group_name(request.app.state.openwa, chat_id))
    _refresh_tasks.add(task)
    task.add_done_callback(_refresh_tasks.discard)


class _TooLarge(Exception):
    """Request body exceeds MAX_BODY_BYTES."""


async def _read_body(request: Request) -> bytes:
    """Read the raw body with a hard cap: a declared Content-Length over the limit is
    rejected before anything is read, and a streamed body stops at the first chunk past it."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.strip().isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _TooLarge
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > MAX_BODY_BYTES:
            raise _TooLarge
    return bytes(buf)


@router.post("/api/webhook/openwa")
async def openwa_webhook(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    try:
        body = await _read_body(request)
    except _TooLarge:
        return _reply("too_large", 413)

    generation = openwa_generation(request.app)  # before the config: see current_openwa
    cfg = await run_in_threadpool(load_openwa_config, settings)
    if not cfg.webhook_secret:
        return _reply("not_configured", 503)
    if not verify_signature(cfg.webhook_secret, body, request.headers.get(SIGNATURE_HEADER)):
        return _reply("bad_signature", 401)

    try:
        payload: Any = json.loads(body)
    except ValueError:
        return _reply("malformed", 400)
    if not isinstance(payload, dict):
        return _reply("malformed", 400)
    if payload.get("event") != EVENT_MESSAGE_RECEIVED:
        return _reply("ignored")
    try:
        ev = parse_webhook(payload)
    except (ValueError, TypeError):
        return _reply("malformed", 400)

    if not cfg.session_id or ev.session_id != cfg.session_id:
        return _reply("bad_session", 403)
    if ev.media_type != "image" or not ev.is_group:
        return _reply("ignored")
    refresh = False

    def get_name() -> str | None:
        nonlocal refresh
        name, refresh = _cached_group_name(request, ev.chat_id)
        return name

    def after_upsert() -> None:
        if refresh:  # after the upsert, so the refresh finds the group row
            _start_refresh(request, ev.chat_id)

    client = await run_in_threadpool(
        partial(current_openwa, request.app, cfg, generation=generation)
    )
    result = await ingest_message(
        ev,
        settings,
        client,
        get_name=get_name,
        after_upsert=after_upsert,
    )
    return _reply(result, _STATUS.get(result, 200))


_STATUS = {"too_large": 413, "media_unavailable": 503, "store_failed": 503}
