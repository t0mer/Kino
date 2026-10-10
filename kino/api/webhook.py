"""POST /api/webhook/openwa (CLAUDE.md section 8.1)."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kino import metrics, tasks
from kino.config import Settings
from kino.db.models import Group, Image
from kino.db.session import get_session
from kino.openwa.client import OpenWAError, mask_path
from kino.openwa.schemas import (
    EVENT_MESSAGE_RECEIVED,
    SIGNATURE_HEADER,
    WebhookEvent,
    parse_webhook,
    verify_signature,
)
from kino.pipeline.prefilter import ImageInfo, NotAnImage, compute_phash, hamming, sniff_image

router = APIRouter()

MAX_BODY_BYTES = 40 * 1024 * 1024
PHASH_WINDOW = timedelta(days=30)
PHASH_MAX_DISTANCE = 4
GROUP_NAME_TTL = 24 * 3600.0
GROUP_NAME_FAILURE_TTL = 300.0
NAME_FETCH_TIMEOUT = 2.0
UNIFORM_POPCOUNT = (2, 62)

# chat_id -> (expires_monotonic, name or None when the lookup failed)
_name_cache: dict[str, tuple[float, str | None]] = {}


def _reply(result: str, status: int = 200) -> Response:
    metrics.webhook_requests.labels(result=result).inc()
    return Response(
        content=json.dumps({"result": result}), status_code=status, media_type="application/json"
    )


def _slugify(name: str, chat_id: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "group-" + re.sub(r"\D", "", chat_id)[-6:]


def _fallback_name(chat_id: str) -> str:
    return "Group " + mask_path(chat_id).split("@")[0][-7:]


_refresh_tasks: set[asyncio.Task[None]] = set()


def _update_group_name(chat_id: str, name: str) -> None:
    with get_session() as s:
        g = s.scalar(select(Group).where(Group.chat_id == chat_id))
        if g is not None and g.name != name:
            g.name = name
            g.slug = _slugify(name, chat_id)


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


def _upsert_group(s: Session, chat_id: str, name: str | None, now: datetime) -> Group:
    g = s.scalar(select(Group).where(Group.chat_id == chat_id))
    if g is None:
        shown = name or _fallback_name(chat_id)
        try:
            with s.begin_nested():
                g = Group(chat_id=chat_id, name=shown, slug=_slugify(shown, chat_id), watched=False)
                s.add(g)
        except IntegrityError:  # lost a race with a concurrent first message
            g = s.scalar(select(Group).where(Group.chat_id == chat_id))
            assert g is not None
    elif name and g.name != name:
        g.name = name
        g.slug = _slugify(name, chat_id)
    g.last_seen_at = now
    return g


def _find_duplicate(s: Session, phash: str, now: datetime) -> Image | None:
    rows = s.scalars(
        select(Image).where(Image.phash.is_not(None), Image.received_at >= now - PHASH_WINDOW)
    )
    for row in rows:
        if row.phash and hamming(row.phash, phash) <= PHASH_MAX_DISTANCE:
            if row.duplicate_of is not None:
                return s.get(Image, row.duplicate_of) or row
            return row
    return None


class StoreError(Exception):
    """Unexpected DB failure while storing an image."""


@dataclass
class _Outcome:
    result: str
    image_id: int | None = None


def _handle_group(ev: WebhookEvent, name: str | None) -> tuple[bool, int | None]:
    """Upsert the group; return (watched, id of a still-`queued` image for this message)."""
    now = datetime.now(UTC).replace(tzinfo=None)
    with get_session() as s:
        g = _upsert_group(s, ev.chat_id, name, now)
        watched = g.watched
        queued_id = None
        if watched:
            queued_id = s.scalar(
                select(Image.id).where(Image.message_id == ev.message_id, Image.status == "queued")
            )
    return watched, queued_id


def _message_known(ev: WebhookEvent) -> bool:
    with get_session() as s:
        return s.scalar(select(Image.id).where(Image.message_id == ev.message_id)) is not None


def _save_image(ev: WebhookEvent, data: bytes, info: ImageInfo, settings: Settings) -> _Outcome:
    now = datetime.now(UTC).replace(tzinfo=None)
    phash = compute_phash(data)
    raw_file: Path | None = None
    try:
        with get_session() as s:
            g = s.scalar(select(Group).where(Group.chat_id == ev.chat_id))
            assert g is not None
            uniform = not UNIFORM_POPCOUNT[0] < int(phash, 16).bit_count() < UNIFORM_POPCOUNT[1]
            dup = None if uniform else _find_duplicate(s, phash, now)
            img = Image(
                message_id=ev.message_id,
                group_id=g.id,
                sender_id=ev.sender_id,
                sender_name=ev.sender_name,
                sent_at=ev.timestamp.astimezone(UTC).replace(tzinfo=None),
                received_at=now,
                width=info.width,
                height=info.height,
                phash=phash,
                status="skipped" if dup else "queued",
            )
            if dup:
                img.duplicate_of = dup.id
                img.skip_reason = "duplicate"
            s.add(img)
            try:
                s.flush()
            except IntegrityError:
                s.rollback()
                if s.scalar(select(Image.id).where(Image.message_id == ev.message_id)) is not None:
                    return _Outcome("duplicate_message")
                logger.error("image insert failed with an unexpected integrity error")
                raise StoreError("integrity error") from None
            if dup:
                metrics.duplicates.inc()
                metrics.images.labels(status="skipped").inc()
                orig_group = s.get(Group, dup.group_id) if dup.group_id else None
                logger.info(
                    "duplicate of image {} (orig group {!r}, now {!r})",
                    dup.id,
                    orig_group.name if orig_group else None,
                    g.name,
                )
                return _Outcome("duplicate", img.id)
            raw_file = (
                settings.data_dir / "raw" / f"{now:%Y}" / f"{now:%m}" / f"{img.id}.{info.ext}"
            )
            raw_file.parent.mkdir(parents=True, exist_ok=True)
            raw_file.write_bytes(data)
            img.raw_path = str(raw_file)
            image_id = img.id
        metrics.images.labels(status="queued").inc()
        return _Outcome("queued", image_id)
    except BaseException:
        if raw_file is not None:
            raw_file.unlink(missing_ok=True)
        raise


@router.post("/api/webhook/openwa")
async def openwa_webhook(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return _reply("too_large", 413)

    secret = settings.require_webhook_secret()
    if not verify_signature(secret, body, request.headers.get(SIGNATURE_HEADER)):
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

    if not settings.openwa_session_id or ev.session_id != settings.openwa_session_id:
        return _reply("bad_session", 403)
    if ev.media_type != "image" or not ev.is_group or ev.from_me:
        return _reply("ignored")

    name, refresh = _cached_group_name(request, ev.chat_id)
    watched, queued_id = await run_in_threadpool(_handle_group, ev, name)
    if refresh:  # after the upsert, so the refresh finds the group row
        _start_refresh(request, ev.chat_id)
    if not watched:
        return _reply("unwatched")
    if queued_id is not None:
        # Redelivery of a message whose earlier enqueue may have failed. Enqueueing twice
        # is safe only because process_image is idempotent (Task 10 guards on status).
        return await _enqueue(queued_id, "requeued")
    if await run_in_threadpool(_message_known, ev):
        return _reply("duplicate_message")

    data: bytes
    if ev.media_b64:
        try:
            data = base64.b64decode("".join(ev.media_b64.split()), validate=True)
        except (binascii.Error, ValueError):
            return _reply("bad_media")
    else:
        client = request.app.state.openwa
        if client is None:
            return _reply("media_unavailable", 503)
        try:
            data = await client.get_media(ev.chat_id, ev.message_id)
        except OpenWAError as e:
            logger.warning("media fetch failed: {}", e)
            return _reply("media_unavailable", 503)
    if len(data) > MAX_BODY_BYTES:
        return _reply("too_large", 413)

    try:
        info = await run_in_threadpool(sniff_image, data)
    except NotAnImage as e:
        logger.info("ignoring non-image media in {}: {}", mask_path(ev.chat_id), e)
        return _reply("not_an_image")

    try:
        outcome = await run_in_threadpool(_save_image, ev, data, info, settings)
    except (OSError, NotAnImage, StoreError) as e:
        logger.error("storing image failed: {}", type(e).__name__)
        return _reply("store_failed", 503)

    if outcome.result == "queued" and outcome.image_id is not None:
        return await _enqueue(outcome.image_id, "queued")
    return _reply(outcome.result)


async def _enqueue(image_id: int, result: str) -> Response:
    """Enqueue process_image; process_image must be idempotent (re-enqueued on redelivery)."""
    try:
        await run_in_threadpool(tasks.process_image, image_id)
    except Exception as e:  # row stays `queued`; redelivery or the sweeper re-enqueues it
        logger.error("enqueue failed for image {}: {}", image_id, type(e).__name__)
        return _reply("enqueue_failed")
    return _reply(result)
