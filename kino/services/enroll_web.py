"""Enrollment from the web UI: uploads, temp faces, references, coverage (CLAUDE.md §11.2, §11.6).

Uploads are untrusted bytes: each file is size-capped, content-sniffed, pixel-capped and
decoded inside its own try/except, so one bad file never fails the others. Detected faces
are kept as ``temp_faces`` rows for TEMP_FACE_TTL; the user then picks faces, which turns
them into ``reference_faces`` exactly once (the temp row is deleted in the same
transaction). Every teaching change bumps ``gallery_version`` so workers reload.
"""

from __future__ import annotations

import io
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger
from numpy.typing import NDArray
from PIL import Image, ImageOps
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from kino.config import Settings
from kino.db.models import (
    REF_SOURCE,
    REF_STATUS,
    Face,
    Kid,
    NegativeFace,
    ReferenceFace,
    TempFace,
)
from kino.db.models import Image as ImageRow
from kino.db.session import blob_to_embedding, embedding_to_blob
from kino.pipeline.detect import Detection, Detector, detect_image
from kino.pipeline.embed import Embedder, align_face, make_crop, should_embed
from kino.pipeline.prefilter import STILL_MULTI_FORMATS, NotAnImage, sniff_image
from kino.pipeline.quality import (
    BLUR_FULL,
    PITCH_MAX_DEG,
    YAW_MAX_DEG,
    FaceQuality,
    Tier,
    face_quality,
    tier_for,
)
from kino.services import ConflictError, GoneError, InvalidError, NotFoundError, ServiceError
from kino.services.files import existing_file

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_UPLOAD_FILES = 20
MAX_UPLOAD_PIXELS = 50_000_000  # well above phone photos; rejects pixel bombs before decoding
UPLOAD_FORMATS = frozenset({"JPEG", "MPO", "PNG", "WEBP", "HEIF"})  # MPO: multi-picture JPEG
TEMP_FACE_TTL = timedelta(hours=1)
MIN_QUALITY = 0.4
RECOMMENDED_REFS = 10
SIDE_YAW_DEG = 15.0  # |yaw| at or above this counts as a side angle for coverage
OLD_REF = timedelta(days=365)
DUPLICATE_COSINE = 0.98

REASON_SMALL = "too small"
REASON_BLURRY = "too blurry"
REASON_PROFILE = "profile too extreme"
HINT_SIDE = "Add side angles"
HINT_OLD = "Most references are over a year old — add recent photos."

