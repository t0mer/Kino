import os

import pytest
from sqlalchemy import select

from kino.config import Settings
from kino.db import session as dbs
from kino.db.models import Setting
from kino.services.openwa_cfg import (
    EnvLockedError,
    OpenWAConfig,
    OpenWANotConfiguredError,
    clear_openwa_session,
    clear_outgoing_session,
    env_overrides,
    load_openwa_config,
    save_openwa_config,
    save_outgoing_session,
)

OPENWA_ENV = (
    "KINO_OPENWA_URL",
    "KINO_OPENWA_API_KEY",
    "KINO_OPENWA_SESSION_ID",
    "KINO_WEBHOOK_SECRET",
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    for k in OPENWA_ENV:
        monkeypatch.delenv(k, raising=False)
    eng = dbs.init_engine(f"sqlite:///{tmp_path / 'cfg.db'}")
    dbs.run_migrations()
    yield tmp_path
    eng.dispose()


def env(data_dir, **kw) -> Settings:
    return Settings(data_dir=data_dir, **kw)


def save(st: Settings, **kw) -> None:
    with dbs.get_session() as s:
        save_openwa_config(s, st, **kw)


def test_empty_is_not_configured(db):
    cfg = load_openwa_config(env(db))
    assert (cfg.url, cfg.api_key, cfg.session_id, cfg.webhook_secret) == (None,) * 4
    assert not cfg.configured and not cfg.client_ready
    with pytest.raises(OpenWANotConfiguredError, match="setup wizard"):
        cfg.client_args()
    with pytest.raises(OpenWANotConfiguredError, match="KINO_WEBHOOK_SECRET"):
        cfg.require_webhook_secret()


def test_saved_values_roundtrip_and_secrets_are_encrypted(db):
    st = env(db)
    save(
        st,
        url="http://owa:2785",
        api_key="API-KEY-123",
        session_id="sess",
        webhook_secret="W" * 32,
        session_name="Main phone",
    )
    cfg = load_openwa_config(st)
    assert cfg.configured and cfg.client_args() == ("http://owa:2785", "API-KEY-123", "sess")
    assert cfg.webhook_secret == "W" * 32 and cfg.session_name == "Main phone"
    with dbs.get_session() as s:
        found = s.scalars(select(Setting).where(Setting.key.like("openwa.%")))
        rows = {r.key: r.value for r in found}
    assert rows["openwa.api_key_enc"].startswith("v1:")
    assert "API-KEY-123" not in str(rows) and "W" * 32 not in str(rows)


def test_env_wins_per_field(db):
    save(
        env(db),
        url="http://db-url",
        api_key="db-key",
        session_id="db-sess",
        webhook_secret="d" * 20,
    )
    st = env(db, openwa_url="http://env-url", webhook_secret="e" * 20)  # type: ignore[arg-type]
    cfg = load_openwa_config(st)
    assert (cfg.url, cfg.api_key, cfg.session_id, cfg.webhook_secret) == (
        "http://env-url",
        "db-key",
        "db-sess",
        "e" * 20,
    )
    assert env_overrides(st) == ["url", "webhook_secret"]


def test_empty_env_values_do_not_lock(db):
    st = env(
        db,
        openwa_url="",
        openwa_api_key="",
        openwa_session_id="",  # type: ignore[arg-type]
        webhook_secret="",
    )
    assert env_overrides(st) == []
    save(st, url="http://owa", api_key="k" * 10, session_id="s", webhook_secret="w" * 20)
    assert load_openwa_config(st).configured


def test_session_name_hidden_when_env_sets_the_session(db):
    save(env(db), session_id="db-sess", session_name="Old phone")
    assert load_openwa_config(env(db, openwa_session_id="env-sess")).session_name is None


def test_save_rejects_env_locked_fields(db):
    st = env(db, openwa_api_key="k")  # type: ignore[arg-type]
    with dbs.get_session() as s:
        with pytest.raises(EnvLockedError, match="KINO_OPENWA_API_KEY"):
            save_openwa_config(s, st, api_key="other")
        save_openwa_config(s, st, url="http://ok")  # an unlocked field still saves
    assert load_openwa_config(st).url == "http://ok"


def test_lost_or_replaced_key_reads_as_not_configured(db):
    st = env(db)
    save(st, url="http://owa", api_key="k" * 10, session_id="s", webhook_secret="w" * 20)
    (db / "secret.key").unlink()
    cfg = load_openwa_config(st)
    assert cfg.url == "http://owa" and cfg.api_key is None and cfg.webhook_secret is None
    assert not cfg.configured
    (db / "secret.key").write_bytes(os.urandom(32))  # replaced by a different key
    assert load_openwa_config(st).api_key is None


def test_repr_hides_secrets():
    text = repr(OpenWAConfig("http://u", "API-KEY", "s", "WEBHOOK-SECRET"))
    assert "API-KEY" not in text and "WEBHOOK-SECRET" not in text


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("http://openwa:2785", "HTTP://OpenWA:2785/", True),
        ("http://user:pw@openwa:2785/api/", "http://openwa:2785/api", True),
        ("http://openwa:2785", "http://openwa:2786", False),
        ("http://openwa:2785", "https://openwa:2785", False),
        ("http://openwa:2785/a", "http://openwa:2785/b", False),
        ("http://[::1]:2785", "http://[::1]:2785/", True),
    ],
)
def test_normalize_url(a, b, same):
    from kino.services.openwa_cfg import normalize_url

    assert (normalize_url(a) == normalize_url(b)) is same


