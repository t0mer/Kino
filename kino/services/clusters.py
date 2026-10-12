"""Unknown-face clusters: build, list, label, ignore, split (CLAUDE.md §10, §11.3, §13).

``build_clusters`` is the body of the nightly / on-demand task. It rebuilds only *unlabeled*
clusters; faces in labeled clusters are never touched, and ignored clusters leave nothing
behind (ignoring deletes the faces, so no embedding, crop file or row survives, §13).

File deletion always happens after the DB commit, restricted to ``<data>/crops`` through
``safe_join``, so a tampered ``crop_path`` can never delete anything elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from loguru import logger
from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.orm import Session

from kino import clustering
from kino.config import Settings
from kino.db.models import CLUSTER_LABEL, Cluster, Face, Group, Kid, Match
from kino.db.models import Image as ImageRow
from kino.db.session import blob_to_embedding, get_session
from kino.pipeline.quality import Tier, tier_for
from kino.services import ConflictError, InvalidError, NotFoundError
from kino.services.enroll_web import add_references, contained, unusable_reason
from kino.services.rescore import RescoreResult, photo_date, rescore

CANDIDATE_DAYS = 60
MIN_TIER_SIZE_PX = 40.0  # tier M and up (kino.pipeline.quality.tier_for)
MAX_LABEL_REFERENCES = 15
SAMPLES_PER_CARD = 6
ACTIVE_MATCH = ("auto_saved", "confirmed", "review")


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _remove_files(paths: list[Path]) -> int:
    n = 0
    for p in paths:
        try:
            p.unlink(missing_ok=True)
            n += 1
        except OSError as e:
            logger.warning("could not remove {}: {}", p.name, type(e).__name__)
    return n


def _crop_files(faces: list[Face], settings: Settings) -> list[Path]:
    base = settings.data_dir / "crops"
    out: list[Path] = []
    for f in faces:
        p = contained(f.crop_path, base)
        if p is not None:
            out.append(p)
    return out


# --- build -------------------------------------------------------------------------------


@dataclass
class BuildResult:
    candidates: int = 0
    clusters: int = 0
    clustered: int = 0
    noise: int = 0


def _candidates(s: Session, now: datetime) -> list[Face]:
    """Unmatched tier >= M faces of the last 60 days that are free or in an unlabeled cluster."""
    unlabeled = select(Cluster.id).where(Cluster.label_status == "unlabeled")
    matched = exists().where(Match.face_id == Face.id, Match.decision.in_(ACTIVE_MATCH))
    q = (
        select(Face)
        .where(
            Face.embedding.is_not(None),
            Face.size_px >= MIN_TIER_SIZE_PX,
            Face.created_at >= now - timedelta(days=CANDIDATE_DAYS),
            (Face.cluster_id.is_(None)) | (Face.cluster_id.in_(unlabeled)),
            ~matched,
        )
        .order_by(Face.id)
    )
    return list(s.scalars(q))


def build_clusters(session: Session, now: datetime | None = None) -> BuildResult:
    """Rebuild the unlabeled clusters from scratch (idempotent; labeled ones are untouched)."""
    now = now or _utcnow()
    unlabeled = select(Cluster.id).where(Cluster.label_status == "unlabeled")
    # Release first so faces that were matched or aged out meanwhile end up cluster-free.
    session.execute(
        update(Face).where(Face.cluster_id.in_(unlabeled)).values(cluster_id=None),
        execution_options={"synchronize_session": False},
    )
    session.execute(delete(Cluster).where(Cluster.label_status == "unlabeled"))
    session.expire_all()
    faces = _candidates(session, now)
    res = BuildResult(candidates=len(faces))
    if not faces:
        return res
    emb = np.stack([blob_to_embedding(f.embedding) for f in faces if f.embedding is not None])
    labels = clustering.cluster_embeddings(emb)
    groups: dict[int, list[Face]] = {}
    for f, lab in zip(faces, labels.tolist(), strict=True):
        if lab != clustering.NOISE:
            groups.setdefault(int(lab), []).append(f)
    for members in groups.values():
        c = Cluster(size=len(members), label_status="unlabeled", created_at=now)
        session.add(c)
        session.flush()
        for f in members:
            f.cluster_id = c.id
    res.clusters = len(groups)
    res.clustered = sum(len(m) for m in groups.values())
    res.noise = res.candidates - res.clustered
    return res


def run_clustering(now: datetime | None = None) -> BuildResult:
    """Own-session entry point for the huey task, the endpoint and tests."""
    with get_session() as s:
        res = build_clusters(s, now)
    logger.info("clustering: {}", vars(res))
    return res


# --- read --------------------------------------------------------------------------------


@dataclass
class ClusterCard:
    cluster: Cluster
    sample_face_ids: list[int]
    groups: list[str]
    date_from: datetime | None
    date_to: datetime | None


@dataclass
class ClusterPage:
    items: list[ClusterCard]
    next_cursor: str | None


@dataclass
class FaceRow:
    face: Face
    group_name: str | None
    sent_at: datetime | None


def _cursor(cursor: str | None) -> tuple[int, int] | None:
    if not cursor:
        return None
    try:
        a, b = cursor.split("_", 1)
        return int(a), int(b)
    except ValueError:
        raise InvalidError("invalid cursor") from None


def _card(s: Session, c: Cluster) -> ClusterCard:
    samples = list(
        s.scalars(
            select(Face.id)
            .where(Face.cluster_id == c.id, Face.crop_path.is_not(None))
            .order_by(func.coalesce(Face.quality, 0).desc(), Face.id)
            .limit(SAMPLES_PER_CARD)
        )
    )
    names = list(
        s.scalars(
            select(Group.name)
            .join(ImageRow, ImageRow.group_id == Group.id)
            .join(Face, Face.image_id == ImageRow.id)
            .where(Face.cluster_id == c.id)
            .distinct()
            .order_by(Group.name)
        )
    )
    d = photo_date()
    lo, hi = s.execute(
        select(func.min(d), func.max(d))
        .select_from(Face)
        .join(ImageRow, ImageRow.id == Face.image_id)
        .where(Face.cluster_id == c.id)
    ).one()
    return ClusterCard(c, samples, [n for n in names if n], lo, hi)


def list_clusters(
    s: Session, status: str = "unlabeled", cursor: str | None = None, limit: int = 50
) -> ClusterPage:
    """Largest first; ``cursor`` is ``<size>_<id>`` of the last item of the previous page."""
    if status not in CLUSTER_LABEL:
        raise InvalidError(f"status must be one of {', '.join(CLUSTER_LABEL)}")
    q = select(Cluster).where(Cluster.label_status == status)
    cur = _cursor(cursor)
    if cur is not None:
        size, cid = cur
        q = q.where((Cluster.size < size) | ((Cluster.size == size) & (Cluster.id < cid)))
    rows = list(s.scalars(q.order_by(Cluster.size.desc(), Cluster.id.desc()).limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    nxt = f"{rows[-1].size}_{rows[-1].id}" if more and rows else None
    return ClusterPage([_card(s, c) for c in rows], nxt)


def get_cluster(s: Session, cluster_id: int) -> Cluster:
    c = s.get(Cluster, cluster_id)
    if c is None:
        raise NotFoundError(f"cluster {cluster_id} not found")
    return c


def cluster_faces(
    s: Session, cluster_id: int, cursor: int | None = None, limit: int = 200
) -> tuple[list[FaceRow], int | None]:
    """Faces of a cluster, best quality first is a client concern; paged by face id."""
    get_cluster(s, cluster_id)
    q = (
        select(Face, Group.name, photo_date())
        .join(ImageRow, ImageRow.id == Face.image_id)
        .outerjoin(Group, Group.id == ImageRow.group_id)
        .where(Face.cluster_id == cluster_id)
    )
    if cursor is not None:
        q = q.where(Face.id > cursor)
    rows = s.execute(q.order_by(Face.id).limit(limit + 1)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    out = [FaceRow(f, g, d) for f, g, d in rows]
    return out, (out[-1].face.id if more and out else None)


# --- label -------------------------------------------------------------------------------


@dataclass
class LabelResult:
    cluster_id: int
    kid_id: int
    selected: int
    references_created: int
    duplicates: int
    rescore: RescoreResult | None = field(default=None)


def label_cluster(
    settings: Settings, cluster_id: int, kid_id: int, face_ids: list[int] | None = None
) -> LabelResult:
    """ "This is <kid>": the best 15 usable selected faces become ``source=cluster`` references.

    The label commits first; the re-score of the clustering window then runs on the new
    gallery so the cluster's remaining faces (and look-alikes) surface as matches. Faces
    deselected by the user are released for the next clustering run.
    """
    with get_session() as s:
        c = get_cluster(s, cluster_id)
        if c.label_status != "unlabeled":
            raise ConflictError(f"cluster {cluster_id} is already {c.label_status}")
        kid = s.get(Kid, kid_id)
        if kid is None:
            raise NotFoundError(f"kid {kid_id} not found")
        if not kid.active:
            raise ConflictError("kid is not active")
        faces = list(s.scalars(select(Face).where(Face.cluster_id == c.id).order_by(Face.id)))
        by_id = {f.id: f for f in faces}
        picked = faces
        if face_ids is not None:
            wanted = list(dict.fromkeys(face_ids))
            if not wanted or any(i not in by_id for i in wanted):
                raise InvalidError("face_ids must be a non-empty subset of the cluster's faces")
            picked = [by_id[i] for i in wanted]
        usable = [
            f
            for f in picked
            if f.embedding is not None
            and unusable_reason(
                tier_for(f.size_px) if f.size_px else Tier.XS,
                f.quality or 0.0,
                f.blur_score or 0.0,
                f.yaw or 0.0,
                f.pitch or 0.0,
            )
            is None
        ]
        if not usable:
            raise InvalidError("none of the selected faces is usable as a reference")
        best = sorted(usable, key=lambda f: (-(f.quality or 0.0), f.id))[:MAX_LABEL_REFERENCES]
        added = add_references(s, kid.id, settings, face_ids=[f.id for f in best], source="cluster")
        keep = {f.id for f in picked}
        for f in faces:
            if f.id not in keep:
                f.cluster_id = None
        c.label_status = "labeled"
        c.kid_id = kid.id
        c.size = len(keep)
        res = LabelResult(c.id, kid.id, len(keep), len(added.created), added.duplicates)
    try:
        res.rescore = rescore(settings, CANDIDATE_DAYS)
    except Exception as e:  # the label is saved; "Re-score" can be run again from the UI
        logger.error("re-score after labeling cluster {} failed: {}", cluster_id, type(e).__name__)
    return res


# --- ignore ------------------------------------------------------------------------------


def ignore_cluster(settings: Settings, cluster_id: int) -> tuple[int, int]:
    """Delete the cluster's faces (embeddings, rows, matches) and crop files immediately.

    Returns ``(faces_deleted, files_removed)``. The cluster row goes too, so nothing about
    these faces remains in the DB or on disk (§13, §19).
    """
    with get_session() as s:
        c = get_cluster(s, cluster_id)
        if c.label_status != "unlabeled":
            raise ConflictError(f"cluster {cluster_id} is already {c.label_status}")
        faces = list(s.scalars(select(Face).where(Face.cluster_id == c.id)))
        files = _crop_files(faces, settings)
        ids = [f.id for f in faces]
        if ids:
            s.execute(delete(Match).where(Match.face_id.in_(ids)))
            s.execute(delete(Face).where(Face.id.in_(ids)))
        s.delete(c)
    removed = _remove_files(files)  # only after the commit
    logger.info("ignored cluster {}: {} face(s) deleted", cluster_id, len(ids))
    return len(ids), removed


# --- split -------------------------------------------------------------------------------


def split_cluster(session: Session, cluster_id: int) -> list[int]:
    """Re-cluster one unlabeled cluster more tightly; returns the new cluster ids.

    Raises ConflictError when it would not split into at least two clusters. Faces that fit
    no sub-cluster become ordinary unclustered faces.
    """
    c = get_cluster(session, cluster_id)
    if c.label_status != "unlabeled":
        raise ConflictError(f"cluster {cluster_id} is already {c.label_status}")
    faces = [
        f
        for f in session.scalars(select(Face).where(Face.cluster_id == c.id).order_by(Face.id))
        if f.embedding is not None
    ]
    if len(faces) < 2 * clustering.SPLIT_MIN_CLUSTER_SIZE:
        raise ConflictError("cannot split further")
    labels = clustering.split_embeddings(np.stack([blob_to_embedding(f.embedding) for f in faces]))  # type: ignore[arg-type]
    groups: dict[int, list[Face]] = {}
    for f, lab in zip(faces, labels.tolist(), strict=True):
        if lab != clustering.NOISE:
            groups.setdefault(int(lab), []).append(f)
    if len(groups) < 2:
        raise ConflictError("cannot split further")
    for f in session.scalars(select(Face).where(Face.cluster_id == c.id)):
        f.cluster_id = None
    session.flush()
    new_ids: list[int] = []
    for members in groups.values():
        n = Cluster(size=len(members), label_status="unlabeled", created_at=_utcnow())
        session.add(n)
        session.flush()
        for f in members:
            f.cluster_id = n.id
        new_ids.append(n.id)
    session.delete(c)
    return new_ids


__all__ = [
    "BuildResult",
    "ClusterCard",
    "ClusterPage",
    "FaceRow",
    "LabelResult",
    "build_clusters",
    "cluster_faces",
    "get_cluster",
    "ignore_cluster",
    "label_cluster",
    "list_clusters",
    "run_clustering",
    "split_cluster",
]