_EXIF_DATETIME_ORIGINAL = 36867
_EXIF_IFD = 0x8769
API_SOURCES = tuple(s for s in REF_SOURCE if s != "auto")  # "auto" is the worker's only


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class UploadRejected(ServiceError):
    """One uploaded file cannot be used; ``status`` is the HTTP code for it."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


# --- face analysis (shared with the CLI enroll) ----------------------------------------------


def unusable_reason(
    tier: Tier, quality: float, blur_score: float, yaw: float, pitch: float
) -> str | None:
    """Why a face cannot become a reference, or None when it can (CLAUDE.md §11.2).

    Tier S/XS faces are "too small". Below MIN_QUALITY the reason names whichever of
    sharpness and pose costs the most quality (a tie blames the pose).
    """
    if tier in (Tier.S, Tier.XS):
        return REASON_SMALL
    if quality >= MIN_QUALITY:
        return None
    blur_t = min(max(blur_score, 0.0) / BLUR_FULL, 1.0)
    pose_t = max(0.0, 1.0 - abs(yaw) / YAW_MAX_DEG) * max(0.0, 1.0 - abs(pitch) / PITCH_MAX_DEG)
    return REASON_BLURRY if blur_t < pose_t else REASON_PROFILE


@dataclass
class AnalyzedFace:
    det: Detection
    tier: Tier
    fq: FaceQuality
    reason: str | None
    emb: NDArray[np.float32] | None


def analyze_faces(
    img: NDArray[np.uint8],
    detector: Detector,
    embedder: Embedder,
    settings: Settings,
    embed: bool = True,
) -> list[AnalyzedFace]:
    """Detect, score and (unless tier XS) embed every face of an upright BGR image."""
    out: list[AnalyzedFace] = []
    for det in detect_image(detector, img, settings):
        aligned = align_face(img, det)
        fq = face_quality(det, aligned)
        tier = tier_for(fq.size_px)
        emb = embedder.embed(aligned) if embed and should_embed(tier) else None
        reason = unusable_reason(tier, fq.quality, fq.blur_score, fq.yaw, fq.pitch)
        out.append(AnalyzedFace(det, tier, fq, reason, emb))
    return out


def exif_datetime(im: Image.Image) -> datetime | None:
    """EXIF DateTimeOriginal as written by the camera (naive), or None."""
    try:
        raw = im.getexif().get_ifd(_EXIF_IFD).get(_EXIF_DATETIME_ORIGINAL)
        if raw:
            return datetime.strptime(str(raw).strip().rstrip("\x00"), "%Y:%m:%d %H:%M:%S")
    except (OSError, ValueError, SyntaxError, TypeError, KeyError):
        pass
    return None


# --- decoding untrusted uploads ---------------------------------------------------------------


@dataclass
class DecodedUpload:
    bgr: NDArray[np.uint8]
    width: int
    height: int
    photo_date: datetime | None


def decode_upload(data: bytes) -> DecodedUpload:
    """Validate and decode one uploaded file; raises UploadRejected (413/415/422)."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadRejected(413, f"file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if not data:
        raise UploadRejected(415, "empty file")
    try:
        info = sniff_image(data)
    except NotAnImage as e:
        reason = str(e)
        if "dimensions" in reason:
            raise UploadRejected(422, "image dimensions are too large") from None
        raise UploadRejected(415, "not a supported image (JPEG, PNG, WebP or HEIC)") from None
    if info.format not in UPLOAD_FORMATS:
        raise UploadRejected(415, "not a supported image (JPEG, PNG, WebP or HEIC)")
    if info.width * info.height > MAX_UPLOAD_PIXELS:
        mp = info.width * info.height / 1e6
        raise UploadRejected(
            422, f"image is {mp:.0f} megapixels; the limit is {MAX_UPLOAD_PIXELS // 1_000_000}"
        )
    try:
        with Image.open(io.BytesIO(data)) as im:
            multi = getattr(im, "is_animated", False) or getattr(im, "n_frames", 1) > 1
            if multi and im.format not in STILL_MULTI_FORMATS:  # HEIF/MPO: primary image
                raise UploadRejected(422, "animated images are not supported")
            taken = exif_datetime(im)
            im.load()
            rgb = ImageOps.exif_transpose(im).convert("RGB")
    except UploadRejected:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as e:
        raise UploadRejected(422, f"image could not be decoded ({type(e).__name__})") from None
    bgr = np.ascontiguousarray(np.asarray(rgb, dtype=np.uint8)[:, :, ::-1])
    return DecodedUpload(bgr, rgb.width, rgb.height, taken)


# --- uploads and temp faces -------------------------------------------------------------------


def temp_dir(settings: Settings) -> Path:
    return settings.data_dir / "crops" / "tmp"


def refs_dir(settings: Settings) -> Path:
    return settings.data_dir / "crops" / "refs"


def negatives_dir(settings: Settings) -> Path:
    return settings.data_dir / "crops" / "negatives"


def contained(path: str | Path | None, base: Path) -> Path | None:
    """``path`` resolved, if it is a file inside ``base``; else None (no traversal)."""
    return existing_file(base, path)


@dataclass
class UploadFace:
    temp_face_id: str
    bbox: tuple[float, float, float, float]  # x, y, w, h in upright image pixels
    quality: float
    tier: str
    selectable: bool
    reason: str | None


@dataclass
class UploadResult:
    upload_id: str
    filename: str
    width: int | None = None
    height: int | None = None
    photo_date: datetime | None = None
    faces: list[UploadFace] = field(default_factory=list)
    error: str | None = None
    status: int = 200


def clean_filename(name: str | None) -> str:
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = "".join(ch for ch in base if ch.isprintable())
    return base[:200] or "upload"


