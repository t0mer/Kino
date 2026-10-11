"""Review queue and the user's decisions on uncertain matches (CLAUDE.md §9.6, §9.8, §11.4).

Race safety: every decision starts with a conditional
``UPDATE matches ... WHERE id = ? AND decision = 'review'``. SQLite serialises writers, so
exactly one request wins it; the loser sees the committed decision and gets either an
idempotent "unchanged" result (the same action repeated) or a ConflictError (a different
action). All side effects (photo file, SavedPhoto row, forward plan, reference, negative)
run only for the winner, in the same transaction as the decision. Any error after the
UPDATE rolls the transaction back, so the item stays in review; files written so far are
removed by :func:`apply_decision`.

Ordering: the queue is listed oldest first (ascending match id) with ``cursor`` = the last
match id seen, so new items never shift a page the user is working through.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kino import metrics
from kino.config import Settings
from kino.db.models import (
    MATCH_METHOD,
    Face,
    Group,
    Image,
    Kid,
    Match,
    NegativeFace,
    ReferenceFace,
    SavedPhoto,
)
from kino.db.session import blob_to_embedding, get_session
from kino.pipeline import save as save_mod
from kino.pipeline.quality import Tier, tier_for
from kino.services import ConflictError, InvalidError, NotFoundError, StorageError
from kino.services import enroll_web as enroll

ACTIONS = ("confirm", "reject", "someone_else", "reassign")
BULK_ACTIONS = ("confirm", "reject")
MAX_BULK = 200
NEAREST_REFS = 3
_FINAL = {
    "confirm": "confirmed",
    "reassign": "confirmed",
    "reject": "rejected",
    "someone_else": "rejected",
}
_SAVED = ("auto_saved", "confirmed")


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _enqueue_forward(forward_id: int) -> None:
    from kino.services.forwards import enqueue

    enqueue(forward_id)


# Swappable in tests; called after the commit for every pending forward a decision planned.
enqueue_forward: Callable[[int], None] = _enqueue_forward


class OriginalMissing(ConflictError):
    """Neither the raw image nor a saved copy of it is on disk any more."""


# --- paths ------------------------------------------------------------------------------


def raw_file(image: Image, settings: Settings) -> Path | None:
    return enroll.contained(image.raw_path, settings.data_dir / "raw")


def saved_file(session: Session, image_id: int, settings: Settings) -> Path | None:
    """An existing saved copy of the image (any kid), inside the photos dir."""
    for p in session.scalars(
        select(SavedPhoto.path).where(SavedPhoto.image_id == image_id).order_by(SavedPhoto.id)
    ):
        found = enroll.contained(p, settings.photos_dir)
        if found is not None:
            return found
    return None


def image_file(session: Session, image: Image, settings: Settings) -> Path | None:
    """R4: the full photo from a saved file, else the raw file if still present, else None."""
    return saved_file(session, image.id, settings) or raw_file(image, settings)


def face_crop(session: Session, settings: Settings, face_id: int) -> Path:
    face = session.get(Face, face_id)
    if face is None:
        raise NotFoundError(f"face {face_id} not found")
    p = enroll.contained(face.crop_path, settings.data_dir / "crops")
    if p is None:
        raise NotFoundError("no crop stored for this face")
    return p


# --- listing ----------------------------------------------------------------------------


@dataclass
class NearRef:
    reference_id: int
    similarity: float
    has_crop: bool


@dataclass
class ReviewItem:
    match: Match
    face: Face
    image: Image
    kid: Kid
    group: Group | None
    tier: str
    has_crop: bool
    has_image: bool
    nearest: list[NearRef]


def _nearest(
    session: Session, kid_ids: set[int]
) -> dict[int, tuple[list[int], list[bool], np.ndarray]]:
    out: dict[int, tuple[list[int], list[bool], np.ndarray]] = {}
    for kid_id in kid_ids:
        rows = session.execute(
            select(ReferenceFace.id, ReferenceFace.embedding, ReferenceFace.crop_path)
            .where(ReferenceFace.kid_id == kid_id, ReferenceFace.status == "active")
            .order_by(ReferenceFace.id)
        ).all()
        if rows:
            out[kid_id] = (
                [r[0] for r in rows],
                [bool(r[2]) for r in rows],
                np.stack([blob_to_embedding(r[1]) for r in rows]),
            )
    return out


def list_review(
    session: Session,
    settings: Settings,
    kid_id: int | None = None,
    method: str | None = None,
    cursor: int | None = None,
    limit: int = 50,
) -> tuple[list[ReviewItem], int | None]:
    """Pending review items, oldest first; returns (items, next_cursor)."""
    if method is not None and method not in MATCH_METHOD:
        raise InvalidError(f"method must be one of {', '.join(MATCH_METHOD)}")
    q = (
        select(Match, Face, Image, Kid)
        .join(Face, Face.id == Match.face_id)
        .join(Image, Image.id == Face.image_id)
        .join(Kid, Kid.id == Match.kid_id)
        .where(Match.decision == "review")
    )
    if kid_id is not None:
        q = q.where(Match.kid_id == kid_id)
    if method is not None:
        q = q.where(Match.method == method)
    if cursor is not None:
        q = q.where(Match.id > cursor)
    rows = session.execute(q.order_by(Match.id).limit(limit + 1)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    refs = _nearest(session, {m.kid_id for m, *_ in rows})
    crops = settings.data_dir / "crops"
    items: list[ReviewItem] = []
    for m, face, image, kid in rows:
        near: list[NearRef] = []
        if face.embedding is not None and m.kid_id in refs:
            ids, has, mat = refs[m.kid_id]
            sims = mat @ blob_to_embedding(face.embedding)
            for i in np.argsort(-sims)[:NEAREST_REFS]:
                near.append(NearRef(ids[int(i)], float(sims[int(i)]), has[int(i)]))
        items.append(
            ReviewItem(
                match=m,
                face=face,
                image=image,
                kid=kid,
                group=session.get(Group, image.group_id) if image.group_id else None,
                tier=tier_for(face.size_px).value,
                has_crop=enroll.contained(face.crop_path, crops) is not None,
                has_image=image_file(session, image, settings) is not None,
                nearest=near,
            )
        )
    return items, (rows[-1][0].id if more and rows else None)


def review_count(session: Session) -> tuple[int, dict[int, int]]:
    """(total pending, per kid) for the nav badge."""
    by_kid = {
        int(k): int(n)
        for k, n in session.execute(
            select(Match.kid_id, func.count())
            .where(Match.decision == "review")
            .group_by(Match.kid_id)
        ).all()
    }
    return sum(by_kid.values()), by_kid


# --- decisions --------------------------------------------------------------------------


@dataclass
class DecisionResult:
    match_id: int
    decision: str
    kid_id: int
    changed: bool
    saved_photo_id: int | None = None
    reference_ids: list[int] = field(default_factory=list)
    negative_id: int | None = None
    forward_ids: list[int] = field(default_factory=list)


@dataclass
class _Effects:
    """What a decision did outside the DB: undone on rollback, finished after commit."""

    photos: dict[Path, tuple[int, int]] = field(default_factory=dict)  # newly created files
    crops: list[Path] = field(default_factory=list)  # reference / negative crop copies
    forward_ids: list[int] = field(default_factory=list)
    image_id: int | None = None
    metric: tuple[str, str, str] | None = None


def _negative_exists(session: Session, kid_id: int, face_id: int) -> bool:
    q = select(NegativeFace.id).where(
        NegativeFace.kid_id == kid_id, NegativeFace.source_face_id == face_id
    )
    return session.scalar(q) is not None


def _settled(session: Session, m: Match, action: str, target: int) -> DecisionResult:
    """The match is no longer in review: same action repeated -> unchanged, else conflict."""
    same = {
        "confirm": m.decision == "confirmed" and m.kid_id == target,
        "reassign": m.decision == "confirmed" and m.kid_id == target,
        "reject": m.decision == "rejected",
        "someone_else": m.decision == "rejected" and _negative_exists(session, m.kid_id, m.face_id),
    }[action]
    if not same:
        raise ConflictError(f"match {m.id} was already decided ({m.decision}); refresh the queue")
    return DecisionResult(m.id, m.decision, m.kid_id, changed=False)


def decide(
    session: Session,
    settings: Settings,
    match_id: int,
    action: str,
    kid_id: int | None = None,
    now: datetime | None = None,
    fx: _Effects | None = None,
) -> DecisionResult:
    """Apply one review action inside the caller's transaction (see module docstring)."""
    now = now or _utcnow()
    fx = fx if fx is not None else _Effects()
    if action not in ACTIONS:
        raise InvalidError(f"action must be one of {', '.join(ACTIONS)}")
    if action == "reassign" and kid_id is None:
        raise InvalidError("kid_id is required to reassign")
    if action != "reassign" and kid_id is not None:
        raise InvalidError("kid_id is only used with reassign")
    m = session.get(Match, match_id)
    if m is None:
        raise NotFoundError(f"review item {match_id} not found")
    target = kid_id if kid_id is not None else m.kid_id
    if m.decision != "review":
        return _settled(session, m, action, target)
    suggested = m.kid_id
    if session.get(Kid, target) is None:
        raise NotFoundError(f"kid {target} not found")
    try:
        res = session.execute(
            update(Match)
            .where(Match.id == match_id, Match.decision == "review")
            .values(decision=_FINAL[action], decided_by="user", decided_at=now, kid_id=target),
            execution_options={"synchronize_session": False},
        )
    except IntegrityError:  # the target kid was deleted meanwhile (FK)
        raise NotFoundError(f"kid {target} not found") from None
    session.expire(m)
    if res.rowcount != 1:  # type: ignore[attr-defined]  # lost the race
        m2 = session.get(Match, match_id)
        if m2 is None:
            raise NotFoundError(f"review item {match_id} not found")
        return _settled(session, m2, action, target)
    face = session.get(Face, m.face_id)
    image = session.get(Image, face.image_id) if face is not None else None
    if face is None or image is None:  # pragma: no cover - FK cascade keeps these
        raise NotFoundError(f"review item {match_id} not found")
    fx.image_id = image.id
    result = DecisionResult(m.id, m.decision, m.kid_id, changed=True)
    if action in ("confirm", "reassign"):
        kid = session.get(Kid, target)
        if kid is None:
            raise NotFoundError(f"kid {target} not found")
        _confirm(session, settings, m, face, image, kid, result, fx, now)
    elif action == "someone_else":
        result.negative_id = _add_negative(session, settings, suggested, face, fx, now)
    if action in ("someone_else", "reassign"):
        _bump(session)
    fx.metric = (str(m.kid_id), m.decision, m.method)
    session.flush()
    return result


