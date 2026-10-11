"""Authentication: local login, Cloudflare Access JWT, CSRF and first-run setup (§13.1, R2)."""

import json
import time
from typing import Annotated

import jwt
import pytest
from argon2 import PasswordHasher
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from kino import metrics
from kino.config import get_settings

SECRET = "test-secret"
TEAM = "myteam.cloudflareaccess.com"
AUD = "aud-tag-123"
PASSWORD = "correct horse battery"
CSRF = "csrf-token-value"


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


KEY = _key()
OTHER_KEY = _key()


def _jwks(*keys: tuple[str, rsa.RSAPrivateKey]) -> dict:
    out = []
    for kid, k in keys:
        jwk = json.loads(RSAAlgorithm.to_jwk(k.public_key()))
        jwk.update(kid=kid, alg="RS256", use="sig")
        out.append(jwk)
    return {"keys": out}


def cf_token(
    *,
    key: rsa.RSAPrivateKey = KEY,
    kid: str = "k1",
    aud: str = AUD,
    iss: str = f"https://{TEAM}",
    email: str = "parent@example.com",
    exp_in: int = 600,
) -> str:
    now = int(time.time())
    claims = {"aud": [aud], "iss": iss, "email": email, "iat": now, "exp": now + exp_in}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in list(__import__("os").environ):
        if k.startswith("KINO_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("KINO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KINO_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("KINO_ADMIN_USER", "admin")
    monkeypatch.setenv("KINO_ADMIN_PASSWORD_HASH", PasswordHasher().hash(PASSWORD))
    monkeypatch.setenv("KINO_SECRET_KEY", "cookie-signing-key")
    monkeypatch.setenv("KINO_CF_ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.setenv("KINO_CF_ACCESS_AUD", AUD)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


@pytest.fixture
def jwks_calls(monkeypatch):
    from kino.api import auth

    calls: list[str] = []

    def fetch(url: str) -> dict:
        calls.append(url)
        return _jwks(("k1", KEY))

    monkeypatch.setattr(auth, "_fetch_jwks", fetch)
    auth.reset_state()
    yield calls
    auth.reset_state()


def _make_client(base_url: str = "http://testserver") -> TestClient:
    from kino.api.app import create_app
    from kino.api.deps import AuthInfo, require_auth, require_csrf

    app = create_app()

    # A protected state-changing probe route, as later routers will declare them.
    @app.post("/api/_probe", dependencies=[Depends(require_csrf)])
    def probe(info: Annotated[AuthInfo, Depends(require_auth)]) -> dict[str, str]:
        return {"method": info.method, "user": info.user}

    return TestClient(app, base_url=base_url)


@pytest.fixture
def client(env, jwks_calls):
    with _make_client() as c:
        yield c


def csrf(c: TestClient) -> dict[str, str]:
    c.cookies.set("kino_csrf", CSRF)
    return {"X-CSRF-Token": CSRF}


def login(c: TestClient, user: str = "admin", password: str = PASSWORD, **kw):
    return c.post(
        "/api/auth/login", json={"username": user, "password": password}, headers=csrf(c), **kw
    )


def auth_count(method: str, result: str) -> float:
    v = metrics.auth.labels(method=method, result=result)._value.get()  # type: ignore[attr-defined]
    return float(v)


# ---- local login ---------------------------------------------------------------------------


def test_protected_route_requires_auth(client):
    assert client.post("/api/_probe", headers=csrf(client)).status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_valid_login_sets_session_and_me(client):
    before = auth_count("local", "ok")
    r = login(client)
    assert r.status_code == 200
    assert r.json() == {"method": "local", "user": "admin"}
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == {"method": "local", "user": "admin"}
    assert client.post("/api/_probe", headers=csrf(client)).json()["method"] == "local"
    assert auth_count("local", "ok") == before + 1


def test_invalid_login(client):
    before = auth_count("local", "invalid")
    assert login(client, password="wrong").status_code == 401
    assert login(client, user="root").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    assert auth_count("local", "invalid") == before + 2


def test_lockout_after_five_failures(client):
    for _ in range(5):
        assert login(client, password="wrong").status_code == 401
    r = login(client)  # even the correct password is refused while locked out
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert client.get("/api/auth/me").status_code == 401


def test_lockout_expires(client, monkeypatch):
    from kino.api import auth

    now = [1000.0]
    monkeypatch.setattr(auth, "_now", lambda: now[0])
    for _ in range(5):
        login(client, password="wrong")
    assert login(client).status_code == 429
    now[0] += 15 * 60 + 1
    assert login(client).status_code == 200


def test_cookie_flags(client):
    r = login(client)
    cookie = next(
        v for k, v in r.headers.multi_items() if k == "set-cookie" and "kino_session" in v
    )
    low = cookie.lower()
    assert "httponly" in low
    assert "samesite=lax" in low
    assert "max-age=604800" in low
    assert "secure" not in low  # plain HTTP on the LAN


def test_cookie_secure_over_https(env, jwks_calls):
    with _make_client("https://testserver") as c:
        r = login(c)
        cookie = next(
            v for k, v in r.headers.multi_items() if k == "set-cookie" and "kino_session" in v
        )
        assert "secure" in cookie.lower()


def test_tampered_session_cookie_rejected(client):
    login(client)
    value = client.cookies.get("kino_session")
    client.cookies.clear()
    client.cookies.set("kino_session", value[:-2] + ("AA" if value[-2:] != "AA" else "BB"))
    assert client.get("/api/auth/me").status_code == 401


def test_logout_clears_session(client):
    login(client)
    assert client.post("/api/auth/logout", headers=csrf(client)).status_code == 204
    assert client.get("/api/auth/me").status_code == 401


# ---- CSRF ----------------------------------------------------------------------------------


def test_csrf_missing_or_wrong_is_403(client):
    login(client)
    client.cookies.delete("kino_csrf")
    assert client.post("/api/_probe").status_code == 403
    client.cookies.set("kino_csrf", CSRF)
    assert client.post("/api/_probe").status_code == 403  # header missing
    assert client.post("/api/_probe", headers={"X-CSRF-Token": "other"}).status_code == 403
    assert client.post("/api/_probe", headers={"X-CSRF-Token": CSRF}).status_code == 200


def test_login_requires_csrf(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert r.status_code == 403


def test_status_issues_csrf_cookie(client):
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json() == {"setup_required": False}
    token = client.cookies.get("kino_csrf")
    assert token and len(token) >= 32
    cookie = next(v for k, v in r.headers.multi_items() if k == "set-cookie" and "kino_csrf" in v)
    assert "httponly" not in cookie.lower()  # the SPA reads it


# ---- Cloudflare Access ---------------------------------------------------------------------


def cf(token: str, **extra: str) -> dict[str, str]:
    return {"Cf-Access-Jwt-Assertion": token, **extra}


def test_cf_valid_jwt_authenticates(client, jwks_calls):
    r = client.get("/api/auth/me", headers=cf(cf_token()))
    assert r.status_code == 200
    assert r.json() == {"method": "cloudflare", "user": "parent@example.com"}
    assert jwks_calls == [f"https://{TEAM}/cdn-cgi/access/certs"]
    # JWKS is cached
    client.get("/api/auth/me", headers=cf(cf_token()))
    assert len(jwks_calls) == 1


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(lambda: cf_token(aud="other-aud"), id="wrong-aud"),
        pytest.param(lambda: cf_token(exp_in=-60), id="expired"),
        pytest.param(lambda: cf_token(key=OTHER_KEY), id="bad-signature"),
        pytest.param(lambda: cf_token(iss="https://evil.cloudflareaccess.com"), id="wrong-iss"),
        pytest.param(lambda: "not-a-jwt", id="garbage"),
    ],
)
def test_cf_invalid_jwt_is_401_without_cookie_fallback(client, token):
    login(client)  # a perfectly valid local session is present too
    assert client.get("/api/auth/me").status_code == 200
    before = auth_count("cloudflare", "invalid")
    r = client.get("/api/auth/me", headers=cf(token()))
    assert r.status_code == 401
    assert client.post("/api/_probe", headers={**cf(token()), **csrf(client)}).status_code == 401
    assert auth_count("cloudflare", "invalid") >= before + 1


def test_cf_unknown_kid_refreshes_jwks(client, jwks_calls, monkeypatch):
    from kino.api import auth

    client.get("/api/auth/me", headers=cf(cf_token()))
    monkeypatch.setattr(
        auth,
        "_fetch_jwks",
        lambda url: (jwks_calls.append(url), _jwks(("k1", KEY), ("k2", OTHER_KEY)))[1],
    )
    monkeypatch.setattr(auth, "_now", lambda: time.time() + 120)  # past the refresh cooldown
    r = client.get("/api/auth/me", headers=cf(cf_token(key=OTHER_KEY, kid="k2")))
    assert r.status_code == 200
    assert len(jwks_calls) == 2


def test_cf_allowed_emails(env, jwks_calls):
    env.setenv("KINO_CF_ACCESS_ALLOWED_EMAILS", "a@example.com, parent@example.com")
    get_settings.cache_clear()
    with _make_client() as c:
        assert c.get("/api/auth/me", headers=cf(cf_token())).status_code == 200
        r = c.get("/api/auth/me", headers=cf(cf_token(email="stranger@example.com")))
        assert r.status_code == 401


def test_cf_header_without_config_is_401(env, jwks_calls):
    env.delenv("KINO_CF_ACCESS_TEAM_DOMAIN")
    env.delenv("KINO_CF_ACCESS_AUD")
    get_settings.cache_clear()
    with _make_client() as c:
        assert c.get("/api/auth/me", headers=cf(cf_token())).status_code == 401


def test_spoofed_email_header_ignored(client):
    r = client.get("/api/auth/me", headers={"Cf-Access-Authenticated-User-Email": "parent@x.com"})
    assert r.status_code == 401
    r = client.post(
        "/api/_probe",
        headers={"Cf-Access-Authenticated-User-Email": "parent@x.com", **csrf(client)},
    )
    assert r.status_code == 401


def test_cf_state_change_still_needs_csrf(client):
    assert client.post("/api/_probe", headers=cf(cf_token())).status_code == 403
    r = client.post("/api/_probe", headers={**cf(cf_token()), **csrf(client)})
    assert r.status_code == 200
    assert r.json()["method"] == "cloudflare"


# ---- /metrics ------------------------------------------------------------------------------


def test_metrics_requires_auth_unless_public(client, env):
    assert client.get("/metrics").status_code == 401
    login(client)
    assert client.get("/metrics").status_code == 200


def test_metrics_public(env, jwks_calls):
    env.setenv("KINO_METRICS_PUBLIC", "true")
    get_settings.cache_clear()
    with _make_client() as c:
        assert c.get("/metrics").status_code == 200


# ---- first-run setup (R2) ------------------------------------------------------------------


@pytest.fixture
def fresh(env, jwks_calls):
    """No password configured anywhere: the first-run setup is open."""
    env.delenv("KINO_ADMIN_PASSWORD_HASH")
    get_settings.cache_clear()
    with _make_client() as c:
        yield c


def setup(c: TestClient, password: str = PASSWORD, **headers: str):
    return c.post("/api/auth/setup", json={"password": password}, headers={**csrf(c), **headers})


def test_setup_once(fresh):
    assert fresh.get("/api/auth/status").json() == {"setup_required": True}
    assert login(fresh).status_code == 409  # nothing to log in against yet
    r = setup(fresh)
    assert r.status_code == 200
    assert r.json() == {"method": "local", "user": "admin"}
    assert fresh.get("/api/auth/me").status_code == 200  # setup logs the admin in
    assert fresh.get("/api/auth/status").json() == {"setup_required": False}
    # second attempt is refused and does not change the password
    assert setup(fresh, password="another password").status_code == 409
    fresh.cookies.delete("kino_session")
    assert login(fresh).status_code == 200
    assert login(fresh, password="another password").status_code == 401


def test_setup_stores_argon2_hash(fresh):
    from kino.db.models import Setting
    from kino.db.session import get_session

    setup(fresh)
    with get_session() as s:
        row = s.get(Setting, "auth.password_hash")
        assert row is not None
        assert row.value.startswith("$argon2")
        assert PASSWORD not in row.value


@pytest.mark.parametrize(
    "header",
    ["Cf-Ray", "Cf-Connecting-Ip", "X-Forwarded-For", "Cf-Access-Jwt-Assertion"],
)
def test_setup_refused_through_proxy(fresh, header):
    assert setup(fresh, **{header: "1.2.3.4"}).status_code == 403
    assert fresh.get("/api/auth/status").json() == {"setup_required": True}


def test_setup_rejects_short_password(fresh):
    assert setup(fresh, password="short").status_code == 422


def test_setup_needs_csrf(fresh):
    r = fresh.post("/api/auth/setup", json={"password": PASSWORD})
    assert r.status_code == 403


def test_env_hash_wins_over_db_hash(env, jwks_calls):
    from kino.db.models import Setting
    from kino.db.session import get_session

    with _make_client() as c:
        with get_session() as s:
            s.add(Setting(key="auth.password_hash", value=PasswordHasher().hash("db password")))
        assert c.get("/api/auth/status").json() == {"setup_required": False}
        assert setup(c).status_code == 409
        assert login(c, password="db password").status_code == 401
        assert login(c).status_code == 200


# ---- secret key ----------------------------------------------------------------------------


def test_secret_key_generated_and_persisted(env, jwks_calls):
    from kino.db.models import Setting
    from kino.db.session import get_session

    env.delenv("KINO_SECRET_KEY")
    get_settings.cache_clear()
    with _make_client() as c:
        assert login(c).status_code == 200
        cookie = c.cookies.get("kino_session")
    with get_session() as s:
        row = s.get(Setting, "auth.secret_key")
        assert row is not None and len(row.value) >= 32
    # a restarted app keeps the same key, so existing sessions stay valid
    with _make_client() as c2:
        c2.cookies.set("kino_session", cookie)
        assert c2.get("/api/auth/me").status_code == 200


def test_password_change_invalidates_sessions(env, jwks_calls):
    with _make_client() as c:
        login(c)
        cookie = c.cookies.get("kino_session")
    env.setenv("KINO_ADMIN_PASSWORD_HASH", PasswordHasher().hash("new password!"))
    get_settings.cache_clear()
    with _make_client() as c2:
        c2.cookies.set("kino_session", cookie)
        assert c2.get("/api/auth/me").status_code == 401