def process_upload(
    session: Session,
    kid_id: int,
    filename: str,
    data: bytes,
    detector: Detector,
    embedder: Embedder,
    settings: Settings,
    now: datetime | None = None,
) -> UploadResult:
    """Detect and embed the faces of one uploaded file and store them as temp faces.

    Never raises for a bad file: the result carries ``error`` and an HTTP-ish ``status``.
    """
    now = now or _utcnow()
    result = UploadResult(upload_id=uuid.uuid4().hex, filename=clean_filename(filename))
    try:
        decoded = decode_upload(data)
        faces = analyze_faces(decoded.bgr, detector, embedder, settings)
    except UploadRejected as e:
        result.error, result.status = str(e), e.status
        return result
    except Exception as e:  # a detector/embedder failure on this file only
        logger.warning("upload {}: processing failed: {}", result.upload_id, type(e).__name__)
        result.error, result.status = f"image could not be processed ({type(e).__name__})", 422
        return result
    result.width, result.height = decoded.width, decoded.height
    result.photo_date = decoded.photo_date
    tmp = temp_dir(settings)
    tmp.mkdir(parents=True, exist_ok=True)
    for f in faces:
        tid = uuid.uuid4().hex
        crop = tmp / f"{tid}.jpg"
        crop.write_bytes(make_crop(decoded.bgr, f.det))
        x1, y1, x2, y2 = (float(v) for v in f.det.bbox)
        session.add(
            TempFace(
                id=tid,
                upload_id=result.upload_id,
                kid_id=kid_id,
                filename=result.filename,
                bbox_x=x1,
                bbox_y=y1,
                bbox_w=x2 - x1,
                bbox_h=y2 - y1,
                size_px=f.fq.size_px,
                quality=f.fq.quality,
                blur_score=f.fq.blur_score,
                tier=f.tier.value,
                yaw=f.fq.yaw,
                pitch=f.fq.pitch,
                embedding=None if f.emb is None else embedding_to_blob(f.emb),
                crop_path=str(crop),
                photo_date=decoded.photo_date,
                created_at=now,
            )
        )
        result.faces.append(
            UploadFace(
                temp_face_id=tid,
                bbox=(x1, y1, x2 - x1, y2 - y1),
                quality=f.fq.quality,
                tier=f.tier.value,
                selectable=f.reason is None,
                reason=f.reason,
            )
        )
    session.flush()
    return result


def get_temp_face(session: Session, temp_face_id: str, now: datetime | None = None) -> TempFace:
    """A live temp face; NotFoundError when unknown or used, GoneError when expired."""
    tf = session.get(TempFace, temp_face_id)
    if tf is None:
        raise NotFoundError(f"temp face {temp_face_id[:32]} not found (already used?)")
    if tf.created_at < (now or _utcnow()) - TEMP_FACE_TTL:
        raise GoneError("this upload has expired; upload the photo again")
    return tf


def _unlink(path: Path | None) -> None:
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("could not remove {}: {}", path.name, type(e).__name__)


def purge_temp_faces(session: Session, settings: Settings, now: datetime | None = None) -> int:
    """Delete expired temp faces and their crops, plus stray temp crop files past the TTL."""
    now = now or _utcnow()
    cutoff = now - TEMP_FACE_TTL
    rows = session.scalars(select(TempFace).where(TempFace.created_at < cutoff)).all()
    tmp = temp_dir(settings)
    for tf in rows:
        _unlink(contained(tf.crop_path, tmp))
        session.delete(tf)
    session.flush()
    live = set(session.scalars(select(TempFace.crop_path)))
    if tmp.is_dir():
        stamp = cutoff.replace(tzinfo=UTC).timestamp()
        for p in tmp.iterdir():
            try:
                stale = p.is_file() and p.stat().st_mtime < stamp and str(p) not in live
            except OSError:
                continue
            if stale:
                _unlink(p)
    return len(rows)


# --- references -------------------------------------------------------------------------------


def _bump(session: Session) -> None:
    from kino.tasks import bump_gallery_version

    bump_gallery_version(session)


def _get_kid(session: Session, kid_id: int) -> Kid:
    kid = session.get(Kid, kid_id)
    if kid is None:
        raise NotFoundError(f"kid {kid_id} not found")
    return kid


def _active_embeddings(session: Session, kid_id: int) -> list[NDArray[np.float32]]:
    blobs = session.scalars(
        select(ReferenceFace.embedding).where(
            ReferenceFace.kid_id == kid_id, ReferenceFace.status == "active"
        )
    )
    return [blob_to_embedding(b) for b in blobs]


