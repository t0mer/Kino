"""Kids: lookup, create/edit, mapping to groups, delete with cascade (CLAUDE.md §11.1)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from kino import metrics
from kino.db.models import (
    Cluster,
    Forward,
    Group,
    Image,
    Kid,
    KidGroup,
    ReferenceFace,
    SavedPhoto,
)
from kino.pipeline.decide import OFFSET_MAX, OFFSET_MIN
from kino.services import ConflictError, InvalidError, NotFoundError
from kino.slug import MAX_LEN, slugify

_UNSET: Any = object()


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
        raise NotFoundError(f"no kid named {name!r} (see `kino kids list`)")
    return kid


def get_kid(session: Session, kid_id: int) -> Kid:
    kid = session.get(Kid, kid_id)
    if kid is None:
        raise NotFoundError(f"kid {kid_id} not found")
    return kid


def get_or_create_kid(
    session: Session, name: str, slug: str | None = None, birth_year: int | None = None
) -> tuple[Kid, bool]:
    """CLI enroll: reuse a kid by name/slug, or create one (an explicit slug clash is an error)."""
    kid = find_kid(session, name)
    if kid is not None:
        return kid, False
    new_slug = slugify(slug or name, fallback="kid")
    if _slug_taken(session, new_slug):
        raise ConflictError(f"slug {new_slug!r} is already used by another kid; pass --slug")
    kid = Kid(display_name=name.strip(), slug=new_slug, birth_year=birth_year)
    session.add(kid)
    session.flush()
    return kid, True


def validate_offset(offset: float) -> float:
    if not math.isfinite(offset):
        raise InvalidError("offset must be a finite number")
    if not OFFSET_MIN <= offset <= OFFSET_MAX:
        raise InvalidError(f"offset must be between {OFFSET_MIN:+.2f} and {OFFSET_MAX:+.2f}")
    return offset


def _slug_taken(session: Session, slug: str, exclude_id: int | None = None) -> bool:
    q = select(Kid.id).where(Kid.slug == slug)
    if exclude_id is not None:
        q = q.where(Kid.id != exclude_id)
    return session.scalar(q) is not None


def unique_slug(session: Session, base: str, exclude_id: int | None = None) -> str:
    """``base``, or ``base-2``, ``base-3``, ... (kept within the slug length limit)."""
    if not _slug_taken(session, base, exclude_id):
        return base
    n = 2
    while True:
        suffix = f"-{n}"
        candidate = base[: MAX_LEN - len(suffix)].rstrip("-") + suffix
        if not _slug_taken(session, candidate, exclude_id):
            return candidate
        n += 1


def _check_name(session: Session, name: str, exclude_id: int | None = None) -> str:
    name = name.strip()
    if not name:
        raise InvalidError("display name must not be empty")
    needle = name.casefold()
    for other in session.scalars(select(Kid)):
        if other.id != exclude_id and needle in (
            other.display_name.casefold(),
            other.slug.casefold(),
        ):
            raise ConflictError(f"a kid named {name!r} already exists")
    return name


def _explicit_slug(session: Session, slug: str, exclude_id: int | None = None) -> str:
    new_slug = slugify(slug, fallback="kid")
    if _slug_taken(session, new_slug, exclude_id):
        raise ConflictError(f"slug {new_slug!r} is already used by another kid")
    return new_slug


def set_kid_groups(session: Session, kid: Kid, group_ids: list[int]) -> None:
    """Replace the kid's group mapping (the candidate prior)."""
    wanted = set(group_ids)
    found = set(session.scalars(select(Group.id).where(Group.id.in_(wanted))))
    if missing := wanted - found:
        raise InvalidError(f"unknown group id(s): {', '.join(map(str, sorted(missing)))}")
    session.execute(delete(KidGroup).where(KidGroup.kid_id == kid.id))
    session.add_all(KidGroup(kid_id=kid.id, group_id=g) for g in sorted(wanted))
    session.flush()


