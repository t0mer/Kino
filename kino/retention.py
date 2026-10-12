"""Retention cleanup (CLAUDE.md sections 9.8, 10, 13).

Two sweeps, both idempotent and safe to run concurrently with the pipeline:

* raw images that matched no kid, older than the raw retention (default 7 days). A raw file
  is kept while a review match is pending on it or a forward for it is unfinished.
* embeddings and crops of faces that are not matched to a kid and are neither references
  nor negatives, older than the face retention (default 30 days).

Values come from :func:`kino.services.settings_svc.get_retention` so UI edits take effect.
The DB change is committed first and files are unlinked afterwards, so a crash leaves at
worst an orphan file (never a row pointing at a missing file). Every path is checked with
``safe_join`` against its base directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from loguru import logger
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from kino.config import Settings, get_settings
from kino.db.models import (
    Face,
    Forward,
    Image,
    Match,
    NegativeFace,
    ReferenceFace,
    SavedPhoto,
)
from kino.db.session import get_session
from kino.services.files import safe_join
from kino.services.settings_svc import get_retention

BATCH = 500
KID_DECISIONS = ("auto_saved", "confirmed", "review")


@dataclass
class RetentionResult:
    raw_files: int = 0
    face_embeddings: int = 0
    face_crops: int = 0


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _unlink(base: Path, rel: str | None) -> bool:
    if not rel:
        return False
    try:
        p = safe_join(base, rel)
    except ValueError:
        logger.warning("retention: refusing a path outside {}", base.name)
        return False
    try:
        p.unlink()
    except FileNotFoundError:
        return False
    except OSError as e:
        logger.warning("retention: could not delete a file: {}", type(e).__name__)
        return False
    return True


def _expired_raw_ids(s: Session, cutoff: datetime) -> list[int]:
    pending_review = (
        select(Match.id)
        .join(Face, Face.id == Match.face_id)
        .where(Face.image_id == Image.id, Match.decision == "review")
    )
    unfinished_forward = select(Forward.id).where(
        Forward.image_id == Image.id, Forward.status == "pending"
    )
    saved = select(SavedPhoto.id).where(SavedPhoto.image_id == Image.id)
    return list(
        s.scalars(
            select(Image.id)
            .where(
                Image.raw_path.is_not(None),
                Image.received_at < cutoff,
                Image.status.in_(("done", "skipped", "failed")),
                ~exists(saved),
                ~exists(pending_review),
                ~exists(unfinished_forward),
            )
            .limit(BATCH)
        )
    )


def purge_raw(s: Session, days: int, now: datetime) -> list[str]:
    """Clear ``raw_path`` of expired no-kid images; returns the paths to unlink."""
    paths: list[str] = []
    cutoff = now - timedelta(days=days)
    while True:
        ids = _expired_raw_ids(s, cutoff)
        if not ids:
            return paths
        for image in s.scalars(select(Image).where(Image.id.in_(ids))):
            if image.raw_path:
                paths.append(image.raw_path)
            image.raw_path = None
        s.flush()


def _expired_face_ids(s: Session, cutoff: datetime) -> list[int]:
    kid_match = select(Match.id).where(Match.face_id == Face.id, Match.decision.in_(KID_DECISIONS))
    ref = select(ReferenceFace.id).where(ReferenceFace.face_id == Face.id)
    neg = select(NegativeFace.id).where(NegativeFace.source_face_id == Face.id)
    return list(
        s.scalars(
            select(Face.id)
            .where(
                Face.created_at < cutoff,
                (Face.embedding.is_not(None)) | (Face.crop_path.is_not(None)),
                ~exists(kid_match),
                ~exists(ref),
                ~exists(neg),
            )
            .limit(BATCH)
        )
    )


def purge_faces(s: Session, days: int, now: datetime) -> tuple[int, list[str]]:
    """Clear embedding and crop_path of expired non-kid faces; returns (count, crop paths)."""
    crops: list[str] = []
    count = 0
    cutoff = now - timedelta(days=days)
    while True:
        ids = _expired_face_ids(s, cutoff)
        if not ids:
            return count, crops
        for face in s.scalars(select(Face).where(Face.id.in_(ids))):
            if face.crop_path:
                crops.append(face.crop_path)
            face.embedding = None
            face.crop_path = None
            count += 1
        s.flush()


def run_retention(settings: Settings | None = None, now: datetime | None = None) -> RetentionResult:
    st = settings or get_settings()
    now = now or _utcnow()
    result = RetentionResult()
    with get_session() as s:
        cfg = get_retention(s, st)
        raw_paths = purge_raw(s, cfg["raw_days"], now)
        face_count, crop_paths = purge_faces(s, cfg["face_days"], now)
    # Committed: now the files.
    result.raw_files = sum(_unlink(st.data_dir / "raw", p) for p in raw_paths)
    result.face_crops = sum(_unlink(st.data_dir / "crops", p) for p in crop_paths)
    result.face_embeddings = face_count
    if raw_paths or face_count:
        logger.info(
            "retention: {} raw file(s), {} face embedding(s), {} crop(s) removed",
            result.raw_files,
            result.face_embeddings,
            result.face_crops,
        )
    return result