def _is_duplicate(emb: NDArray[np.float32], existing: list[NDArray[np.float32]]) -> bool:
    return any(float(np.dot(emb, e)) > DUPLICATE_COSINE for e in existing)


def _copy_crop(src: str | None, base: Path, dest_dir: Path, stem: str) -> str | None:
    p = contained(src, base)
    if p is None:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{stem}_{uuid.uuid4().hex[:12]}.jpg"
    shutil.copyfile(p, dest)
    return str(dest)


@dataclass
class _Candidate:
    emb: NDArray[np.float32]
    quality: float | None
    yaw: float | None
    photo_date: datetime | None
    face_id: int | None
    crop_src: str | None
    temp: TempFace | None = None


@dataclass
class AddResult:
    created: list[ReferenceFace]
    duplicates: int
    # Temp crops of the consumed faces: the caller removes them after the commit.
    remove_after_commit: list[Path] = field(default_factory=list)


def add_references(
    session: Session,
    kid_id: int,
    settings: Settings,
    temp_face_ids: list[str] | None = None,
    face_ids: list[int] | None = None,
    source: str = "upload",
    photo_date: datetime | None = None,
    now: datetime | None = None,
) -> AddResult:
    """Turn picked temp faces and/or pipeline faces into references, all or nothing.

    Temp faces are consumed (deleted) here, so a repeated request finds them gone (404)
    instead of creating duplicates. Faces that are too small, too blurry or too far in
    profile are rejected with the same reason the upload showed. Near-identical faces
    (already a reference of this kid) are skipped and counted as duplicates.
    """
    now = now or _utcnow()
    if source not in API_SOURCES:
        raise InvalidError(f"source must be one of {', '.join(API_SOURCES)}")
    temp_face_ids = list(dict.fromkeys(temp_face_ids or []))
    face_ids = list(dict.fromkeys(face_ids or []))
    if not temp_face_ids and not face_ids:
        raise InvalidError("pick at least one face")
    kid = _get_kid(session, kid_id)
    cands: list[_Candidate] = []
    for tid in temp_face_ids:
        tf = get_temp_face(session, tid, now)
        if tf.kid_id != kid.id:
            raise NotFoundError(f"temp face {tid[:32]} not found (already used?)")
        reason = unusable_reason(
            Tier(tf.tier), tf.quality, tf.blur_score or 0.0, tf.yaw or 0.0, tf.pitch or 0.0
        )
        if reason is not None or tf.embedding is None:
            raise InvalidError(f"face in {tf.filename} cannot be used: {reason or REASON_SMALL}")
        cands.append(
            _Candidate(
                blob_to_embedding(tf.embedding),
                tf.quality,
                tf.yaw,
                tf.photo_date,
                None,
                tf.crop_path,
                tf,
            )
        )
    for fid in face_ids:
        face = session.get(Face, fid)
        if face is None:
            raise NotFoundError(f"face {fid} not found")
        reason = unusable_reason(
            tier_for(face.size_px),
            face.quality or 0.0,
            face.blur_score or 0.0,
            face.yaw or 0.0,
            face.pitch or 0.0,
        )
        if reason is not None or face.embedding is None:
            raise InvalidError(f"face {fid} cannot be used: {reason or REASON_SMALL}")
        image = session.get(ImageRow, face.image_id)
        cands.append(
            _Candidate(
                blob_to_embedding(face.embedding),
                face.quality,
                face.yaw,
                image.sent_at if image is not None else None,
                face.id,
                face.crop_path,
            )
        )
    # Consume temp faces first: a concurrent identical request loses this race (rowcount 0).
    if temp_face_ids:
        res = session.execute(delete(TempFace).where(TempFace.id.in_(temp_face_ids)))
        if res.rowcount != len(temp_face_ids):  # type: ignore[attr-defined]
            raise NotFoundError("some faces were already used; refresh and try again")
    existing = _active_embeddings(session, kid.id)
    linked = set(
        session.scalars(
            select(ReferenceFace.face_id).where(
                ReferenceFace.kid_id == kid.id,
                ReferenceFace.status == "active",
                ReferenceFace.face_id.is_not(None),
            )
        )
    )
    created: list[ReferenceFace] = []
    duplicates = 0
    tmp, crops_root = temp_dir(settings), settings.data_dir / "crops"
    moved: list[Path] = []
    for c in cands:
        src = contained(c.crop_src, tmp) if c.temp is not None else None
        if src is not None:
            moved.append(src)
        if (c.face_id is not None and c.face_id in linked) or _is_duplicate(c.emb, existing):
            duplicates += 1
            continue
        if c.temp is not None:
            crop = _copy_crop(c.crop_src, tmp, refs_dir(settings), kid.slug)
        else:
            crop = _copy_crop(c.crop_src, crops_root, refs_dir(settings), kid.slug)
        ref = ReferenceFace(
            kid_id=kid.id,
            face_id=c.face_id,
            embedding=embedding_to_blob(c.emb),
            crop_path=crop,
            source=source,
            quality=c.quality,
            photo_date=photo_date or c.photo_date or now,
            added_at=now,
            weight=1.0,
            status="active",
            yaw=c.yaw,
        )
        session.add(ref)
        created.append(ref)
        existing.append(c.emb)
        if c.face_id is not None:
            linked.add(c.face_id)
    if created:
        _bump(session)
    session.flush()
    return AddResult(created, duplicates, moved)