def _bump(session: Session) -> None:
    from kino.tasks import bump_gallery_version

    bump_gallery_version(session)


def _confirm(
    session: Session,
    settings: Settings,
    m: Match,
    face: Face,
    image: Image,
    kid: Kid,
    result: DecisionResult,
    fx: _Effects,
    now: datetime,
) -> None:
    from kino import forward as fwd
    from kino.tasks import upsert_saved

    if not kid.active:
        raise ConflictError(f"{kid.display_name} is inactive; activate the kid first")
    other = session.scalar(
        select(Match.id)
        .join(Face, Face.id == Match.face_id)
        .where(
            Face.image_id == image.id,
            Match.kid_id == kid.id,
            Match.id != m.id,
            Match.decision.in_(_SAVED),
        )
    )
    if other is not None:
        raise ConflictError(f"{kid.display_name} is already matched to another face in this photo")
    group = session.get(Group, image.group_id) if image.group_id is not None else None
    if group is None:
        raise ConflictError("the photo has no group; it cannot be saved")
    path = _save_file(session, settings, image, kid, group, fx)
    upsert_saved(session, image.id, kid.id, path)
    session.flush()
    result.saved_photo_id = session.scalar(
        select(SavedPhoto.id).where(SavedPhoto.image_id == image.id, SavedPhoto.kid_id == kid.id)
    )
    cfg = fwd.get_forward_config(session)
    if cfg.include_review_confirmed:
        for row in fwd.plan_forwards(session, image, [kid], {kid.id: path}, group):
            if row.status != "pending":
                continue
            if kid.id not in (row.kid_ids or []):  # join a sibling's pending forward
                row.kid_ids = [*(row.kid_ids or []), kid.id]
                names = {
                    k.id: k.display_name
                    for k in session.scalars(select(Kid).where(Kid.id.in_(row.kid_ids)))
                }
                row.caption = fwd.image_caption(
                    cfg.caption_template,
                    [names[k] for k in row.kid_ids if k in names],
                    image,
                    group,
                )
            result.forward_ids.append(row.id)
        fx.forward_ids.extend(result.forward_ids)
    tier = tier_for(face.size_px)
    usable = face.embedding is not None and tier in (Tier.L, Tier.M)
    if usable and (
        enroll.unusable_reason(
            tier, face.quality or 0.0, face.blur_score or 0.0, face.yaw or 0.0, face.pitch or 0.0
        )
        is None
    ):
        added = enroll.add_references(
            session, kid.id, settings, face_ids=[face.id], source="review", now=now
        )
        result.reference_ids = [r.id for r in added.created]
        fx.crops.extend(Path(r.crop_path) for r in added.created if r.crop_path)


