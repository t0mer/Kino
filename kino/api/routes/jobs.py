"""/api/jobs: progress of background jobs such as group backfill (CLAUDE.md section 12)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query
from fastapi.concurrency import run_in_threadpool

from kino import jobs
from kino.api.deps import protected_router
from kino.api.schemas import JobOut
from kino.db.models import Job
from kino.services import NotFoundError

router = protected_router(prefix="/api/jobs", tags=["jobs"])


def _out(j: Job) -> JobOut:
    return JobOut(
        id=j.id,
        kind=j.kind,
        status=j.status,
        group_id=j.subject_id,
        params=j.params,
        total=j.total,
        processed=j.processed,
        skipped=j.skipped,
        errors=j.errors,
        error=j.error,
        created_at=j.created_at,
        started_at=j.started_at,
        finished_at=j.finished_at,
    )


@router.get("", response_model=list[JobOut])
async def list_jobs(
    kind: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[JobOut]:
    return [_out(j) for j in await run_in_threadpool(jobs.list_jobs, kind, limit)]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: int) -> JobOut:
    job = await run_in_threadpool(jobs.get_job, job_id)
    if job is None:
        raise NotFoundError("job not found")
    return _out(job)
