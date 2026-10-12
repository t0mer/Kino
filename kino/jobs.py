"""Generic background-job rows: create, update progress, read (CLAUDE.md section 12).

Error text is stored as a short sanitised label (exception class name), never a message that
could carry URLs, ids or response bodies.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from kino.db.models import Job
from kino.db.session import get_session
from kino.services import ConflictError

ACTIVE = ("queued", "running")


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def create_job(
    kind: str,
    params: dict[str, Any],
    *,
    subject_id: int | None = None,
    exclusive: bool = False,
) -> int:
    """Insert a queued job. With ``exclusive``, refuse (ConflictError) when a queued/running
    job of the same kind and subject exists."""
    with get_session() as s:
        if exclusive:
            busy = s.scalar(
                select(Job.id).where(
                    Job.kind == kind, Job.subject_id == subject_id, Job.status.in_(ACTIVE)
                )
            )
            if busy is not None:
                raise ConflictError(f"a {kind} job is already queued or running")
        job = Job(kind=kind, subject_id=subject_id, params=params)
        s.add(job)
        s.flush()
        return job.id


def start_job(job_id: int, total: int = 0) -> None:
    update_job(job_id, status="running", started_at=_utcnow(), total=total)


def update_job(job_id: int, **values: Any) -> None:
    with get_session() as s:
        job = s.get(Job, job_id)
        if job is not None:
            for k, v in values.items():
                setattr(job, k, v)


def finish_job(job_id: int, error: BaseException | None = None) -> None:
    update_job(
        job_id,
        status="failed" if error is not None else "done",
        error=type(error).__name__ if error is not None else None,
        finished_at=_utcnow(),
    )


def get_job(job_id: int) -> Job | None:
    with get_session() as s:
        job = s.get(Job, job_id)
        if job is not None:
            s.expunge(job)
        return job


def list_jobs(kind: str | None = None, limit: int = 20) -> list[Job]:
    with get_session() as s:
        q = select(Job).order_by(Job.id.desc()).limit(limit)
        if kind:
            q = q.where(Job.kind == kind)
        rows = list(s.scalars(q))
        s.expunge_all()
        return rows
