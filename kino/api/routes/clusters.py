"""/api/clusters: the "Who is this?" page (CLAUDE.md §11.3, §12, §13)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query, Request

from kino.api.deps import protected_router
from kino.api.schemas import (
    ClusterCardOut,
    ClusterDetailOut,
    ClusterFaceOut,
    ClusterIgnoreOut,
    ClusterLabelIn,
    ClusterLabelOut,
    ClusterPageOut,
    ClusterRunOut,
    ClusterSplitOut,
    RescoreOut,
)
from kino.config import Settings
from kino.db.session import get_session
from kino.services import clusters as svc
from kino.services.images import mask_ids

router = protected_router(prefix="/api/clusters", tags=["clusters"])


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _card(c: svc.ClusterCard) -> ClusterCardOut:
    return ClusterCardOut(
        id=c.cluster.id,
        size=c.cluster.size,
        label_status=c.cluster.label_status,
        kid_id=c.cluster.kid_id,
        sample_crop_urls=[f"/api/crops/{i}" for i in c.sample_face_ids],
        groups=[mask_ids(g) or "" for g in c.groups],
        date_from=c.date_from,
        date_to=c.date_to,
    )


@router.get("", response_model=ClusterPageOut)
def list_clusters(
    status: str = "unlabeled",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ClusterPageOut:
    with get_session() as s:
        page = svc.list_clusters(s, status, cursor, limit)
        return ClusterPageOut(items=[_card(c) for c in page.items], next_cursor=page.next_cursor)


@router.post("/run", response_model=ClusterRunOut, status_code=202)
def run_clusters() -> ClusterRunOut:
    from kino import tasks

    job = tasks.cluster_unknown_faces_task()
    return ClusterRunOut(status="queued", job_id=getattr(job, "id", None))


@router.get("/{cluster_id}", response_model=ClusterDetailOut)
def get_cluster(
    cluster_id: int,
    cursor: int | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> ClusterDetailOut:
    with get_session() as s:
        c = svc.get_cluster(s, cluster_id)
        rows, nxt = svc.cluster_faces(s, cluster_id, cursor, limit)
        faces = [
            ClusterFaceOut(
                face_id=r.face.id,
                crop_url=f"/api/crops/{r.face.id}" if r.face.crop_path else None,
                quality=r.face.quality,
                size_px=r.face.size_px,
                group_name=mask_ids(r.group_name),
                sent_at=r.sent_at,
            )
            for r in rows
        ]
        return ClusterDetailOut(
            id=c.id,
            size=c.size,
            label_status=c.label_status,
            kid_id=c.kid_id,
            faces=faces,
            next_cursor=nxt,
        )


@router.post("/{cluster_id}/label", response_model=ClusterLabelOut)
def label(request: Request, cluster_id: int, body: ClusterLabelIn) -> ClusterLabelOut:
    r = svc.label_cluster(_settings(request), cluster_id, body.kid_id, body.face_ids)
    return ClusterLabelOut(
        cluster_id=r.cluster_id,
        kid_id=r.kid_id,
        selected=r.selected,
        references_created=r.references_created,
        duplicates=r.duplicates,
        skipped=r.skipped,
        rescore=RescoreOut(**vars(r.rescore)) if r.rescore is not None else None,
    )


@router.post("/{cluster_id}/ignore", response_model=ClusterIgnoreOut)
def ignore(request: Request, cluster_id: int) -> ClusterIgnoreOut:
    deleted, removed, skipped = svc.ignore_cluster(_settings(request), cluster_id)
    return ClusterIgnoreOut(faces_deleted=deleted, files_removed=removed, faces_skipped=skipped)


@router.post("/{cluster_id}/split", response_model=ClusterSplitOut)
def split(cluster_id: int) -> ClusterSplitOut:
    with get_session() as s:
        return ClusterSplitOut(cluster_ids=svc.split_cluster(s, cluster_id))


__all__ = ["router"]