def _save_file(
    session: Session, settings: Settings, image: Image, kid: Kid, group: Group, fx: _Effects
) -> Path:
    """Save the photo for ``kid`` from the raw file, else hardlink/copy a sibling's saved copy."""
    tz = settings.timezone
    try:
        dest = save_mod.build_dest_path(kid, group, image, settings.photos_dir, timezone=tz)
    except ValueError as e:
        raise ConflictError(f"the photo cannot be saved: {e}") from None
    existed = dest.exists()
    raw = raw_file(image, settings)
    sibling = None if raw is not None else saved_file(session, image.id, settings)
    if raw is None and sibling is None:
        raise OriginalMissing("original image no longer stored; it cannot be saved")
    try:
        if raw is not None:
            save_mod.save_for_kids(
                raw,
                [kid],
                group,
                image,
                settings.photos_dir,
                strip_gps=settings.strip_gps,
                timezone=tz,
            )
        else:
            assert sibling is not None
            save_mod.link_or_copy(sibling, dest)
    except save_mod.UnreadableJpeg:
        raise ConflictError("the original image is unreadable; it cannot be saved") from None
    except (OSError, ValueError) as e:
        logger.warning("review save of image {} failed: {}", image.id, type(e).__name__)
        raise StorageError("saving the photo failed; try again") from None
    finally:
        if not existed:
            try:
                st = os.stat(dest)
                fx.photos[dest] = (st.st_dev, st.st_ino)
            except OSError:
                pass
    return dest


