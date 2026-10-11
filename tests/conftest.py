import os
import tempfile
import warnings

# Starlette warns once, at import, that its TestClient should use httpx2 instead of httpx.
# Import it here with that warning silenced so `pytest -W error` stays clean.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`")
    import fastapi.testclient  # noqa: F401

# kino.tasks builds its huey queue file at import time; keep it out of /data.
os.environ.setdefault("KINO_DATA_DIR", tempfile.mkdtemp(prefix="kino-test-"))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _in_process_metrics():
    """create_app() switches metrics to a per-test multiprocess dir; undo it afterwards."""
    yield
    from kino import metrics

    if metrics.multiprocess_dir() is not None:
        metrics.reset()


API_PASSWORD = "api-test-password"
API_CSRF = "api-csrf-token"
_api_hash: str | None = None


@pytest.fixture
def api(monkeypatch, tmp_path):
    """An authenticated TestClient (local login) with the CSRF header pre-set.

    Data dir and photos dir live under ``tmp_path``; OpenWA is not configured unless a test
    sets ``api.app.state.openwa`` (and settings) itself.
    """
    global _api_hash
    from argon2 import PasswordHasher
    from fastapi.testclient import TestClient

    from kino.api import auth
    from kino.config import get_settings
    from kino.db import session as dbs

    if _api_hash is None:
        _api_hash = PasswordHasher().hash(API_PASSWORD)
    for k in list(os.environ):
        if k.startswith("KINO_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("KINO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KINO_PHOTOS_DIR", str(tmp_path / "photos"))
    monkeypatch.setenv("KINO_WEBHOOK_SECRET", "api-test-webhook-secret")
    monkeypatch.setenv("KINO_ADMIN_USER", "admin")
    monkeypatch.setenv("KINO_ADMIN_PASSWORD_HASH", _api_hash)
    monkeypatch.setenv("KINO_SECRET_KEY", "api-test-cookie-key")
    get_settings.cache_clear()
    auth.reset_state()
    from kino.api.app import create_app

    with TestClient(create_app()) as c:
        c.cookies.set("kino_csrf", API_CSRF)
        c.headers["X-CSRF-Token"] = API_CSRF
        r = c.post("/api/auth/login", json={"username": "admin", "password": API_PASSWORD})
        assert r.status_code == 200, r.text
        yield c
    auth.reset_state()
    get_settings.cache_clear()
    dbs.get_engine().dispose()
