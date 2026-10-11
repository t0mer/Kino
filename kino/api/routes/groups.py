"""/api/groups: discovered groups, watching, kid mapping, import from OpenWA (§11.7, §12)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select

from kino.api.deps import openwa_client, protected_router
from kino.api.schemas import GroupOut, GroupsRefreshed, GroupUpdate
from kino.db.models import Group
from kino.db.session import get_session
from kino.openwa.client import OpenWAClient
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
