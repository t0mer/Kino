"""Group backfill: fetch recent image messages from OpenWA history and ingest them through
the same path as the webhook, minus the HMAC check (CLAUDE.md section 8.2).

Dedupe (message_id, pHash within 30 days), the forward echo-guard and the media fetch are all
`kino.ingest.ingest_message`'s, so a backfill can never double-process what the webhook saw.
Per-message failures are counted, not fatal; only a failed history listing fails the job.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select

from kino import jobs
from kino.config import Settings, get_settings
from kino.db.models import Group
from kino.db.session import get_session
from kino.ingest import ingest_message
from kino.openwa.client import OpenWAClient
from kino.openwa.schemas import parse_webhook
from kino.services import InvalidError
from kino.services.openwa_cfg import OpenWANotConfiguredError, load_openwa_config

KIND = "backfill"
MIN_DAYS, MAX_DAYS = 1, 90

QUEUED = {"queued", "requeued"}
SKIPPED = {"duplicate", "duplicate_message", "ignored", "unwatched", "not_an_image"}
# results that cost no OpenWA media call: no need to pause after them
NO_FETCH = {"duplicate_message", "ignored", "requeued"}


def validate_days(days: int) -> int:
    if not MIN_DAYS <= days <= MAX_DAYS:
        raise InvalidError(f"days must be between {MIN_DAYS} and {MAX_DAYS}")
    return days


def _to_event(msg: dict[str, Any], chat_id: str, session_id: str) -> Any:
    data = {**msg, "chatId": chat_id, "isGroup": True}
    return parse_webhook({"sessionId": session_id, "data": data})


async def run_backfill(
    job_id: int,
    settings: Settings,
    client: OpenWAClient,
    now: datetime | None = None,
    *,
    session_id: str | None = None,
) -> None:
    """Run job `job_id` to completion; never raises (the job row carries the outcome)."""
    job = jobs.get_job(job_id)
    if job is None:
        return
    try:
        with get_session() as s:
            chat_id = s.scalar(select(Group.chat_id).where(Group.id == job.subject_id))
        if chat_id is None:
            raise LookupError("group vanished")
        days = validate_days(int(job.params["days"]))
        cutoff = (now or datetime.now(UTC).replace(tzinfo=None)) - timedelta(days=days)
        messages = await client.list_chat_messages(chat_id, since=cutoff)
        messages.sort(key=lambda m: m.get("timestamp") or 0)
        jobs.start_job(job_id, total=len(messages))
        processed = skipped = errors = 0
        if session_id is None:
            session_id = load_openwa_config(settings).session_id or ""
        for msg in messages:
            result = "error"
            try:
                ev = _to_event(msg, chat_id, session_id)
                result = await ingest_message(
                    ev, settings, client, allow_unwatched=True, requeue=False
                )
            except Exception as e:  # one bad message must not stop the run
                logger.warning("backfill message failed: {}", type(e).__name__)
            if result in QUEUED:
                processed += 1
            elif result in SKIPPED:
                skipped += 1
            else:
                errors += 1
            jobs.update_job(job_id, processed=processed, skipped=skipped, errors=errors)
            if result not in NO_FETCH and settings.backfill_delay_seconds > 0:
                await asyncio.sleep(settings.backfill_delay_seconds)
    except Exception as e:
        logger.error("backfill job {} failed: {}", job_id, type(e).__name__)
        jobs.finish_job(job_id, e)
        return
    logger.info(
        "backfill job {} done: {} queued, {} skipped, {} errors", job_id, processed, skipped, errors
    )
    jobs.finish_job(job_id)


def backfill_group(job_id: int) -> None:
    """Huey entry point: builds its own client, runs the job, closes the client."""
    settings = get_settings()
    try:
        url, api_key, session_id = load_openwa_config(settings).client_args()
    except OpenWANotConfiguredError:
        jobs.finish_job(job_id, RuntimeError("OpenWA is not configured"))
        return

    async def go() -> None:
        client = OpenWAClient(url, api_key, session_id)
        try:
            await run_backfill(job_id, settings, client, session_id=session_id)
        finally:
            await client.aclose()

    asyncio.run(go())
