"""The API serves the built SPA from KINO_WEB_DIR without shadowing any API path."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

INDEX = "<!doctype html><title>Kino</title><div id=root></div>"
ASSET = "console.log('kino')"


@pytest.fixture
def web_dir(tmp_path: Path) -> Path:
    d = tmp_path / "web"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(INDEX)
    (d / "theme-init.js").write_text("/* theme */")
    (d / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (d / "assets" / "index-abc123.js").write_text(ASSET)
    (tmp_path / "secret.txt").write_text("TOP-SECRET")  # sibling of the web dir
    return d


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, web: Path | None):
    from fastapi.testclient import TestClient

    from kino.api import auth
    from kino.config import get_settings

    for k in list(os.environ):
        if k.startswith("KINO_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("KINO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KINO_PHOTOS_DIR", str(tmp_path / "data" / "photos"))
    monkeypatch.setenv("KINO_WEBHOOK_SECRET", "web-test-webhook-secret")
    monkeypatch.setenv("KINO_SECRET_KEY", "web-test-cookie-key")
    monkeypatch.setenv("KINO_WEB_DIR", str(web if web is not None else tmp_path / "missing"))
    get_settings.cache_clear()
    auth.reset_state()
    from kino.api.app import create_app

    return TestClient(create_app())


@pytest.fixture
def client(monkeypatch, tmp_path, web_dir):
    from kino.config import get_settings
    from kino.db import session as dbs

    with _client(monkeypatch, tmp_path, web_dir) as c:
        yield c
    get_settings.cache_clear()
    dbs.get_engine().dispose()


def test_root_serves_index_with_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.text == INDEX
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-content-type-options"] == "nosniff"
    csp = r.headers["content-security-policy"]
    for part in (
        "default-src 'self'",
        "img-src 'self' data: blob:",
        "style-src 'self' 'unsafe-inline'",
        "frame-ancestors 'none'",
    ):
        assert part in csp


def test_head_root(client):
    r = client.head("/")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


def test_hashed_asset_cached_forever(client):
    r = client.get("/assets/index-abc123.js")
    assert r.status_code == 200
    assert r.text == ASSET
    assert "javascript" in r.headers["content-type"]
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize("name", ["theme-init.js", "favicon.svg", "index.html"])
def test_root_files_revalidate(client, name):
    r = client.get(f"/{name}")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/kids", "/kids/3", "/kids/3/enroll", "/review"])
def test_client_routes_fall_back_to_index(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert r.text == INDEX
    assert r.headers["cache-control"] == "no-cache"


def test_missing_asset_is_404_not_html(client):
    r = client.get("/assets/index-gone.js")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


@pytest.mark.parametrize("path", ["/api/nope", "/api", "/api/kids/1/nope/deeper"])
def test_unknown_api_paths_stay_json_404(client, path):
    r = client.get(path)
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/json"
    assert r.json() == {"detail": "Not Found"}


def test_api_routes_unaffected(client):
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/json"
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/readyz").status_code == 200
    assert client.get("/metrics").status_code == 401  # not public, not logged in
    assert client.get("/api/kids").status_code == 401  # auth errors are not swallowed


def test_non_get_on_ui_path_is_not_served(client):
    r = client.post("/kids/3")
    assert r.status_code in (404, 405)
    assert "Kino" not in r.text


@pytest.mark.parametrize(
    "path",
    [
        "/%2e%2e/secret.txt",
        "/assets/%2e%2e/%2e%2e/secret.txt",
        "/..%2fsecret.txt",
        "/%2fetc%2fpasswd",
        "//etc/passwd",
        "/%00",
    ],
)
def test_traversal_never_leaks(client, path):
    r = client.get(path)
    assert "TOP-SECRET" not in r.text
    assert "root:" not in r.text
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert r.text == INDEX


def test_missing_web_dir_runs_api_only(monkeypatch, tmp_path):
    from kino.config import get_settings
    from kino.db import session as dbs

    with _client(monkeypatch, tmp_path, None) as c:
        r = c.get("/")
        assert r.status_code == 404
        assert r.json() == {"detail": "Not Found"}
        assert c.get("/healthz").status_code == 200
        assert c.get("/api/auth/status").status_code == 200
    get_settings.cache_clear()
    dbs.get_engine().dispose()