def create_kid(
    session: Session,
    display_name: str,
    *,
    slug: str | None = None,
    birth_year: int | None = None,
    active: bool = True,
    auto_save_enabled: bool = False,
    threshold_offset: float = 0.0,
    group_ids: list[int] | None = None,
) -> Kid:
    """Create a kid. A slug derived from the name gets a numeric suffix on collision;
    an explicitly chosen slug that is taken is a conflict."""
    name = _check_name(session, display_name)
    new_slug = (
        _explicit_slug(session, slug)
        if slug and slug.strip()
        else unique_slug(session, slugify(name, fallback="kid"))
    )
    kid = Kid(
        display_name=name,
        slug=new_slug,
        birth_year=birth_year,
        active=active,
        auto_save_enabled=auto_save_enabled,
        threshold_offset=validate_offset(threshold_offset),
    )
    session.add(kid)
    session.flush()
    if group_ids:
        set_kid_groups(session, kid, group_ids)
    return kid


def update_kid(
    session: Session,
    kid_id: int,
    *,
    display_name: str | None = None,
    slug: str | None = None,
    birth_year: int | None = _UNSET,
    active: bool | None = None,
    auto_save_enabled: bool | None = None,
    threshold_offset: float | None = None,
    group_ids: list[int] | None = None,
) -> Kid:
    """Edit a kid; ``None`` leaves a field unchanged (``birth_year=None`` clears it).

    A slug change only affects photos saved from now on (existing files stay where they are).
    """
    kid = get_kid(session, kid_id)
    if display_name is not None:
        kid.display_name = _check_name(session, display_name, exclude_id=kid.id)
    if slug is not None:
        kid.slug = _explicit_slug(session, slug, exclude_id=kid.id)
    if birth_year is not _UNSET:
        kid.birth_year = birth_year
    if active is not None:
        kid.active = active
    if auto_save_enabled is not None:
        kid.auto_save_enabled = auto_save_enabled
    if threshold_offset is not None:
        kid.threshold_offset = validate_offset(threshold_offset)
    if group_ids is not None:
        set_kid_groups(session, kid, group_ids)
    session.flush()
    return kid


@dataclass
class KidDeletion:
    """Result of :func:`delete_kid`. ``files`` are removed by :func:`remove_saved_files`
    after the transaction commits."""

    kid_id: int
    files: list[Path] = field(default_factory=list)
    saved_photo_ids: list[int] = field(default_factory=list)
    cancelled_forwards: int = 0


def delete_kid(session: Session, kid_id: int) -> KidDeletion:
    """Delete a kid and everything that is only theirs.

    References, negatives, matches, saved-photo rows and group mappings go with the kid via
    ON DELETE CASCADE. A pending forward that names only this kid is cancelled; one that
    also names siblings keeps going without this kid (caption re-rendered, file switched to
    a sibling's copy). The kid's forward destination override is dropped, labelled clusters
    become unlabelled again, and the gallery version is bumped so workers reload.
    """
    from kino.forward import K_PER_KID, get_forward_config, put_setting
    from kino.tasks import bump_gallery_version

    kid = get_kid(session, kid_id)
    result = KidDeletion(kid_id=kid.id)
    paths = list(session.scalars(select(SavedPhoto.path).where(SavedPhoto.kid_id == kid.id)))
    shared = set(
        session.scalars(
            select(SavedPhoto.path).where(SavedPhoto.kid_id != kid.id, SavedPhoto.path.in_(paths))
        )
    )
    result.files = [Path(p) for p in paths if p not in shared]
    result.saved_photo_ids = list(
        session.scalars(select(SavedPhoto.id).where(SavedPhoto.kid_id == kid.id))
    )
    cfg = get_forward_config(session)
    for f in session.scalars(select(Forward).where(Forward.status == "pending")):
        if kid.id in (f.kid_ids or []) and not drop_kid_from_forward(
            session, f, kid.id, set(paths), cfg.caption_template
        ):
            f.status = "cancelled"
            f.last_error = "kid deleted"
            f.next_attempt_at = None
            metrics.forwards.labels(status="cancelled").inc()
            result.cancelled_forwards += 1
    per_kid = cfg.per_kid
    if kid.id in per_kid:
        del per_kid[kid.id]
        put_setting(session, K_PER_KID, {str(k): v for k, v in per_kid.items()})
    session.execute(
        update(Cluster)
        .where(Cluster.kid_id == kid.id)
        .values(kid_id=None, label_status="unlabeled")
    )
    session.delete(kid)
    session.flush()
    bump_gallery_version(session)
    return result


