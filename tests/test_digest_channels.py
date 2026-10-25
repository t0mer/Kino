"""Channel-aware review digest (WhatsApp default, Telegram)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from kino import digest, metrics
from kino.db import session as dbs
from kino.db.models import Digest, TelegramChat
from kino.delivery.base import SentRef
from kino.services import InvalidError, settings_svc
from tests.test_digest import CHAT, FakeClient, add_pending, env  # noqa: F401

TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
TG = "-100123"


class FakeChannel:
    def __init__(self, exc: Exception | None = None) -> None:
        self.sent: list[tuple[str, str]] = []
        self.exc = exc

    async def send_text(self, dest: str, text: str) -> SentRef:
        if self.exc:
            raise self.exc
        self.sent.append((dest, text))
        return SentRef("1")

    async def aclose(self) -> None:
        pass


@pytest.fixture
def tg_env(env):  # noqa: F811
    st = env.model_copy(update={"telegram_bot_token": SecretStr(TOKEN)})
    with dbs.get_session() as s:
        s.add(TelegramChat(chat_id=TG, title="T", type="group"))
        s.flush()
        settings_svc.update_digest(
            s, st, chat_id=TG, cron="0 20 * * *", apprise_urls=None, public_base_url=None,
            channel="telegram",
        )  # fmt: skip
    metrics.reset()
    return st


def test_update_digest_validation(env):  # noqa: F811
    kw = dict(cron="", apprise_urls=None, public_base_url=None)
    with dbs.get_session() as s:
        with pytest.raises(InvalidError):
            settings_svc.update_digest(s, env, chat_id=CHAT, channel="bogus", **kw)
        with pytest.raises(InvalidError, match="token"):
            settings_svc.update_digest(s, env, chat_id=TG, channel="telegram", **kw)
        st = env.model_copy(update={"telegram_bot_token": SecretStr(TOKEN)})
        with pytest.raises(InvalidError, match="not linked"):
            settings_svc.update_digest(s, st, chat_id=TG, channel="telegram", **kw)
        assert settings_svc.get_digest(s, env).channel == "whatsapp"


def test_unknown_stored_channel_reads_as_whatsapp(env):  # noqa: F811
    with dbs.get_session() as s:
        s.merge(__import__("kino.db.models", fromlist=["Setting"]).Setting(
            key="digest", value={"chat_id": CHAT, "cron": "", "channel": "carrier-pigeon"}
        ))  # fmt: skip
        s.flush()
        assert settings_svc.get_digest(s, env).channel == "whatsapp"


def test_telegram_digest_sent_and_recorded(tg_env):
    add_pending(1, 1)
    fake = FakeChannel()
    names: list[str] = []

    def factory(name: str) -> FakeChannel:
        names.append(name)
        return fake

    assert digest.run_digest(tg_env, channel_factory=factory)
    assert names == ["telegram"]
    assert fake.sent == [(TG, "1 photo waiting for review (Noa 1)")]
    with dbs.get_session() as s:
        row = s.scalars(select(Digest)).one()
        assert (row.channel, row.dest_chat_id) == ("telegram", TG)
    assert metrics.digests_sent.labels(channel="telegram")._value.get() == 1
    assert not digest.run_digest(tg_env, channel_factory=factory)  # unchanged, deduped


def test_changing_channel_resends_same_hash(env):  # noqa: F811
    add_pending(1, 1)
    wa = FakeClient()
    assert digest.run_digest(env, client_factory=lambda _s: wa)
    assert not digest.run_digest(env, client_factory=lambda _s: wa)
    st = env.model_copy(update={"telegram_bot_token": SecretStr(TOKEN)})
    with dbs.get_session() as s:
        s.add(TelegramChat(chat_id=TG, title="T", type="group"))
        s.flush()
        settings_svc.update_digest(
            s, st, chat_id=TG, cron="", apprise_urls=None, public_base_url=None,
            channel="telegram",
        )  # fmt: skip
    fake = FakeChannel()
    assert digest.run_digest(st, channel_factory=lambda _n: fake)
    assert len(fake.sent) == 1 and len(wa.sent) == 1
    with dbs.get_session() as s:
        assert [d.channel for d in s.scalars(select(Digest).order_by(Digest.id))] == [
            "whatsapp",
            "telegram",
        ]


def test_whatsapp_metric_label(env):  # noqa: F811
    metrics.reset()
    add_pending(1, 1)
    assert digest.run_digest(env, client_factory=lambda _s: FakeClient())
    assert metrics.digests_sent.labels(channel="whatsapp")._value.get() == 1


def test_failure_not_recorded_and_token_not_logged(tg_env, capsys):
    from loguru import logger

    msgs: list[str] = []
    hid = logger.add(lambda m: msgs.append(str(m)), level="DEBUG")
    try:
        add_pending(1, 1)
        bad = FakeChannel(RuntimeError(f"POST https://api.telegram.org/bot{TOKEN}/sendMessage"))
        assert not digest.run_digest(tg_env, channel_factory=lambda _n: bad)
    finally:
        logger.remove(hid)
    with dbs.get_session() as s:
        assert s.scalar(select(Digest.id)) is None
    assert msgs and all(TOKEN not in m for m in msgs)
    assert metrics.digests_sent is not None
