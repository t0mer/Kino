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
