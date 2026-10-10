import numpy as np
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from kino.db import models, session

EXPECTED_TABLES = {
    "kids", "groups", "kid_groups", "images", "faces", "matches", "reference_faces",
    "negative_faces", "clusters", "saved_photos", "forwards", "digests", "settings",
}  # fmt: skip


@pytest.fixture
def engine(tmp_path):
    eng = session.init_engine(f"sqlite:///{tmp_path / 'kino.db'}")
    yield eng
    eng.dispose()


def test_create_all_tables(engine):
    models.Base.metadata.create_all(engine)
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
    assert "persons" not in inspect(engine).get_table_names()


def test_blob_roundtrip():
    emb = np.random.default_rng(0).standard_normal(512).astype(np.float32)
    blob = embedding_blob = session.embedding_to_blob(emb)
    assert isinstance(embedding_blob, bytes) and len(blob) == 512 * 4
    out = session.blob_to_embedding(blob)
    assert out.dtype == np.float32
    assert np.array_equal(out, emb)


def test_message_id_unique(engine):
    models.Base.metadata.create_all(engine)
    with session.get_session() as s:
        s.add(models.Image(message_id="m1", raw_path="/a", status="queued"))
    with pytest.raises(IntegrityError), session.get_session() as s:
        s.add(models.Image(message_id="m1", raw_path="/b", status="queued"))


def test_pragmas(engine):
    with engine.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_session_rollback_on_error(engine):
    models.Base.metadata.create_all(engine)
    with pytest.raises(RuntimeError), session.get_session() as s:
        s.add(models.Kid(display_name="A", slug="a"))
        s.flush()
        raise RuntimeError
    with session.get_session() as s:
        assert s.query(models.Kid).count() == 0


def test_enum_rejects_invalid(engine):
    models.Base.metadata.create_all(engine)
    with pytest.raises(IntegrityError), session.get_session() as s:
        s.add(models.Image(message_id="x", raw_path="/a", status="bogus"))


def test_indexes_present(engine):
    models.Base.metadata.create_all(engine)
    insp = inspect(engine)
    cols = {tuple(i["column_names"]) for i in insp.get_indexes("images")}
    assert ("group_id", "sent_at") in cols
    assert ("image_id",) in {tuple(i["column_names"]) for i in insp.get_indexes("faces")}
    assert ("decision",) in {tuple(i["column_names"]) for i in insp.get_indexes("matches")}
    assert ("kid_id", "status") in {
        tuple(i["column_names"]) for i in insp.get_indexes("reference_faces")
    }


def test_run_migrations_matches_models(tmp_path):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    url = f"sqlite:///{tmp_path / 'm.db'}"
    eng = session.init_engine(url)
    session.run_migrations()
    assert "kids" in inspect(eng).get_table_names()
    with eng.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c), models.Base.metadata)
    assert diff == []
    session.run_migrations()  # idempotent
    eng.dispose()