def drop_kid_from_forward(
    session: Session, f: Forward, kid_id: int, kid_paths: set[str], template: str
) -> bool:
    """Keep a pending forward that also names other kids: drop the deleted kid, re-render the
    caption and, if it pointed at the deleted kid's file, use a remaining kid's saved file.
    Returns False when nothing is left to send (the caller cancels it)."""
    from kino.forward import image_caption

    remaining = [k for k in f.kid_ids or [] if k != kid_id]
    image = session.get(Image, f.image_id)
    if not remaining or image is None:
        return False
    if f.file_path is None or f.file_path in kid_paths:
        other = session.scalar(
            select(SavedPhoto.path)
            .where(SavedPhoto.image_id == f.image_id, SavedPhoto.kid_id.in_(remaining))
            .order_by(SavedPhoto.id)
        )
        if other is None:
            return False
        f.file_path = other
    names = {
        k.id: k.display_name for k in session.scalars(select(Kid).where(Kid.id.in_(remaining)))
    }
    group = session.get(Group, image.group_id) if image.group_id is not None else None
    f.kid_ids = remaining
    f.caption = image_caption(template, [names[k] for k in remaining if k in names], image, group)
    return True


def remove_saved_files(files: list[Path], photos_dir: Path) -> int:
    """Unlink saved photos; anything resolving outside ``photos_dir`` is left alone."""
    base = photos_dir.resolve()
    removed = 0
    for path in files:
        target = path.resolve()
        if not target.is_relative_to(base):
            logger.warning("not deleting a saved photo outside the photos directory")
            continue
        try:
            target.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as e:
            logger.warning("could not delete a saved photo: {}", type(e).__name__)
    return removed


# --- read models ------------------------------------------------------------------------


@dataclass
class KidSummary:
    kid: Kid
    group_ids: list[int]
    reference_count: int
    today: int
    last_7_days: int


def day_start_utc(timezone: str, now: datetime | None = None) -> datetime:
    """Start of today in ``timezone``, as a naive UTC datetime (how timestamps are stored)."""
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(timezone))
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(UTC).replace(tzinfo=None)


def summarize_kids(
    session: Session, timezone: str, kid_ids: list[int] | None = None
) -> list[KidSummary]:
    q = select(Kid).order_by(Kid.id)
    if kid_ids is not None:
        q = q.where(Kid.id.in_(kid_ids))
    kids = list(session.scalars(q))
    groups: dict[int, list[int]] = {}
    for kg in session.scalars(select(KidGroup).order_by(KidGroup.group_id)):
        groups.setdefault(kg.kid_id, []).append(kg.group_id)
    refs = {
        int(k): int(n)
        for k, n in session.execute(
            select(ReferenceFace.kid_id, func.count())
            .where(ReferenceFace.status == "active")
            .group_by(ReferenceFace.kid_id)
        ).all()
    }
    today_start = day_start_utc(timezone)
    week_start = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=7)

    def saved_since(since: datetime) -> dict[int, int]:
        rows = session.execute(
            select(SavedPhoto.kid_id, func.count())
            .where(SavedPhoto.saved_at >= since)
            .group_by(SavedPhoto.kid_id)
        ).all()
        return {int(k): int(n) for k, n in rows}

    today, week = saved_since(today_start), saved_since(week_start)
    return [
        KidSummary(
            kid=k,
            group_ids=groups.get(k.id, []),
            reference_count=refs.get(k.id, 0),
            today=today.get(k.id, 0),
            last_7_days=week.get(k.id, 0),
        )
        for k in kids
    ]


def kid_names(session: Session) -> dict[int, str]:
    return {k.id: k.display_name for k in session.scalars(select(Kid))}
