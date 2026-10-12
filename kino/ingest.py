"""Shared message ingestion for the webhook and backfill (CLAUDE.md sections 8.1, 8.2).

`ingest_message` takes a parsed, already-authenticated event and runs everything after the
HMAC/session checks: forward-echo guard, group upsert, watched check, message_id and pHash
dedupe, media decode/fetch, raw write, enqueue. It returns the result code the webhook
replies with, so the webhook behaviour is unchanged and backfill gets identical dedupe.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.concurrency import run_in_threadpool
from loguru import logger
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kino import metrics, tasks
from kino.config import Settings
from kino.db.models import Forward, Group, Image
from kino.db.session import get_session
from kino.openwa.client import OpenWAClient, OpenWAError, mask_path
from kino.openwa.schemas import WebhookEvent, normalize_message_id
from kino.pipeline.prefilter import ImageInfo, NotAnImage, compute_phash, hamming, sniff_image
from kino.slug import slugify

MAX_MEDIA_BYTES = 40 * 1024 * 1024
PHASH_WINDOW = timedelta(days=30)
PHASH_MAX_DISTANCE = 4
UNIFORM_POPCOUNT = (2, 62)


def _fallback_name(chat_id: str) -> str:
    return "Group " + mask_path(chat_id).split("@")[0][-7:]


def rename_group(g: Group, name: str) -> None:
    """Update the display name. The slug is set once, from the first real name, and then
    kept: saved photo paths contain it, so a later rename must not move new photos."""
    g.name = name
    if not g.slug:
        g.slug = slugify(name, "group")


def _upsert_group(s: Session, chat_id: str, name: str | None, now: datetime) -> Group:
    g = s.scalar(select(Group).where(Group.chat_id == chat_id))
    if g is None:
        # Without a known name the slug stays empty until the first real name arrives.
        slug = slugify(name, "group") if name else None
        try:
            with s.begin_nested():
                g = Group(
                    chat_id=chat_id, name=name or _fallback_name(chat_id), slug=slug, watched=False
                )
                s.add(g)
        except IntegrityError:  # lost a race with a concurrent first message
            g = s.scalar(select(Group).where(Group.chat_id == chat_id))
            assert g is not None
    elif name and (g.name != name or not g.slug):
        rename_group(g, name)
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


def _is_forward_echo(message_id: str) -> bool:
    """True when the message is one Kino itself sent as a forward (loop guard).

    Ids are compared normalised on both sides (serialized `true_<chat>_<id>[_<participant>]`
    vs bare id); the SQL filter is only a coarse prefilter.
    """
    want = normalize_message_id(message_id)
    with get_session() as s:
        stored = s.scalars(
            select(Forward.openwa_message_id).where(
                or_(
                    Forward.openwa_message_id == want,
                    Forward.openwa_message_id.contains("_" + want, autoescape=True),
                )
            )
        )
        return any(m is not None and normalize_message_id(m) == want for m in stored)


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


async def ingest_message(
    ev: WebhookEvent,
    settings: Settings,
    client: OpenWAClient | None,
    *,
    get_name: Callable[[], str | None] = lambda: None,
    after_upsert: Callable[[], None] | None = None,
) -> str:
    """Ingest one image message; returns a result code (see webhook `_reply` mapping).

    Codes: ignored, unwatched, requeued, queued, duplicate, duplicate_message, bad_media,
    media_unavailable (503), too_large (413), not_an_image, store_failed (503),
    enqueue_failed. `get_name` runs after the echo check; `after_upsert` after the group row
    exists (the webhook starts its background name refresh there).
    """
    # The user's own (fromMe) photos are processed like anyone else's; only messages Kino
    # itself sent as forwards are dropped, whoever they appear to come from.
    if await run_in_threadpool(_is_forward_echo, ev.message_id):
        return "ignored"
    watched, queued_id = await run_in_threadpool(_handle_group, ev, get_name())
    if after_upsert is not None:
        after_upsert()
    if not watched:
        return "unwatched"
    if queued_id is not None:
        # Redelivery of a message whose earlier enqueue may have failed. Enqueueing twice
        # is safe only because process_image is idempotent (guards on status).
        return await _enqueue(queued_id, "requeued")
    if await run_in_threadpool(_message_known, ev):
        return "duplicate_message"

    data: bytes
    fetched = not ev.media_b64
    if ev.media_b64:
        try:
            data = base64.b64decode("".join(ev.media_b64.split()), validate=True)
        except (binascii.Error, ValueError):
            return "bad_media"
    else:
        if client is None:
            return "media_unavailable"
        try:
            # The id goes to OpenWA exactly as delivered: the live media endpoint needs the
            # serialized `false_<chat>_<id>_<participant>` form (a bare id gives 404).
            data = await client.get_media(ev.chat_id, ev.message_id)
        except OpenWAError as e:
            logger.warning("media fetch failed: {}", e)
            return "media_unavailable"
    if len(data) > MAX_MEDIA_BYTES:
        return "too_large"

    try:
        info = await run_in_threadpool(sniff_image, data)
    except NotAnImage as e:
        if fetched:
            # A missing/failed download (empty body, error page) must not be final: the
            # webhook answers 503 so OpenWA redelivers and the next attempt refetches.
            logger.warning(
                "fetched media for {} is not an image ({}); asking OpenWA to retry",
                mask_path(ev.chat_id),
                e,
            )
            return "media_unavailable"
        logger.info("ignoring non-image media in {}: {}", mask_path(ev.chat_id), e)
        return "not_an_image"

    try:
        outcome = await run_in_threadpool(_save_image, ev, data, info, settings)
    except (OSError, NotAnImage, StoreError) as e:
        logger.error("storing image failed: {}", type(e).__name__)
        return "store_failed"

    if outcome.result == "queued" and outcome.image_id is not None:
        return await _enqueue(outcome.image_id, "queued")
    return outcome.result


async def _enqueue(image_id: int, result: str) -> str:
    """Enqueue process_image; process_image must be idempotent (re-enqueued on redelivery)."""
    try:
        await run_in_threadpool(tasks.process_image, image_id)
    except Exception as e:  # row stays `queued`; redelivery or the sweeper re-enqueues it
        logger.error("enqueue failed for image {}: {}", image_id, type(e).__name__)
        return "enqueue_failed"
    return result
