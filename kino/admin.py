"""Kid and group lookups/edits shared by the CLI (and later the UI)."""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from kino.db.models import Group, Kid, KidGroup
from kino.slug import slugify


class AdminError(RuntimeError):
    """A lookup or edit that cannot be satisfied; the message is shown to the user."""


def find_kid(session: Session, name: str) -> Kid | None:
    """Match a kid by display name or slug, case-insensitively."""
    needle = name.strip().casefold()
    for kid in session.scalars(select(Kid).order_by(Kid.id)):
        if needle in (kid.display_name.casefold(), kid.slug.casefold()):
            return kid
    return None


def require_kid(session: Session, name: str) -> Kid:
    kid = find_kid(session, name)
    if kid is None:
        raise AdminError(f"no kid named {name!r} (see `kino kids list`)")
    return kid


def get_or_create_kid(
    session: Session, name: str, slug: str | None = None, birth_year: int | None = None
) -> tuple[Kid, bool]:
    kid = find_kid(session, name)
    if kid is not None:
        return kid, False
    new_slug = slugify(slug or name, fallback="kid")
    if session.scalar(select(Kid.id).where(Kid.slug == new_slug)) is not None:
        raise AdminError(f"slug {new_slug!r} is already used by another kid; pass --slug")
    kid = Kid(display_name=name.strip(), slug=new_slug, birth_year=birth_year)
    session.add(kid)
    session.flush()
    return kid, True


def find_group(session: Session, chat_id: str) -> Group | None:
    return session.scalar(select(Group).where(Group.chat_id == chat_id.strip()))


def require_group(session: Session, chat_id: str) -> Group:
    group = find_group(session, chat_id)
    if group is None:
        raise AdminError(
            "unknown group; it appears after its first message (webhook), "
            "or run `kino groups watch --fetch` to import groups from OpenWA"
        )
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
