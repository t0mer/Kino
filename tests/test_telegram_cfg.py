import pytest
from sqlalchemy import select

from kino.config import Settings
from kino.db import session as dbs
from kino.db.models import Setting
from kino.services import InvalidError
from kino.services.telegram_cfg import (
    load_telegram_config,
    redact_token,
    save_telegram_connection,
    save_telegram_options,
    save_telegram_token,
)

TOKEN = "123456789:AAEhBOweik6ad9r_QXMENQjcrGbqCr4K-RM"
OTHER = "987654321:ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"


@pytest.fixture
def db(tmp_path, monkeypatch):
    eng = dbs.init_engine(f"sqlite:///{tmp_path / 'cfg.db'}")
    dbs.run_migrations()
    yield tmp_path
    eng.dispose()


def env(data_dir, **kw) -> Settings:
    return Settings(data_dir=data_dir, **kw)


def raw(key):
    with dbs.get_session() as s:
        return s.scalar(select(Setting.value).where(Setting.key == key))


def test_defaults(db):
    cfg = load_telegram_config(env(db))
    assert not cfg.has_token
    assert (cfg.send_as_file, cfg.delete_on_remove, cfg.bot_username) == (False, False, None)
    assert cfg.api_url == "https://api.telegram.org" and cfg.rate_per_min == 20


def test_token_encrypted_at_rest_and_trimmed(db):
    st = env(db)
    with dbs.get_session() as s:
        save_telegram_token(s, st, f"  {TOKEN}\n", "kino_bot")
    stored = raw("telegram.bot_token_enc")
    assert stored.startswith("v1:") and TOKEN not in stored
    cfg = load_telegram_config(st)
    assert cfg.bot_token == TOKEN and cfg.has_token and cfg.bot_username == "kino_bot"


@pytest.mark.parametrize("empty", [None, "", "  "])
def test_clear(db, empty):
    st = env(db)
    with dbs.get_session() as s:
        save_telegram_token(s, st, TOKEN, "kino_bot")
    with dbs.get_session() as s:
        save_telegram_token(s, st, empty, None)
    cfg = load_telegram_config(st)
    assert not cfg.has_token and cfg.bot_username is None
    assert raw("telegram.bot_token_enc") is None


def test_removed_env_names_are_ignored(db, monkeypatch):
    monkeypatch.setenv("KINO_TELEGRAM_BOT_TOKEN", OTHER)
    monkeypatch.setenv("KINO_TELEGRAM_API_URL", "http://env.example")
    monkeypatch.setenv("KINO_TELEGRAM_RATE_PER_MIN", "3")
    cfg = load_telegram_config(env(db))
    assert not cfg.has_token
    assert cfg.api_url == "https://api.telegram.org" and cfg.rate_per_min == 20
    with dbs.get_session() as s:
        save_telegram_token(s, env(db), TOKEN, None)
    assert load_telegram_config(env(db)).bot_token == TOKEN


def test_connection_round_trip_and_partial_update(db):
    st = env(db)
    with dbs.get_session() as s:
        save_telegram_connection(s, api_url=" http://tg.local:8081/ ", rate_per_min=5)
    cfg = load_telegram_config(st)
    assert (cfg.api_url, cfg.rate_per_min) == ("http://tg.local:8081", 5)
    with dbs.get_session() as s:
        save_telegram_connection(s, rate_per_min=7)  # api_url omitted: unchanged
    cfg = load_telegram_config(st)
    assert (cfg.api_url, cfg.rate_per_min) == ("http://tg.local:8081", 7)


@pytest.mark.parametrize(
    "bad",
    [
        "ftp://x",
        "api.telegram.org",
        "",
        "http://",
        "https://u:p@host",
        "https://host?x=1",
        "https://host#f",
        "https://host:99999",
    ],
)
def test_bad_api_url_rejected(db, bad):
    with dbs.get_session() as s, pytest.raises(InvalidError):
        save_telegram_connection(s, api_url=bad)
    assert raw("telegram.api_url") is None


@pytest.mark.parametrize("bad", [0, -1, 601, True])
def test_bad_rate_rejected(db, bad):
    with dbs.get_session() as s, pytest.raises(InvalidError):
        save_telegram_connection(s, rate_per_min=bad)
    assert raw("telegram.rate_per_min") is None


def test_undecryptable_reads_as_not_set(db):
    st = env(db)
    with dbs.get_session() as s:
        save_telegram_token(s, st, TOKEN, None)
    (db / "secret.key").unlink()
    assert not load_telegram_config(st).has_token


@pytest.mark.parametrize(
    "bad", ["abc", "123:short", "notnumeric:AAEhBOweik6ad9r_QXMENQjcr", "1:a b" + "c" * 25]
)
def test_bad_shape_rejected(db, bad):
    with dbs.get_session() as s, pytest.raises(InvalidError):
        save_telegram_token(s, env(db), bad, None)
    assert raw("telegram.bot_token_enc") is None


def test_options_round_trip(db):
    st = env(db)
    with dbs.get_session() as s:
        save_telegram_options(s, st, True, False)
    cfg = load_telegram_config(st)
    assert (cfg.send_as_file, cfg.delete_on_remove) == (True, False)
    with dbs.get_session() as s:
        save_telegram_options(s, st, False, True)
    cfg = load_telegram_config(st)
    assert (cfg.send_as_file, cfg.delete_on_remove) == (False, True)


def test_repr_never_leaks_token(db):
    with dbs.get_session() as s:
        save_telegram_token(s, env(db), TOKEN, None)
    cfg = load_telegram_config(env(db))
    assert TOKEN not in repr(cfg) and TOKEN.split(":")[1] not in f"{cfg!r} {cfg}"


def test_redact_token():
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    assert redact_token(url, TOKEN) == "https://api.telegram.org/bot***/sendPhoto"
    assert redact_token(url) == "https://api.telegram.org/bot***/sendPhoto"
    assert redact_token(f"bad token {TOKEN} here", TOKEN) == "bad token bot*** here"
    assert redact_token("nothing here", None) == "nothing here"
