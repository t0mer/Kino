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
    assert s.forward_rate_per_min == 6


def test_yaml_used_when_env_absent(tmp_path):
    (tmp_path / "config.yaml").write_text("workers: 5\n")
    assert Settings().workers == 5


def test_env_wins_over_yaml(monkeypatch, tmp_path):
    (tmp_path / "config.yaml").write_text("workers: 5\n")
    monkeypatch.setenv("KINO_WORKERS", "3")
    assert Settings().workers == 3


def test_missing_webhook_secret_raises():
    with pytest.raises(RuntimeError):
        Settings().require_webhook_secret()


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
