"""/api/groups: discovered groups, watching, kid mapping, import from OpenWA (§11.7, §12)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Query
from fastapi.concurrency import run_in_threadpool
from loguru import logger
from sqlalchemy import func, select

from kino import backfill, jobs, tasks
from kino.api.deps import openwa_client, protected_router
from kino.api.schemas import GroupOut, GroupsRefreshed, GroupUpdate, JobStarted
from kino.db.models import Group
from kino.db.session import get_session
from kino.openwa.client import OpenWAClient
from kino.services import NotFoundError
from kino.services import groups as svc

router = protected_router(prefix="/api/groups", tags=["groups"])


def _out(s: svc.GroupSummary) -> GroupOut:
    g = s.group
    return GroupOut(
        id=g.id,
        chat_id=g.chat_id,
        name=g.name,
        slug=g.slug,
        watched=g.watched,
        kid_ids=s.kid_ids,
        is_forward_destination=s.is_forward_destination,
        created_at=g.created_at,
        last_seen_at=g.last_seen_at,
    )


@router.get("", response_model=list[GroupOut])
def list_groups() -> list[GroupOut]:
    with get_session() as s:
        return [_out(g) for g in svc.summarize_groups(s)]


@router.patch("/{group_id}", response_model=GroupOut)
def update_group(group_id: int, body: GroupUpdate) -> GroupOut:
    with get_session() as s:
        svc.update_group(s, group_id, watched=body.watched, kid_ids=body.kid_ids)
        return next(_out(g) for g in svc.summarize_groups(s) if g.group.id == group_id)


def _import(found: list[dict[str, Any]]) -> GroupsRefreshed:
    with get_session() as s:
        new = svc.import_groups(s, found)
        total = s.scalar(select(func.count()).select_from(Group)) or 0
    return GroupsRefreshed(imported=new, total=total)


@router.post("/refresh", response_model=GroupsRefreshed)
async def refresh_groups(
    client: Annotated[OpenWAClient, Depends(openwa_client)],
) -> GroupsRefreshed:
    """Import group names from OpenWA (new groups start unwatched)."""
    found = await svc.fetch_groups(client)
    return await run_in_threadpool(_import, found)


def _start_backfill(group_id: int, days: int) -> int:
    with get_session() as s:
        if s.get(Group, group_id) is None:
            raise NotFoundError("group not found")
    job_id = jobs.create_job(backfill.KIND, {"days": days}, subject_id=group_id, exclusive=True)
    try:
        tasks.backfill_group_task(job_id)
    except Exception as e:
        logger.error("enqueue of backfill job {} failed: {}", job_id, type(e).__name__)
        jobs.finish_job(job_id, e)
        raise
    return job_id


@router.post("/{group_id}/backfill", response_model=JobStarted, status_code=202)
async def backfill_group(
    group_id: int,
    _client: Annotated[OpenWAClient, Depends(openwa_client)],
    days: Annotated[int, Query(ge=backfill.MIN_DAYS, le=backfill.MAX_DAYS)] = 30,
) -> JobStarted:
    """Ingest the group's image messages of the last `days` days from OpenWA history."""
    return JobStarted(job_id=await run_in_threadpool(_start_backfill, group_id, days))