def list_references(
    session: Session,
    kid_id: int,
    status: str | None = None,
    source: str | None = None,
    cursor: int | None = None,
    limit: int = 200,
) -> list[ReferenceFace]:
    _get_kid(session, kid_id)
    if status is not None and status not in REF_STATUS:
        raise InvalidError(f"status must be one of {', '.join(REF_STATUS)}")
    if source is not None and source not in REF_SOURCE:
        raise InvalidError(f"source must be one of {', '.join(REF_SOURCE)}")
    q = select(ReferenceFace).where(ReferenceFace.kid_id == kid_id)
    if status is not None:
        q = q.where(ReferenceFace.status == status)
    if source is not None:
        q = q.where(ReferenceFace.source == source)
    if cursor is not None:
        q = q.where(ReferenceFace.id < cursor)
    return list(session.scalars(q.order_by(ReferenceFace.id.desc()).limit(limit)))


def get_reference(session: Session, ref_id: int) -> ReferenceFace:
    ref = session.get(ReferenceFace, ref_id)
    if ref is None:
        raise NotFoundError(f"reference {ref_id} not found")
    return ref


_UNSET: Any = object()


def update_reference(
    session: Session,
    ref_id: int,
    status: str | None = None,
    kid_id: int | None = None,
    photo_date: datetime | None = _UNSET,
) -> ReferenceFace:
    """Retire/reactivate, move to another kid, or correct the photo date."""
    ref = get_reference(session, ref_id)
    if status is not None:
        if status not in REF_STATUS:
            raise InvalidError(f"status must be one of {', '.join(REF_STATUS)}")
        ref.status = status
    if kid_id is not None and kid_id != ref.kid_id:
        _get_kid(session, kid_id)
        if ref.face_id is not None and session.scalar(
            select(ReferenceFace.id).where(
                ReferenceFace.kid_id == kid_id,
                ReferenceFace.face_id == ref.face_id,
                ReferenceFace.status == "active",
                ReferenceFace.id != ref.id,
            )
        ):
            raise ConflictError("that kid already has this face as a reference")
        ref.kid_id = kid_id
    if photo_date is not _UNSET:
        ref.photo_date = photo_date
    _bump(session)
    session.flush()
    return ref


def _crop_in_use(session: Session, path: str, except_ref: int) -> bool:
    for model, col in (
        (ReferenceFace, ReferenceFace.crop_path),
        (NegativeFace, NegativeFace.crop_path),
        (Face, Face.crop_path),
    ):
        q = select(func.count()).select_from(model).where(col == path)
        if model is ReferenceFace:
            q = q.where(ReferenceFace.id != except_ref)
        if session.scalar(q):
            return True
    return False


def delete_reference(session: Session, settings: Settings, ref_id: int) -> Path | None:
    """Delete a reference; returns its crop file to remove after commit (only when the
    crop lives in the references dir and nothing else uses it)."""
    ref = get_reference(session, ref_id)
    crop = contained(ref.crop_path, refs_dir(settings))
    if crop is not None and ref.crop_path and _crop_in_use(session, ref.crop_path, ref.id):
        crop = None
    session.delete(ref)
    _bump(session)
    session.flush()
    return crop


