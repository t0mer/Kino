"""Activity list and image detail (CLAUDE.md §11.7). Read-only; no paths or embeddings leak."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kino.config import Settings
from kino.db.models import Face, Forward, Group, Image, Kid, Match, SavedPhoto
from kino.pipeline.quality import tier_for
from kino.services import NotFoundError
from kino.services.files import existing_file

MAX_ERROR = 300
_PATH = re.compile(r"(?:[A-Za-z]:)?[/\\][^\s'\"]+")


def sanitize_error(text: str | None) -> str | None:
    """The error with filesystem paths masked, capped to MAX_ERROR characters."""
    if not text:
        return None
    return _PATH.sub("<path>", text)[:MAX_ERROR]


def _has_file(image: Image, saved: dict[int, list[str]], settings: Settings) -> bool:
    if any(existing_file(settings.photos_dir, p) for p in saved.get(image.id, ())):
        return True
    return existing_file(settings.data_dir / "raw", image.raw_path) is not None


def _saved_paths(session: Session, ids: list[int]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = defaultdict(list)
    for iid, path in session.execute(
        select(SavedPhoto.image_id, SavedPhoto.path)
        .where(SavedPhoto.image_id.in_(ids))
        .order_by(SavedPhoto.id)
    ):
        out[iid].append(path)
    return out


@dataclass
class ImageRow:
    image: Image
    group_name: str | None
    face_count: int
    matches: dict[str, int]
    has_file: bool


def list_images(
    session: Session,
    settings: Settings,
    status: str | None = None,
    group_id: int | None = None,
    cursor: int | None = None,
    limit: int = 50,
) -> tuple[list[ImageRow], int | None]:
    """Newest first (by id); ``cursor`` is the last id seen."""
    q = select(Image, Group.name).outerjoin(Group, Group.id == Image.group_id)
    if status is not None:
        q = q.where(Image.status == status)
    if group_id is not None:
        q = q.where(Image.group_id == group_id)
    if cursor is not None:
        q = q.where(Image.id < cursor)
    rows = session.execute(q.order_by(Image.id.desc()).limit(limit + 1)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    ids = [i.id for i, _ in rows]
    faces: Counter[int] = Counter()
    matches: dict[int, Counter[str]] = defaultdict(Counter)
    if ids:
        for iid, n in session.execute(
            select(Face.image_id, func.count())
            .where(Face.image_id.in_(ids))
            .group_by(Face.image_id)
        ):
            faces[iid] = n
        for iid, dec, n in session.execute(
            select(Face.image_id, Match.decision, func.count())
            .join(Face, Face.id == Match.face_id)
            .where(Face.image_id.in_(ids))
            .group_by(Face.image_id, Match.decision)
        ):
            matches[iid][dec] = n
    saved = _saved_paths(session, ids)
    out = [
        ImageRow(i, g, faces[i.id], dict(matches[i.id]), _has_file(i, saved, settings))
        for i, g in rows
    ]
    return out, (out[-1].image.id if more and out else None)


@dataclass
class FaceDetail:
    face: Face
    tier: str


@dataclass
class MatchDetail:
    match: Match
    kid_name: str


@dataclass
class ImageDetail:
    image: Image
    group_name: str | None
    faces: list[FaceDetail] = field(default_factory=list)
    matches: list[MatchDetail] = field(default_factory=list)
    forwards: list[Forward] = field(default_factory=list)
    has_file: bool = False


def get_image(session: Session, image_id: int) -> Image:
    image = session.get(Image, image_id)
    if image is None:
        raise NotFoundError(f"image {image_id} not found")
    return image


def image_detail(session: Session, settings: Settings, image_id: int) -> ImageDetail:
    image = get_image(session, image_id)
    group = session.get(Group, image.group_id) if image.group_id is not None else None
    faces = list(session.scalars(select(Face).where(Face.image_id == image_id).order_by(Face.id)))
    mrows = session.execute(
        select(Match, Kid.display_name)
        .join(Kid, Kid.id == Match.kid_id)
        .join(Face, Face.id == Match.face_id)
        .where(Face.image_id == image_id)
        .order_by(Match.id)
    ).all()
    fwds = list(
        session.scalars(select(Forward).where(Forward.image_id == image_id).order_by(Forward.id))
    )
    return ImageDetail(
        image=image,
        group_name=group.name if group else None,
        faces=[FaceDetail(f, tier_for(f.size_px).value) for f in faces],
        matches=[MatchDetail(m, n) for m, n in mrows],
        forwards=fwds,
        has_file=_has_file(image, _saved_paths(session, [image_id]), settings),
    )


__all__ = [
    "ImageDetail",
    "ImageRow",
    "get_image",
    "image_detail",
    "list_images",
    "sanitize_error",
]
