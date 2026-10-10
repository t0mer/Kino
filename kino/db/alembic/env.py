"""Alembic environment: uses the engine passed by kino.db.session.run_migrations."""

from alembic import context

from kino.db.models import Base
from kino.db.session import get_engine

target_metadata = Base.metadata


def run_migrations_online() -> None:
    engine = context.config.attributes.get("engine") or get_engine()
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
