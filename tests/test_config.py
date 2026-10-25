import pytest

from kino.config import Settings, get_settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    import os

    for k in list(os.environ):
        if k.startswith("KINO_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("KINO_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()


def test_env_value_is_read(monkeypatch):
    monkeypatch.setenv("KINO_WORKERS", "3")
    assert Settings().workers == 3


def test_defaults():
    s = Settings()
    assert s.workers == 2
    assert s.det_size == 1024
    assert s.embedder == "auraface"


def test_yaml_used_when_env_absent(tmp_path):
    (tmp_path / "config.yaml").write_text("workers: 5\n")
    assert Settings().workers == 5


def test_env_wins_over_yaml(monkeypatch, tmp_path):
    (tmp_path / "config.yaml").write_text("workers: 5\n")
    monkeypatch.setenv("KINO_WORKERS", "3")
    assert Settings().workers == 3


def test_missing_webhook_secret_is_not_fatal_at_load():
    s = Settings()  # Kino starts without it; the setup wizard can provide it later
    assert s.webhook_secret is None
    with pytest.raises(RuntimeError, match="setup wizard"):
        s.require_webhook_secret()


def test_webhook_secret_returned(monkeypatch):
    monkeypatch.setenv("KINO_WEBHOOK_SECRET", "s3")
    assert Settings().require_webhook_secret() == "s3"


def test_get_settings_cached():
    assert get_settings() is get_settings()


def test_repr_hides_secrets():
    s = Settings(
        webhook_secret="WH-SECRET-1",
        secret_key="SK-SECRET-2",
        admin_password_hash="PW-HASH-3",
        openwa_api_key="API-KEY-4",
    )
    text = repr(s) + str(s)
    for v in ("WH-SECRET-1", "SK-SECRET-2", "PW-HASH-3", "API-KEY-4"):
        assert v not in text
    assert s.require_webhook_secret() == "WH-SECRET-1"


def test_timezone_default_and_env(monkeypatch):
    assert Settings().timezone == "UTC"
    monkeypatch.setenv("KINO_TIMEZONE", "Asia/Jerusalem")
    assert Settings().timezone == "Asia/Jerusalem"


@pytest.mark.parametrize("bad", ["Mars/Olympus", "not a zone", "../etc/passwd", ""])
def test_timezone_unknown_rejected(monkeypatch, bad):
    monkeypatch.setenv("KINO_TIMEZONE", bad)
    with pytest.raises(ValueError, match="timezone"):
        Settings()


def test_removed_delivery_env_vars_are_ignored(monkeypatch):
    for name, value in {
        "KINO_TELEGRAM_BOT_TOKEN": "1:abc",
        "KINO_TELEGRAM_API_URL": "ftp://bad",
        "KINO_TELEGRAM_RATE_PER_MIN": "0",
        "KINO_FORWARD_RATE_PER_MIN": "0",
    }.items():
        monkeypatch.setenv(name, value)
    s = Settings()
    for attr in ("telegram_bot_token", "telegram_api_url", "telegram_rate_per_min"):
        assert not hasattr(s, attr)
    assert not hasattr(s, "forward_rate_per_min")
