"""/api/forwards and /api/forward/test (CLAUDE.md §9.9, §12)."""

from __future__ import annotations

import math
import time
from typing import Annotated

from fastapi import HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from loguru import logger
from sqlalchemy import select

from kino.api.deps import current_openwa_out, protected_router
from kino.api.errors import OpenWANotConfigured
from kino.api.routes.telegram import make_channel, raise_delivery
from kino.api.schemas import ForwardOut, ForwardTestIn, ForwardTestOut
from kino.config import Settings
from kino.db.models import Forward, TelegramChat
from kino.db.session import get_session
from kino.delivery.base import DeliveryError
from kino.forward import validate_destination
from kino.openwa.client import OpenWAClient, mask_path
from kino.services import InvalidError
from kino.services import forwards as svc
from kino.services.images import mask_ids, sanitize_error
from kino.services.openwa_cfg import NOT_CONFIGURED_HINT
from kino.services.telegram_cfg import load_telegram_config

router = protected_router(tags=["forwards"])

TEST_COOLDOWN = 10.0  # seconds between test sends (WhatsApp ban-risk guard)
_clock = time.monotonic


def _out(f: Forward) -> ForwardOut:
    return ForwardOut(
        id=f.id,
        image_id=f.image_id,
        channel=f.channel,
        # phone numbers are masked; Telegram chat ids are not personal data and must stay usable
        dest_chat_id=(f.dest_chat_id if f.channel == "telegram" else mask_ids(f.dest_chat_id))
        or "",
        kid_ids=list(f.kid_ids or []),
        caption=mask_ids(f.caption),
        status=f.status,
        attempts=f.attempts,
        last_error=sanitize_error(f.last_error),
        created_at=f.created_at,
        sent_at=f.sent_at,
        next_attempt_at=f.next_attempt_at,
    )


@router.get("/api/forwards", response_model=list[ForwardOut])
def list_forwards(
    status: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[ForwardOut]:
    with get_session() as s:
        return [_out(f) for f in svc.list_forwards(s, status, limit)]


@router.post("/api/forwards/{forward_id}/retry", response_model=ForwardOut)
def retry_forward(forward_id: int) -> ForwardOut:
    with get_session() as s:
        out = _out(svc.reset_failed(s, forward_id))
    svc.enqueue(forward_id)  # after commit, so the sender sees the pending row
    return out


def _check_destination(chat_id: str, channel: str) -> None:
    with get_session() as s:
        validate_destination(chat_id, s, channel)
        if channel == "telegram":
            linked = s.scalar(select(TelegramChat.id).where(TelegramChat.chat_id == chat_id))
            if linked is None:
                raise InvalidError("Telegram chat is not linked: link it in Settings first")


async def _send_telegram_test(request: Request, chat_id: str) -> None:
    env: Settings = request.app.state.settings
    cfg = await run_in_threadpool(load_telegram_config, env)
    channel = make_channel(request.app, cfg, send_as_file=cfg.send_as_file)
    failure: DeliveryError | None = None
    try:
        await channel.send_photo(chat_id, svc.make_test_photo(), "image/jpeg", svc.TEST_CAPTION)
    except DeliveryError as e:
        failure = e
    finally:
        await channel.aclose()
    if failure is not None:
        raise_delivery(failure, cfg.bot_token)


@router.post("/api/forward/test", response_model=ForwardTestOut)
async def send_test(request: Request, body: ForwardTestIn) -> ForwardTestOut:
    """Send a generated test photo to a candidate destination (watched groups refused).

    At most one send per TEST_COOLDOWN seconds per process; the slot is taken only once the
    destination passed validation, and checked and taken with no await in between."""
    client: OpenWAClient | None = None
    if body.channel == "whatsapp":
        client = await run_in_threadpool(current_openwa_out, request.app)
        if client is None:
            raise OpenWANotConfigured(f"OpenWA is not configured: {NOT_CONFIGURED_HINT}")
    await run_in_threadpool(_check_destination, body.chat_id, body.channel)
    now = _clock()
    last: float | None = getattr(request.app.state, "forward_test_at", None)
    if last is not None and now - last < TEST_COOLDOWN:
        wait = math.ceil(TEST_COOLDOWN - (now - last))
        raise HTTPException(429, f"please wait {wait} s before sending another test photo")
    request.app.state.forward_test_at = now
    if client is not None:
        await svc.send_test(client, body.chat_id)
        logger.info("sent a test photo to {}", mask_path(body.chat_id))
    else:
        await _send_telegram_test(request, body.chat_id)
        logger.info("sent a test photo to Telegram chat {}", body.chat_id)
    return ForwardTestOut(sent=True)
