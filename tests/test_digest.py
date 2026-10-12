"""Review digest: cron matching, change detection, fake OpenWA client only."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from kino import digest
from kino.config import Settings
from kino.db import session as dbs
from kino.db.models import Digest, Face, Image, Kid, Match
from kino.services import settings_svc

CHAT = "9725012345678@c.us"


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    async def send_text(self, chat_id: str, text: str) -> str:
        if self.fail:
            raise RuntimeError(f"boom for {chat_id}")
        self.sent.append((chat_id, text))
        return "id"


@pytest.fixture
def env(tmp_path):
    eng = dbs.init_engine(f"sqlite:///{tmp_path / 'd.db'}")
    dbs.run_migrations()
    st = Settings(data_dir=tmp_path, photos_dir=tmp_path / "photos", digest_cron="0 20 * * *")
    with dbs.get_session() as s:
        s.add_all(
            [Kid(id=1, display_name="Noa", slug="noa"), Kid(id=2, display_name="Itai", slug="i")]
        )
        settings_svc.update_digest(
            s, st, chat_id=CHAT, cron="0 20 * * *", apprise_urls=None, public_base_url=None
        )
    yield st
    eng.dispose()


def add_pending(n: int, kid_id: int) -> None:
    with dbs.get_session() as s:
        s.add(Image(id=n, message_id=f"m{n}"))
        s.flush()
        s.add(
            Face(
                id=n, image_id=n, bbox_x=0, bbox_y=0, bbox_w=1, bbox_h=1, det_score=0.9, size_px=50
            )
        )
        s.flush()
        s.add(Match(id=n, face_id=n, kid_id=kid_id, score=0.4, decision="review"))


def test_cron_matches():
    t = datetime(2026, 10, 12, 20, 0)  # a Monday
    assert digest.cron_matches("0 20 * * *", t)
    assert not digest.cron_matches("5 20 * * *", t)
    assert digest.cron_matches("*/15 18-21 * * 1", t)
    assert not digest.cron_matches("0 20 * * 0", t)
    assert digest.cron_matches("0 20 * * 7", datetime(2026, 10, 11, 20, 0))  # Sunday
    assert not digest.cron_matches("garbage", t)
    assert not digest.cron_matches("61 * * * *", t)


def test_sends_once_until_pending_changes(env):
    fake = FakeClient()
    factory = lambda _st: fake  # noqa: E731
    assert not digest.run_digest(env, client_factory=factory)  # nothing pending
    add_pending(1, 1)
    add_pending(2, 1)
    add_pending(3, 2)
    assert digest.run_digest(env, client_factory=factory)
    assert fake.sent == [(CHAT, "3 photos waiting for review (Noa 2, Itai 1)")]
    assert not digest.run_digest(env, client_factory=factory)  # unchanged
    assert len(fake.sent) == 1
    add_pending(4, 2)
    assert digest.run_digest(env, client_factory=factory)
    assert fake.sent[-1][1].startswith("4 photos")
    with dbs.get_session() as s:
        assert [d.pending_count for d in s.scalars(select(Digest).order_by(Digest.id))] == [3, 4]


def test_failure_is_not_recorded_and_is_sanitised(env, caplog):
    add_pending(1, 1)
    bad = FakeClient(fail=True)
    assert not digest.run_digest(env, client_factory=lambda _s: bad)
    with dbs.get_session() as s:
        assert s.scalar(select(Digest.id)) is None
    good = FakeClient()
    assert digest.run_digest(env, client_factory=lambda _s: good)
    assert good.sent[0][1] == "1 photo waiting for review (Noa 1)"


def test_no_destination_and_link(env):
    add_pending(1, 1)
    fake = FakeClient()
    with dbs.get_session() as s:
        settings_svc.update_digest(
            s, env, chat_id=None, cron="0 20 * * *", apprise_urls=None, public_base_url=None
        )
    assert not digest.run_digest(env, client_factory=lambda _s: fake)
    with dbs.get_session() as s:
        settings_svc.update_digest(
            s,
            env,
            chat_id=CHAT,
            cron="0 20 * * *",
            apprise_urls=None,
            public_base_url="https://kino.example.com/",
        )
    assert digest.run_digest(env, client_factory=lambda _s: fake)
    assert fake.sent[0][1].endswith("(Noa 1) · https://kino.example.com/review")


def test_run_if_due_honours_cron_and_timezone(env):
    add_pending(1, 1)
    fake = FakeClient()
    f = lambda _s: fake  # noqa: E731
    env_tz = env.model_copy(update={"timezone": "Asia/Jerusalem"})  # UTC+3 in October
    off = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)  # 23:00 local
    assert not digest.run_digest_if_due(off, env_tz, client_factory=f)
    on = datetime(2026, 10, 12, 17, 0, tzinfo=UTC)  # 20:00 local
    assert digest.run_digest_if_due(on, env_tz, client_factory=f)
    with dbs.get_session() as s:
        settings_svc.update_digest(
            s, env, chat_id=CHAT, cron="", apprise_urls=None, public_base_url=None
        )
    add_pending(2, 1)
    assert not digest.run_digest_if_due(on, env_tz, client_factory=f)  # empty cron disables