def _add_negative(
    session: Session, settings: Settings, kid_id: int, face: Face, fx: _Effects, now: datetime
) -> int:
    if face.embedding is None:
        raise InvalidError("this face has no embedding; it cannot become a negative example")
    crop = enroll._copy_crop(
        face.crop_path, settings.data_dir / "crops", enroll.negatives_dir(settings), f"kid{kid_id}"
    )
    if crop:
        fx.crops.append(Path(crop))
    neg = NegativeFace(
        kid_id=kid_id,
        embedding=face.embedding,
        crop_path=crop,
        source_face_id=face.id,
        added_at=now,
    )
    session.add(neg)
    session.flush()
    return neg.id


def _undo(fx: _Effects, settings: Settings) -> None:
    for p in fx.crops:
        p.unlink(missing_ok=True)
    for p, ident in fx.photos.items():
        try:
            st = os.stat(p)
            if (st.st_dev, st.st_ino) == ident:
                save_mod.remove_photo(p, settings.photos_dir)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as e:
            logger.warning("could not remove a partial photo: {}", type(e).__name__)


def _after_commit(fx: _Effects) -> None:
    from kino.tasks import cleanup_raw

    if fx.metric is not None:
        kid, decision, method = fx.metric
        metrics.matches.labels(kid=kid, decision=decision, method=method).inc()
    for fid in fx.forward_ids:
        try:
            enqueue_forward(fid)
        except Exception as e:  # the retry_forwards sweep picks the row up later
            logger.error("enqueue of forward {} failed: {}", fid, type(e).__name__)
    if fx.image_id is None:
        return
    try:
        with get_session() as s:
            saved = s.scalar(select(SavedPhoto.id).where(SavedPhoto.image_id == fx.image_id))
        if saved is not None:  # like the pipeline: raw goes once saved and nothing is pending
            cleanup_raw(fx.image_id)
    except Exception as e:
        logger.warning("raw cleanup for image {} failed: {}", fx.image_id, type(e).__name__)


def apply_decision(
    settings: Settings, match_id: int, action: str, kid_id: int | None = None
) -> DecisionResult:
    """Run :func:`decide` in its own transaction; undo files on failure, enqueue after commit."""
    fx = _Effects()
    try:
        with get_session() as s:
            result = decide(s, settings, match_id, action, kid_id, fx=fx)
    except BaseException:
        _undo(fx, settings)
        raise
    _after_commit(fx)
    return result


__all__ = [
    "ACTIONS",
    "BULK_ACTIONS",
    "MAX_BULK",
    "DecisionResult",
    "OriginalMissing",
    "ReviewItem",
    "apply_decision",
    "decide",
    "face_crop",
    "image_file",
    "list_review",
    "review_count",
]
