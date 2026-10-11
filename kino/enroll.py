"""Headless enrollment: turn a directory of photos of one kid into reference faces."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from loguru import logger
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from kino.config import Settings
from kino.db.models import Kid, ReferenceFace
from kino.db.session import blob_to_embedding, embedding_to_blob
from kino.pipeline.detect import Detection, Detector
from kino.pipeline.embed import Embedder, align_face, make_crop
from kino.pipeline.prefilter import load_for_pipeline
from kino.services.enroll_web import (
    DUPLICATE_COSINE,
    MIN_QUALITY,
    RECOMMENDED_REFS,
    AnalyzedFace,
    analyze_faces,
    exif_datetime,
)
from kino.tasks import bump_gallery_version

EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic"})
DOMINANCE = 1.5  # the chosen face must have this many times the area of any other face

__all__ = ["DUPLICATE_COSINE", "MIN_QUALITY", "RECOMMENDED_REFS", "enroll_directory"]


@dataclass
class FileResult:
    name: str
    status: str  # enrolled | duplicate | skipped
    detail: str = ""


def list_images(directory: Path) -> list[Path]:
    return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS)


def photo_date(path: Path) -> datetime:
    """EXIF DateTimeOriginal (naive, as written by the camera), else the file mtime in UTC."""
    try:
        with Image.open(path) as im:
            taken = exif_datetime(im)
        if taken is not None:
            return taken
    except (OSError, ValueError, SyntaxError):
        pass
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).replace(tzinfo=None)


def _area(d: Detection) -> float:
    x1, y1, x2, y2 = d.bbox
    return max(x2 - x1, 0.0) * max(y2 - y1, 0.0)


def _pick_face(faces: list[AnalyzedFace]) -> tuple[AnalyzedFace | None, str]:
    """The one usable, clearly dominant face of a photo, or (None, reason)."""
    usable = [f for f in faces if f.reason is None]
    if not usable:
        return None, "no usable face (too small, blurry or off-angle)"
    best = max(usable, key=lambda f: _area(f.det))
    for other in faces:
        if other is not best and _area(best.det) < DOMINANCE * _area(other.det):
            return None, "several comparable faces; use a photo of this kid alone"
    return best, ""


def _is_duplicate(emb: np.ndarray, existing: list[np.ndarray]) -> bool:
    return any(float(np.dot(emb, e)) > DUPLICATE_COSINE for e in existing)


def enroll_directory(
    session: Session,
    kid: Kid,
    directory: Path,
    detector: Detector,
    embedder: Embedder,
    settings: Settings,
) -> list[FileResult]:
    """Add one reference per usable photo; bumps gallery_version when any was added."""
    crops = settings.data_dir / "crops" / "refs"
    existing = [
        blob_to_embedding(b)
        for b in session.scalars(
            select(ReferenceFace.embedding).where(
                ReferenceFace.kid_id == kid.id, ReferenceFace.status == "active"
            )
        )
    ]
    results: list[FileResult] = []
    for path in list_images(directory):
        try:
            results.append(
                _enroll_one(session, kid, path, detector, embedder, settings, crops, existing)
            )
        except Exception as e:  # one bad file must not abort the run
            logger.warning("enroll: {} failed: {}", path.name, type(e).__name__)
            results.append(FileResult(path.name, "skipped", f"unreadable ({type(e).__name__})"))
    if any(r.status == "enrolled" for r in results):
        bump_gallery_version(session)
    session.flush()
    return results


def _enroll_one(
    session: Session,
    kid: Kid,
    path: Path,
    detector: Detector,
    embedder: Embedder,
    settings: Settings,
    crops: Path,
    existing: list[np.ndarray],
) -> FileResult:
    loaded = load_for_pipeline(path)
    if loaded.bgr is None:
        return FileResult(path.name, "skipped", loaded.skip_reason or "skipped")
    img = loaded.bgr
    face, reason = _pick_face(analyze_faces(img, detector, embedder, settings, embed=False))
    if face is None:
        return FileResult(path.name, "skipped", reason)
    det, quality = face.det, face.fq.quality
    emb = embedder.embed(align_face(img, det))
    if _is_duplicate(emb, existing):
        return FileResult(path.name, "duplicate", "already enrolled")
    crops.mkdir(parents=True, exist_ok=True)
    crop_path = crops / f"{kid.slug}_{uuid.uuid4().hex[:12]}.jpg"
    crop_path.write_bytes(make_crop(img, det))
    session.add(
        ReferenceFace(
            kid_id=kid.id,
            embedding=embedding_to_blob(emb),
            crop_path=str(crop_path),
            source="upload",
            quality=quality,
            photo_date=photo_date(path),
            status="active",
            yaw=face.fq.yaw,
        )
    )
    existing.append(emb)
    return FileResult(path.name, "enrolled", f"quality {quality:.2f}, {det.size_px:.0f}px")


def coverage(session: Session, kid: Kid) -> int:
    return len(
        session.scalars(
            select(ReferenceFace.id).where(
                ReferenceFace.kid_id == kid.id, ReferenceFace.status == "active"
            )
        ).all()
    )
