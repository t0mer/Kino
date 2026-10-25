from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from kino.db import session


def _cfg(eng):
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(session.__file__).parent / "alembic"))
    cfg.attributes["engine"] = eng
    return cfg


@pytest.fixture
def eng(tmp_path):
    e = session.init_engine(f"sqlite:///{tmp_path / 'm.db'}")
    yield e
    e.dispose()


def _seed_image(c):
    c.execute(
        text(
            "INSERT INTO images (id, message_id, status, received_at) "
            "VALUES (1, 'm1', 'done', '2026-01-01 00:00:00')"
        )
    )


def test_upgrade_backfills_existing_rows(eng):
    cfg = _cfg(eng)
    command.upgrade(cfg, "0009_reference_outlier")
    with eng.begin() as c:
        _seed_image(c)
        c.execute(
            text(
                "INSERT INTO forwards (image_id, dest_chat_id, kid_ids, status, attempts, "
                "openwa_message_id, created_at) VALUES (1, 'c1', '[]', 'sent', 1, "
                "'wa-123', '2026-01-01 00:00:00')"
            )
        )
        c.execute(
            text(
                "INSERT INTO digests (sent_at, pending_count, dest_chat_id) "
                "VALUES ('2026-01-01 00:00:00', 2, 'c1')"
            )
        )
    command.upgrade(cfg, "head")
    with eng.connect() as c:
        row = c.execute(text("SELECT channel, sent_ref FROM forwards")).one()
        assert tuple(row) == ("whatsapp", "wa-123")
        assert c.execute(text("SELECT channel FROM digests")).scalar_one() == "whatsapp"
    assert "telegram_chats" in inspect(eng).get_table_names()


def test_unique_per_channel(eng):
    command.upgrade(_cfg(eng), "head")
    ins = text(
        "INSERT INTO forwards (image_id, channel, dest_chat_id, kid_ids, status, attempts, "
        "created_at) VALUES (1, :ch, 'c1', '[]', 'pending', 0, '2026-01-01 00:00:00')"
    )
    with eng.begin() as c:
        _seed_image(c)
        c.execute(ins, {"ch": "whatsapp"})
        c.execute(ins, {"ch": "telegram"})
    with pytest.raises(IntegrityError), eng.begin() as c:
        c.execute(ins, {"ch": "telegram"})


def test_downgrade_upgrade_round_trip(eng):
    cfg = _cfg(eng)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0009_reference_outlier")
    insp = inspect(eng)
    assert "telegram_chats" not in insp.get_table_names()
    assert "channel" not in {c["name"] for c in insp.get_columns("forwards")}
    command.upgrade(cfg, "head")
    assert "channel" in {c["name"] for c in inspect(eng).get_columns("forwards")}