def remove_file(path: Path | None) -> None:
    _unlink(path)


def reference_to_negative(
    session: Session, settings: Settings, ref_id: int
) -> tuple[NegativeFace, Path | None]:
    """ "It's someone else": the reference becomes a negative example for its kid and is
    removed from the gallery. The negative keeps its own copy of the crop; the returned path
    is the reference's old crop, to remove after the commit."""
    ref = get_reference(session, ref_id)
    crop = _copy_crop(
        ref.crop_path, settings.data_dir / "crops", negatives_dir(settings), f"kid{ref.kid_id}"
    )
    neg = NegativeFace(
        kid_id=ref.kid_id,
        embedding=ref.embedding,
        crop_path=crop,
        source_face_id=ref.face_id,
    )
    session.add(neg)
    old = delete_reference(session, settings, ref_id)
    session.flush()
    return neg, old


# --- coverage ---------------------------------------------------------------------------------


@dataclass
class Coverage:
    kid_id: int
    count: int
    recommended: int
    yaw_known: int
    yaw_min: float | None
    yaw_max: float | None
    left: int
    frontal: int
    right: int
    dated: int
    older_than_year: int
    oldest: datetime | None
    newest: datetime | None
    hints: list[str]


def coverage(session: Session, kid_id: int, now: datetime | None = None) -> Coverage:
    """Reference count, yaw spread and age, with the spec's hints (CLAUDE.md §11.2)."""
    now = now or _utcnow()
    _get_kid(session, kid_id)
    rows = session.execute(
        select(ReferenceFace.yaw, ReferenceFace.photo_date).where(
            ReferenceFace.kid_id == kid_id, ReferenceFace.status == "active"
        )
    ).all()
    yaws = [float(y) for y, _ in rows if y is not None]
    dates = [d for _, d in rows if d is not None]
    old = sum(d < now - OLD_REF for d in dates)
    left = sum(y <= -SIDE_YAW_DEG for y in yaws)
    right = sum(y >= SIDE_YAW_DEG for y in yaws)
    hints: list[str] = []
    if len(rows) < RECOMMENDED_REFS:
        missing = RECOMMENDED_REFS - len(rows)
        hints.append(
            f"Add {missing} more reference{'s' if missing != 1 else ''} "
            f"(recommended minimum is {RECOMMENDED_REFS})"
        )
    if yaws and (left == 0 or right == 0):
        hints.append(HINT_SIDE)
    if dates and old * 2 > len(dates):
        hints.append(HINT_OLD)
    return Coverage(
        kid_id=kid_id,
        count=len(rows),
        recommended=RECOMMENDED_REFS,
        yaw_known=len(yaws),
        yaw_min=min(yaws) if yaws else None,
        yaw_max=max(yaws) if yaws else None,
        left=left,
        frontal=len(yaws) - left - right,
        right=right,
        dated=len(dates),
        older_than_year=old,
        oldest=min(dates) if dates else None,
        newest=max(dates) if dates else None,
        hints=hints,
    )


def reference_crop(session: Session, settings: Settings, ref_id: int) -> Path:
    ref = get_reference(session, ref_id)
    p = contained(ref.crop_path, settings.data_dir / "crops")
    if p is None:
        raise NotFoundError("no crop stored for this reference")
    return p


def temp_crop(session: Session, settings: Settings, temp_face_id: str) -> Path:
    tf = get_temp_face(session, temp_face_id)
    p = contained(tf.crop_path, temp_dir(settings))
    if p is None:
        raise NotFoundError("no crop stored for this face")
    return p


__all__ = [
    "MAX_UPLOAD_BYTES",
    "MAX_UPLOAD_FILES",
    "RECOMMENDED_REFS",
    "AddResult",
    "Coverage",
    "UploadRejected",
    "UploadResult",
    "add_references",
    "analyze_faces",
    "contained",
    "coverage",
    "decode_upload",
    "delete_reference",
    "exif_datetime",
    "get_reference",
    "list_references",
    "process_upload",
    "purge_temp_faces",
    "reference_crop",
    "reference_to_negative",
    "remove_file",
    "temp_crop",
    "unusable_reason",
    "update_reference",
]
