"""Engine/session management, migrations and embedding BLOB helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
from alembic import command
from alembic.config import Config
from filelock import FileLock
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None

EMBEDDING_DIM = 512


def default_url() -> str:
    from kino.config import get_settings

    return f"sqlite:///{get_settings().data_dir / 'kino.db'}"


def embedding_to_blob(emb: np.ndarray) -> bytes:
    return np.ascontiguousarray(emb, dtype=np.float32).tobytes()


def blob_to_embedding(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32).copy()


def _set_pragmas(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def init_engine(url: str | None = None) -> Engine:
    """(Re)initialise the global engine; every connection gets WAL + foreign_keys=ON."""
    global _engine, _factory
    url = url or default_url()
    if _engine is not None:
        _engine.dispose()
    parsed = make_url(url)
    if parsed.database and parsed.database != ":memory:":
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"timeout": 30})
    event.listen(engine, "connect", _set_pragmas)
    _engine = engine
    _factory = sessionmaker(engine, expire_on_commit=False)
    return engine


def get_engine() -> Engine:
    return _engine if _engine is not None else init_engine()


@contextmanager
def get_session() -> Iterator[Session]:
    """Commit on success, roll back on error."""
    get_engine()
    assert _factory is not None
    s = _factory()
    try:
        yield s
        s.commit()
    except BaseException:
        s.rollback()
        raise
    finally:
        s.close()


def run_migrations() -> None:
    """Upgrade to head, serialised across processes with a lock file next to the DB."""
    engine = get_engine()
    db = engine.url.database
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "alembic"))
    cfg.attributes["engine"] = engine
    lock_path = f"{db}.migrate.lock" if db and db != ":memory:" else None
    if lock_path is None:
        command.upgrade(cfg, "head")
        return
    with FileLock(lock_path, timeout=120):
        command.upgrade(cfg, "head")
