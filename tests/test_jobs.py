from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from kino import jobs
from kino.db import models, session
from kino.services import ConflictError


@pytest.fixture
def db(tmp_path):
    eng = session.init_engine(f"sqlite:///{tmp_path / 'j.db'}")
    session.run_migrations()
    yield eng
    eng.dispose()


def test_migration_matches_models(db):
    assert "jobs" in inspect(db).get_table_names()
    with db.connect() as c:
        assert compare_metadata(MigrationContext.configure(c), models.Base.metadata) == []


def test_migration_down_and_up(tmp_path):
    eng = session.init_engine(f"sqlite:///{tmp_path / 'd.db'}")
    session.run_migrations()
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(session.__file__).parent / "alembic"))
    cfg.attributes["engine"] = eng
    command.downgrade(cfg, "0006_temp_faces")
    assert "jobs" not in inspect(eng).get_table_names()
    command.upgrade(cfg, "head")
    assert "jobs" in inspect(eng).get_table_names()
    eng.dispose()


def test_lifecycle_and_exclusive(db):
    jid = jobs.create_job("backfill", {"days": 3}, subject_id=7, exclusive=True)
    assert jobs.get_job(jid).status == "queued"
    with pytest.raises(ConflictError):
        jobs.create_job("backfill", {"days": 3}, subject_id=7, exclusive=True)
    assert jobs.create_job("backfill", {}, subject_id=8, exclusive=True) != jid
    jobs.start_job(jid, total=5)
    jobs.update_job(jid, processed=2, skipped=1)
    j = jobs.get_job(jid)
    assert (j.status, j.total, j.processed, j.skipped) == ("running", 5, 2, 1)
    assert j.started_at is not None
    jobs.finish_job(jid, ValueError("secret 9725551234567"))
    j = jobs.get_job(jid)
    assert j.status == "failed" and j.error == "ValueError" and j.finished_at is not None
    # finished job no longer blocks a new one
    assert jobs.create_job("backfill", {}, subject_id=7, exclusive=True)
    assert [x.kind for x in jobs.list_jobs("backfill", 2)] == ["backfill", "backfill"]
    assert jobs.get_job(9999) is None


def test_stale_active_job_stops_blocking(db, monkeypatch):
    from datetime import datetime, timedelta

    now = datetime(2026, 10, 12, 12, 0)
    monkeypatch.setattr(jobs, "_utcnow", lambda: now)
    old = jobs.create_job("backfill", {}, subject_id=3, exclusive=True)
    jobs.update_job(old, status="running", started_at=now - timedelta(hours=5))
    with pytest.raises(ConflictError):  # 5h: still plausibly alive
        jobs.create_job("backfill", {}, subject_id=3, exclusive=True)
    jobs.update_job(old, started_at=now - timedelta(hours=7))
    new = jobs.create_job("backfill", {}, subject_id=3, exclusive=True)
    assert new != old
    j = jobs.get_job(old)
    assert (j.status, j.error, j.finished_at) == ("failed", "interrupted", now)
    # a never-started queued job is aged by created_at
    q = jobs.create_job("backfill", {}, subject_id=4, exclusive=True)
    jobs.update_job(q, created_at=now)  # created_at defaults to the real clock, pin it
    monkeypatch.setattr(jobs, "_utcnow", lambda: now + timedelta(hours=7))
    assert jobs.create_job("backfill", {}, subject_id=4, exclusive=True) != q


def test_worker_start_fails_running_jobs_only(db):
    running = jobs.create_job("backfill", {}, subject_id=1)
    jobs.start_job(running)
    queued = jobs.create_job("backfill", {}, subject_id=2)
    done = jobs.create_job("backfill", {}, subject_id=5)
    jobs.finish_job(done)
    assert jobs.fail_interrupted_jobs() == 1
    assert jobs.get_job(running).status == "failed"
    assert jobs.get_job(running).error == "interrupted"
    assert jobs.get_job(queued).status == "queued"
    assert jobs.get_job(done).status == "done"
    jobs.create_job("backfill", {}, subject_id=1, exclusive=True)  # unblocked


def test_worker_startup_hook_runs_cleanup(db, monkeypatch):
    from kino import tasks

    jid = jobs.create_job("backfill", {}, subject_id=1)
    jobs.start_job(jid)
    monkeypatch.setattr(tasks, "get_pipeline", lambda: None)
    tasks._warm_pipeline()
    assert jobs.get_job(jid).status == "failed"
