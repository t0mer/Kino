"""Best-effort deletion of already-sent Telegram messages when a photo is removed from a kid.

Only Telegram messages are ever deleted (WhatsApp messages stay), only when the operator
enabled ``telegram.delete_on_remove``, and only for forwards that named no kid but the
removed one. Collection happens inside the removal transaction; the network calls run after
it commits and never fail the removal.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from kino.config import get_settings
from kino.db.models import Forward
from kino.delivery.base import DeliveryError, SentRef

TELEGRAM = "telegram"
PACE_SECONDS = 0.05  # gap between deleteMessage calls
MAX_RETRY_SLEEP = 30.0  # cap on honouring Telegram's retry_after
MAX_DELETIONS = 200  # per request; the rest is logged and left


@dataclass(frozen=True)
class SentDeletion:
    dest: str
    ref: str


def collect_sent_deletions(
    session: Session, kid_id: int, image_id: int | None = None
) -> list[SentDeletion]:
    """Sent Telegram forwards (of one image, or all) that only ever concerned ``kid_id``."""
    from kino.services.telegram_cfg import get_telegram_config

    q = select(Forward).where(
        Forward.status == "sent", Forward.channel == TELEGRAM, Forward.sent_ref.is_not(None)
    )
    if image_id is not None:
        q = q.where(Forward.image_id == image_id)
    rows = [  # kid_ids is JSON, so the subset check stays in Python
        f for f in session.scalars(q) if kid_id in (f.kid_ids or []) and set(f.kid_ids) <= {kid_id}
    ]
    if not rows or not get_telegram_config(session, get_settings()).delete_on_remove:
        return []
    return [SentDeletion(f.dest_chat_id, f.sent_ref) for f in rows if f.sent_ref]


def _default_channel() -> Any:
    from kino.delivery import get_channel

    return get_channel(TELEGRAM)


def delete_sent_messages(
    items: list[SentDeletion],
    channel_factory: Callable[[], Any] | None = None,
    *,
    pace: float = PACE_SECONDS,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_items: int = MAX_DELETIONS,
) -> int:
    """Delete each message, paced; a flood-wait (`retry_after`) is honoured once per item.

    Any failure is logged (redacted) and skipped. At most ``max_items`` are attempted.
    Returns the deleted count.
    """
    if not items:
        return 0
    if len(items) > max_items:
        logger.warning(
            "not deleting {} sent Telegram messages: over the {} per request cap",
            len(items) - max_items,
            max_items,
        )
        items = items[:max_items]
    from kino.forward import _error_text

    async def run() -> int:
        done = 0
        channel = (channel_factory or _default_channel)()
        try:
            for n, item in enumerate(items):
                if n and pace > 0:
                    await sleeper(pace)
                try:
                    try:
                        await channel.delete(item.dest, SentRef(item.ref))
                    except DeliveryError as e:
                        if e.retry_after is None:
                            raise
                        await sleeper(min(max(e.retry_after, 0.0), MAX_RETRY_SLEEP))
                        await channel.delete(item.dest, SentRef(item.ref))
                    done += 1
                except Exception as e:
                    logger.warning(
                        "could not delete a sent Telegram message: {}",
                        _error_text(e, TELEGRAM)[:120],
                    )
        finally:
            await channel.aclose()
        return done

    try:
        return asyncio.run(run())
    except Exception as e:  # channel could not even be built
        logger.warning(
            "could not delete sent Telegram messages: {}", _error_text(e, TELEGRAM)[:120]
        )
        return 0
