"""WhatsApp groups: lookup, import from OpenWA, watch/unwatch, kid mapping (CLAUDE.md §11.7)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from kino.db.models import Group, Kid, KidGroup
from kino.openwa.client import OpenWAClient
from kino.services import InvalidError, NotFoundError
from kino.slug import slugify


def find_group(session: Session, chat_id: str) -> Group | None:
    return session.scalar(select(Group).where(Group.chat_id == chat_id.strip()))


def require_group(session: Session, chat_id: str) -> Group:
    group = find_group(session, chat_id)
    if group is None:
        raise NotFoundError(
            "unknown group; it appears after its first message (webhook), "
            "or run `kino groups watch --fetch` to import groups from OpenWA"
        )
    return group


def get_group(session: Session, group_id: int) -> Group:
    group = session.get(Group, group_id)
    if group is None:
        raise NotFoundError(f"group {group_id} not found")
    return group


def map_kids_to_group(session: Session, group: Group, kids: list[Kid], *, replace: bool) -> None:
    if replace:
        session.execute(delete(KidGroup).where(KidGroup.group_id == group.id))
    have = set(session.scalars(select(KidGroup.kid_id).where(KidGroup.group_id == group.id)))
    for kid in kids:
        if kid.id not in have:
            session.add(KidGroup(kid_id=kid.id, group_id=group.id))
            have.add(kid.id)
    session.flush()


def group_kid_names(session: Session, group_id: int) -> list[str]:
    return list(
        session.scalars(
            select(Kid.display_name)
            .join(KidGroup, KidGroup.kid_id == Kid.id)
            .where(KidGroup.group_id == group_id)
            .order_by(func.lower(Kid.display_name))
        )
    )


def set_watched(session: Session, group: Group, watched: bool) -> None:
    """Watch or unwatch; a forward destination can never be watched (its photos would loop)."""
    from kino.forward import ensure_not_destination

    if watched:
        ensure_not_destination(session, group.chat_id)
    group.watched = watched
    session.flush()


def update_group(
    session: Session,
    group_id: int,
    *,
    watched: bool | None = None,
    kid_ids: list[int] | None = None,
) -> Group:
    group = get_group(session, group_id)
    if kid_ids is not None:
        wanted = set(kid_ids)
        kids = list(session.scalars(select(Kid).where(Kid.id.in_(wanted))))
        if missing := wanted - {k.id for k in kids}:
            raise InvalidError(f"unknown kid id(s): {', '.join(map(str, sorted(missing)))}")
        map_kids_to_group(session, group, kids, replace=True)
    if watched is not None:
        set_watched(session, group, watched)
    return group


def import_groups(session: Session, found: list[dict[str, Any]]) -> int:
    """Upsert groups from OpenWA (unwatched when new); returns how many were new."""
    new = 0
    for item in found:
        chat_id, name = str(item.get("id") or ""), item.get("name")
        if not chat_id.endswith("@g.us"):
            continue
        group = find_group(session, chat_id)
        if group is None:
            session.add(
                Group(chat_id=chat_id, name=name, slug=slugify(name or "", fallback="group"))
            )
            new += 1
        elif name and group.name != name:
            group.name = name
            if not group.slug:  # set once from the first real name; saved paths use it
                group.slug = slugify(name, fallback="group")
    session.flush()
    return new


async def fetch_groups(client: OpenWAClient) -> list[dict[str, Any]]:
    return await client.list_groups()


@dataclass
class GroupSummary:
    group: Group
    kid_ids: list[int]
    is_forward_destination: bool


def summarize_groups(session: Session) -> list[GroupSummary]:
    from kino.forward import get_forward_config

    cfg = get_forward_config(session)
    destinations = {cfg.default_chat_id, *cfg.per_kid.values()} - {None}
    kids: dict[int, list[int]] = {}
    for kg in session.scalars(select(KidGroup).order_by(KidGroup.kid_id)):
        kids.setdefault(kg.group_id, []).append(kg.kid_id)
    return [
        GroupSummary(g, kids.get(g.id, []), g.chat_id in destinations)
        for g in session.scalars(select(Group).order_by(Group.id))
    ]