def test_outgoing_session_falls_back_to_incoming(db):
    st = env(db)
    save(st, url="http://owa", api_key="k" * 10, session_id="in", session_name="Main")
    cfg = load_openwa_config(st)
    assert cfg.outgoing_session_id == "in" and cfg.same_as_incoming
    assert cfg.outgoing_client_args() == cfg.client_args() == ("http://owa", "k" * 10, "in")
    assert cfg.outgoing_session_name == "Main"


def test_outgoing_session_stored_and_cleared(db):
    st = env(db)
    save(st, url="http://owa", api_key="k" * 10, session_id="in")
    with dbs.get_session() as s:
        save_outgoing_session(s, "out", "Spare")
    cfg = load_openwa_config(st)
    assert (cfg.outgoing_session_id, cfg.outgoing_session_name) == ("out", "Spare")
    assert not cfg.same_as_incoming
    assert cfg.outgoing_client_args() == ("http://owa", "k" * 10, "out")
    assert cfg.client_args()[2] == "in"
    with dbs.get_session() as s:
        clear_outgoing_session(s)
    cfg = load_openwa_config(st)
    assert cfg.outgoing_session_id == "in" and cfg.same_as_incoming


def test_outgoing_session_with_env_incoming_session(db):
    st = env(db, openwa_session_id="env-in")  # type: ignore[arg-type]
    with dbs.get_session() as s:
        save_outgoing_session(s, "out", "Spare")
    assert load_openwa_config(st).outgoing_session_id == "out"
    assert load_openwa_config(env(db, openwa_session_id="env-in")).session_id == "env-in"  # type: ignore[arg-type]


def test_clear_openwa_session_also_clears_outgoing(db):
    st = env(db)
    save(st, url="http://owa", api_key="k" * 10, session_id="in", session_name="Main")
    with dbs.get_session() as s:
        save_outgoing_session(s, "out", "Spare")
        clear_openwa_session(s, st)
    cfg = load_openwa_config(st)
    assert cfg.session_id is None and cfg.outgoing_session_id is None


def test_clear_openwa_session_clears_outgoing_even_when_env_pins_incoming(db):
    st = env(db, openwa_session_id="env-in")  # type: ignore[arg-type]
    with dbs.get_session() as s:
        save_outgoing_session(s, "out", "Spare")
        clear_openwa_session(s, st)
    cfg = load_openwa_config(st)
    assert cfg.session_id == "env-in" and cfg.outgoing_session_id == "env-in"
