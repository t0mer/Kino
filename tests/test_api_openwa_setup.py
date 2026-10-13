"""Setup wizard API against a fake OpenWA (sessions list + webhooks)."""

import json
from functools import partial

import httpx
import pytest
from loguru import logger
from pydantic import SecretStr

from kino.api import deps
from kino.api.routes import openwa_setup
from kino.openwa import client as owa_client
from kino.openwa.client import OpenWAClient
from kino.services.openwa_cfg import load_openwa_config

API_KEY = "owa-api-key-123"
SECRET = "wizard-webhook-secret-0123456789"
PHONE = "972501234567"
HOOK = "http://kino-api:8080/api/webhook/openwa"


class FakeOpenWA:
    def __init__(self) -> None:
        self.sessions = [
            {"id": "main", "name": "Main phone", "status": "ready", "phone": PHONE},
            {"id": "spare", "name": "טלפון", "status": "qr", "phone": None},
        ]
        self.hooks: list[dict] = []
        self.calls: list[tuple] = []
        self.hosts: list[str] = []
        self.key = API_KEY
        self.down = False
        self.fail_webhooks = False

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=req)
        self.hosts.append(req.url.host)
        if req.headers.get("x-api-key") != self.key:
            return httpx.Response(401)
        path = req.url.path
        body = json.loads(req.content) if req.content else None
        self.calls.append((req.method, path, body))
        if path == "/api/sessions":
            return httpx.Response(200, json=self.sessions)
        if "/webhooks" in path:
            if self.fail_webhooks:
                return httpx.Response(500)
            if req.method == "GET":
                return httpx.Response(200, json=self.hooks)
            if req.method == "POST":
                self.hooks.append({"id": "h-1", "url": body["url"]})
                return httpx.Response(201, json={"id": "h-1"})
            return httpx.Response(200, json={"id": path.rsplit("/", 1)[1]})
        if path.endswith("/chats"):
            return httpx.Response(200, json=[])
        if path.endswith("/messages/send-image"):
            return httpx.Response(201, json={"messageId": "true_x_ID"})
        return httpx.Response(404)


@pytest.fixture
def owa(api, monkeypatch):
    fake = FakeOpenWA()
    transport = httpx.MockTransport(fake)
    monkeypatch.setattr(
        openwa_setup, "list_sessions", partial(owa_client.list_sessions, transport=transport)
    )
    monkeypatch.setattr(
        deps,
        "OpenWAClient",
        lambda url, key, sid: OpenWAClient(url, key, sid, transport=transport),
    )
    # the api fixture sets KINO_WEBHOOK_SECRET; here the wizard owns every field
    api.app.state.settings = api.app.state.settings.model_copy(update={"webhook_secret": None})
    return fake


def connect(api, **body):
    return api.post("/api/openwa/setup/connect", json=body)


def status(api) -> dict:
    return api.get("/api/openwa/setup/status").json()


def test_status_when_nothing_is_set(api, owa):
    assert status(api) == {
        "configured": False,
        "url": None,
        "session_id": None,
        "session_name": None,
        "has_api_key": False,
        "has_webhook_secret": False,
        "webhook_secret_pending": False,
        "webhook_url": HOOK,
        "env_overrides": [],
        "outgoing_session_id": None,
        "outgoing_session_name": None,
        "same_as_incoming": True,
    }


def test_connect_pick_session_registers_webhook(api, owa):
    r = connect(api, url="http://openwa:2785", api_key=API_KEY, webhook_secret=SECRET)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "sessions": [
            {"id": "main", "name": "Main phone", "status": "ready", "phone_masked": "9725****567"},
            {"id": "spare", "name": "טלפון", "status": "qr", "phone_masked": None},
        ],
        "generated_webhook_secret": False,
    }
    r = api.post("/api/openwa/setup/session", json={"session_id": "main"})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "session_id": "main",
        "registered": True,
        "action": "created",
        "webhook_id": "h-1",
        "webhook_url": HOOK,
        "error": None,
    }
    method, path, body = owa.calls[-1]
    assert (method, path) == ("POST", "/api/sessions/main/webhooks")
    assert body["secret"] == SECRET and body["url"] == HOOK
    s = status(api)
    assert s["configured"] and s["session_name"] == "Main phone"
    assert s["has_api_key"] and s["has_webhook_secret"]


def test_secrets_never_returned_or_logged(api, owa):
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        first = connect(api, url="http://openwa:2785", api_key=API_KEY, webhook_secret=SECRET)
        texts = [first.text]
        texts.append(api.post("/api/openwa/setup/session", json={"session_id": "main"}).text)
        texts.append(api.get("/api/openwa/setup/status").text)
        owa.key = "rotated"
        texts.append(connect(api, api_key="wrong-key-value").text)
        owa.key, owa.fail_webhooks = API_KEY, True
        texts.append(api.post("/api/openwa/setup/session", json={"session_id": "main"}).text)
    finally:
        logger.remove(sink)
    for leak in (API_KEY, SECRET, PHONE, "wrong-key-value"):
        assert all(leak not in t for t in texts), leak
        assert all(leak not in ln for ln in lines), leak


def test_status_url_hides_credentials(api, owa):
    r = connect(api, url="http://user:pw@openwa:2785", api_key=API_KEY)
    assert r.status_code == 200, r.text
    s = api.get("/api/openwa/setup/status")
    assert s.json()["url"] == "http://openwa:2785"
    assert "pw" not in s.text


def test_bad_key_is_400_and_nothing_saved(api, owa):
    owa.key = "something-else"
    r = connect(api, url="http://openwa:2785", api_key=API_KEY, webhook_secret=SECRET)
    assert r.status_code == 400 and "API key" in r.json()["detail"]
    assert status(api)["has_api_key"] is False and status(api)["url"] is None


def test_unreachable_is_502_and_nothing_saved(api, owa):
    owa.down = True
    r = connect(api, url="http://openwa:2785", api_key=API_KEY)
    assert r.status_code == 502 and "Cannot reach OpenWA" in r.json()["detail"]
    assert status(api)["url"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"url": "ftp://openwa", "api_key": API_KEY},
        {"url": "javascript:alert(1)", "api_key": API_KEY},
        {"url": "http://openwa", "api_key": "has space"},
        {"url": "http://openwa", "api_key": API_KEY, "webhook_secret": "short"},
        {"url": "http://openwa", "api_key": API_KEY, "extra": 1},
        {"url": "http://openwa/?token=x", "api_key": API_KEY},  # query breaks httpx base_url
        {"url": "http://[bad", "api_key": API_KEY},  # unparseable: 422, never a 500
    ],
)
def test_bad_input_is_422(api, owa, body):
    assert api.post("/api/openwa/setup/connect", json=body).status_code == 422


def test_missing_url_or_key_without_stored_values_is_422(api, owa):
    assert connect(api, api_key=API_KEY).json()["detail"] == "Enter the OpenWA URL."
    assert connect(api, url="http://openwa").json()["detail"] == "Enter the OpenWA API key."


def test_secret_generated_once_and_blank_keeps_stored(api, owa):
    r = connect(api, url="http://openwa:2785", api_key=API_KEY)
    assert r.json()["generated_webhook_secret"] is True
    generated = load_openwa_config(api.app.state.settings).webhook_secret
    assert generated is not None and len(generated) == 64
    r = connect(api, url="", api_key="", webhook_secret="")  # blanks keep what is stored
    assert r.status_code == 200 and r.json()["generated_webhook_secret"] is False
    cfg = load_openwa_config(api.app.state.settings)
    assert cfg.webhook_secret == generated and cfg.api_key == API_KEY


def test_unknown_or_vanished_session_is_404(api, owa):
    connect(api, url="http://openwa:2785", api_key=API_KEY, webhook_secret=SECRET)
    assert api.post("/api/openwa/setup/session", json={"session_id": "nope"}).status_code == 404
    owa.sessions = [owa.sessions[1]]  # "main" removed between the two steps
    r = api.post("/api/openwa/setup/session", json={"session_id": "main"})
    assert r.status_code == 404 and "no longer exists" in r.json()["detail"]
    assert status(api)["session_id"] is None


def test_session_before_connect_is_409(api, owa):
    assert api.post("/api/openwa/setup/session", json={"session_id": "main"}).status_code == 409


def test_registration_failure_keeps_session_then_retry(api, owa):
    connect(api, url="http://openwa:2785", api_key=API_KEY, webhook_secret=SECRET)
    owa.fail_webhooks = True
    r = api.post("/api/openwa/setup/session", json={"session_id": "main"})
    assert r.status_code == 200
    body = r.json()
    assert body["registered"] is False and "HTTP 500" in body["error"]
    assert "Session saved" in body["error"]
    assert status(api)["session_id"] == "main"
    owa.fail_webhooks = False
    r = api.post("/api/openwa/webhook/register", json={"url": body["webhook_url"]})
    assert r.status_code == 200 and r.json()["action"] == "created"
    assert owa.calls[-1][2]["secret"] == SECRET


def test_env_locked_secret(api, owa):
    env_secret = "env-webhook-secret-0123456789"
    api.app.state.settings = api.app.state.settings.model_copy(
        update={"webhook_secret": SecretStr(env_secret)}
    )
    r = connect(api, url="http://openwa:2785", api_key=API_KEY, webhook_secret=SECRET)
    assert r.status_code == 409 and "KINO_WEBHOOK_SECRET" in r.json()["detail"]
    assert "set in the environment or config.yaml (KINO_WEBHOOK_SECRET)" in r.json()["detail"]
    assert owa.calls == []  # refused before contacting OpenWA
    r = connect(api, url="http://openwa:2785", api_key=API_KEY)
    assert r.status_code == 200 and r.json()["generated_webhook_secret"] is False
    assert status(api)["env_overrides"] == ["webhook_secret"]
    api.post("/api/openwa/setup/session", json={"session_id": "main"})
    assert owa.calls[-1][2]["secret"] == env_secret


def test_reconnect_drops_the_cached_client(api, owa):
    connect(api, url="http://openwa-a:2785", api_key=API_KEY, webhook_secret=SECRET)
    api.post("/api/openwa/setup/session", json={"session_id": "main"})
    assert api.get("/api/openwa/chats").status_code == 200
    assert owa.hosts[-1] == "openwa-a"
    connect(api, url="http://openwa-b:2785", api_key=API_KEY)
    assert api.get("/api/openwa/chats").status_code == 503  # session of openwa-a dropped
    api.post("/api/openwa/setup/session", json={"session_id": "main"})
    assert api.get("/api/openwa/chats").status_code == 200
    assert owa.hosts[-1] == "openwa-b"


def test_setup_routes_need_auth_and_csrf(api, owa):
    del api.headers["X-CSRF-Token"]
    assert api.post("/api/openwa/setup/connect", json={}).status_code == 403
    r = api.put("/api/openwa/setup/outgoing-session", json={"session_id": None})
    assert r.status_code == 403
    api.cookies.delete("kino_session")
    for method, path in [
        ("GET", "/api/openwa/setup/status"),
        ("POST", "/api/openwa/setup/connect"),
        ("POST", "/api/openwa/setup/session"),
        ("GET", "/api/openwa/setup/sessions"),
        ("PUT", "/api/openwa/setup/outgoing-session"),
    ]:
        assert api.request(method, path, json={}).status_code == 401


def test_validation_errors_do_not_echo_input(api, owa):
    bodies = [
        {"api_key": "secret key-value-XYZ"},
        {"webhook_secret": "my secret with space 123456"},
        {"url": "http://u:hunter2pw@openwa/?t=1"},
    ]
    for body in bodies:
        r = api.post("/api/openwa/setup/connect", json=body)
        assert r.status_code == 422
        assert r.json()["detail"][0]["loc"] and r.json()["detail"][0]["msg"]
        for leak in ("key-value-XYZ", "with space", "hunter2pw", "t=1"):
            assert leak not in r.text, leak


# --- F1: a new webhook secret stays pending until OpenWA has it --------------------------

NEW_SECRET = "rotated-webhook-secret-9876543210"


def deliver(api, secret: str) -> int:
    """A signed non-message event: 200 ("ignored") when the signature matches, else 401."""
    import hashlib
    import hmac

    from kino.openwa.schemas import SIGNATURE_HEADER

    body = json.dumps({"event": "session.status"}).encode()
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    r = api.post("/api/webhook/openwa", content=body, headers={SIGNATURE_HEADER: f"sha256={mac}"})
    return r.status_code


def configured(api, owa) -> None:
    assert connect(api, url="http://openwa:2785", api_key=API_KEY, webhook_secret=SECRET).is_success
    r = api.post("/api/openwa/setup/session", json={"session_id": "main"})
    assert r.json()["registered"] is True


def test_fresh_install_stores_secret_directly(api, owa):
    connect(api, url="http://openwa:2785", api_key=API_KEY, webhook_secret=SECRET)
    assert status(api)["webhook_secret_pending"] is False
    assert deliver(api, SECRET) == 200


def test_new_secret_without_session_step_keeps_old_active(api, owa):
    configured(api, owa)
    r = connect(api, webhook_secret=NEW_SECRET)
    assert r.status_code == 200, r.text
    assert status(api)["webhook_secret_pending"] is True
    assert deliver(api, SECRET) == 200
    assert deliver(api, NEW_SECRET) == 401


def test_session_step_promotes_pending_secret(api, owa):
    configured(api, owa)
    connect(api, webhook_secret=NEW_SECRET)
    r = api.post("/api/openwa/setup/session", json={"session_id": "main"})
    assert r.json()["registered"] is True
    assert owa.calls[-1][2]["secret"] == NEW_SECRET
    assert status(api)["webhook_secret_pending"] is False
    assert deliver(api, NEW_SECRET) == 200
    assert deliver(api, SECRET) == 401


def test_registration_failure_keeps_pending_and_old_active(api, owa):
    configured(api, owa)
    connect(api, webhook_secret=NEW_SECRET)
    owa.fail_webhooks = True
    r = api.post("/api/openwa/setup/session", json={"session_id": "main"})
    assert r.status_code == 200 and r.json()["registered"] is False and r.json()["error"]
    assert status(api)["webhook_secret_pending"] is True
    assert deliver(api, SECRET) == 200
    assert deliver(api, NEW_SECRET) == 401


def test_retry_route_registers_and_promotes_pending(api, owa):
    configured(api, owa)
    connect(api, webhook_secret=NEW_SECRET)
    owa.fail_webhooks = True
    api.post("/api/openwa/setup/session", json={"session_id": "main"})
    owa.fail_webhooks = False
    r = api.post("/api/openwa/webhook/register", json={})
    assert r.status_code == 200, r.text
    assert owa.calls[-1][2]["secret"] == NEW_SECRET
    assert status(api)["webhook_secret_pending"] is False
    assert deliver(api, NEW_SECRET) == 200
    assert deliver(api, SECRET) == 401


def test_reentering_the_active_secret_drops_pending(api, owa):
    configured(api, owa)
    connect(api, webhook_secret=NEW_SECRET)
    connect(api, webhook_secret=SECRET)
    assert status(api)["webhook_secret_pending"] is False
    assert deliver(api, SECRET) == 200


def test_pending_secret_never_returned(api, owa):
    configured(api, owa)
    texts = [connect(api, webhook_secret=NEW_SECRET).text]
    texts.append(api.get("/api/openwa/setup/status").text)
    texts.append(api.post("/api/openwa/setup/session", json={"session_id": "main"}).text)
    texts.append(api.post("/api/openwa/webhook/register", json={}).text)
    for t in texts:
        assert NEW_SECRET not in t and SECRET not in t


# --- F2: a different OpenWA URL drops the session picked on the old host -------------------


def test_url_change_clears_the_session(api, owa):
    configured(api, owa)
    r = connect(api, url="http://openwa-new:2785", api_key=API_KEY)
    assert r.status_code == 200, r.text
    s = status(api)
    assert s["configured"] is False
    assert s["session_id"] is None and s["session_name"] is None
    assert load_openwa_config(api.app.state.settings).session_id is None


def test_same_url_keeps_the_session(api, owa):
    configured(api, owa)
    r = connect(api, url="HTTP://OpenWA:2785/", api_key=API_KEY)
    assert r.status_code == 200, r.text
    s = status(api)
    assert s["configured"] is True
    assert s["session_id"] == "main" and s["session_name"] == "Main phone"


def test_url_change_keeps_an_env_locked_session(api, owa):
    api.app.state.settings = api.app.state.settings.model_copy(update={"openwa_session_id": "main"})
    connect(api, url="http://openwa:2785", api_key=API_KEY, webhook_secret=SECRET)
    r = connect(api, url="http://openwa-new:2785", api_key=API_KEY)
    assert r.status_code == 200, r.text
    assert status(api)["session_id"] == "main"


# --- F3: the saved API key is never sent to a newly typed host -----------------------------


def test_url_change_without_key_is_400_and_sends_nothing(api, owa):
    configured(api, owa)
    before = len(owa.hosts)
    r = connect(api, url="http://evil.example:2785")
    assert r.status_code == 400
    assert r.json()["detail"] == "Enter the API key for the new URL."
    assert len(owa.hosts) == before
    assert status(api)["url"] == "http://openwa:2785"


def test_url_change_with_key_connects(api, owa):
    configured(api, owa)
    r = connect(api, url="http://openwa-new:2785", api_key=API_KEY)
    assert r.status_code == 200, r.text
    assert owa.hosts[-1] == "openwa-new"


def test_same_url_without_key_keeps_the_stored_key(api, owa):
    configured(api, owa)
    r = connect(api, url="http://openwa:2785/")
    assert r.status_code == 200, r.text
    assert load_openwa_config(api.app.state.settings).api_key == API_KEY


def test_env_locked_key_cannot_follow_a_new_url(api, owa):
    api.app.state.settings = api.app.state.settings.model_copy(
        update={"openwa_api_key": SecretStr(API_KEY)}
    )
    assert connect(api, url="http://openwa:2785", webhook_secret=SECRET).status_code == 200
    before = len(owa.hosts)
    r = connect(api, url="http://evil.example:2785")
    assert r.status_code == 400 and "KINO_OPENWA_API_KEY" in r.json()["detail"]
    assert len(owa.hosts) == before
    assert status(api)["url"] == "http://openwa:2785"


@pytest.mark.parametrize("blank", ["", "   "])
def test_url_change_with_blank_key_is_400_and_sends_nothing(api, owa, blank):
    configured(api, owa)
    before = len(owa.hosts)
    r = connect(api, url="http://evil.example:2785", api_key=blank)
    assert r.status_code == 400
    assert r.json()["detail"] == "Enter the API key for the new URL."
    assert len(owa.hosts) == before


# --- F4: a reset while a client is being built never leaves a stale client cached ----------


def test_reset_during_build_does_not_cache_the_stale_client(api, owa, monkeypatch):
    import asyncio

    configured(api, owa)
    app = api.app
    asyncio.run(deps.reset_openwa(app))
    real_load = deps.load_openwa_config
    stale = real_load(app.state.settings)
    raced = {"done": False}

    def load_then_reconnect(env):
        cfg = real_load(env)
        if not raced["done"]:  # the wizard saves a new host and resets meanwhile
            raced["done"] = True
            connect(api, url="http://openwa-new:2785", api_key=API_KEY)
            api.post("/api/openwa/setup/session", json={"session_id": "main"})
            asyncio.run(deps.reset_openwa(app))
            return stale
        return cfg

    monkeypatch.setattr(deps, "load_openwa_config", load_then_reconnect)
    client = deps.current_openwa(app)
    assert client is not None and "openwa-new" in str(client._http.base_url)
    assert app.state.openwa is None or "openwa-new" in str(app.state.openwa._http.base_url)


def test_webhook_cfg_loaded_before_a_reset_is_not_cached(api, owa):
    import asyncio

    configured(api, owa)
    app = api.app
    asyncio.run(deps.reset_openwa(app))
    generation = deps.openwa_generation(app)
    stale = load_openwa_config(app.state.settings)
    connect(api, url="http://openwa-new:2785", api_key=API_KEY)
    api.post("/api/openwa/setup/session", json={"session_id": "main"})
    asyncio.run(deps.reset_openwa(app))
    client = deps.current_openwa(app, stale, generation=generation)
    assert client is not None and "openwa-new" in str(client._http.base_url)


# --- separate outgoing session ----------------------------------------------------------------

DEST = "972500000001@c.us"
OUTGOING = "/api/openwa/setup/outgoing-session"


def sent_paths(owa) -> list[str]:
    return [p for _, p, _ in owa.calls if "/sessions/" in p]


def test_sessions_list_needs_a_connection(api, owa):
    r = api.get("/api/openwa/setup/sessions")
    assert r.status_code == 409 and "Connect to OpenWA first" in r.json()["detail"]
    assert owa.calls == []


def test_sessions_list_masks_phone_and_hides_secrets(api, owa):
    configured(api, owa)
    r = api.get("/api/openwa/setup/sessions")
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"id": "main", "name": "Main phone", "status": "ready", "phone_masked": "9725****567"},
        {"id": "spare", "name": "טלפון", "status": "qr", "phone_masked": None},
    ]
    assert PHONE not in r.text and API_KEY not in r.text and SECRET not in r.text


def test_sessions_list_openwa_down(api, owa):
    configured(api, owa)
    owa.down = True
    assert api.get("/api/openwa/setup/sessions").status_code == 502


def test_outgoing_session_saved_and_reported(api, owa):
    configured(api, owa)
    calls = len(owa.calls)
    r = api.put(OUTGOING, json={"session_id": "spare"})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "outgoing_session_id": "spare",
        "outgoing_session_name": "טלפון",
        "same_as_incoming": False,
    }
    s = status(api)
    assert (s["session_id"], s["session_name"]) == ("main", "Main phone")  # incoming untouched
    assert (s["outgoing_session_id"], s["outgoing_session_name"]) == ("spare", "טלפון")
    assert s["same_as_incoming"] is False
    # only the session list was fetched: no webhook registered or changed
    assert [(m, p) for m, p, _ in owa.calls[calls:]] == [("GET", "/api/sessions")]
    assert API_KEY not in r.text and SECRET not in r.text


@pytest.mark.parametrize(
    "body", [{"session_id": None}, {"session_id": ""}, {"session_id": "  "}, {}]
)
def test_outgoing_session_cleared(api, owa, body):
    configured(api, owa)
    api.put(OUTGOING, json={"session_id": "spare"})
    calls = len(owa.calls)
    r = api.put(OUTGOING, json=body)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "outgoing_session_id": "main",
        "outgoing_session_name": "Main phone",
        "same_as_incoming": True,
    }
    assert status(api)["same_as_incoming"] is True
    assert owa.calls[calls:] == []  # clearing needs no OpenWA call


def test_outgoing_session_unknown_id_rejected(api, owa):
    configured(api, owa)
    r = api.put(OUTGOING, json={"session_id": "gone"})
    assert r.status_code == 404 and "no longer exists" in r.json()["detail"]
    assert status(api)["same_as_incoming"] is True


def test_outgoing_session_needs_a_connection(api, owa):
    r = api.put(OUTGOING, json={"session_id": "spare"})
    assert r.status_code == 409


def test_outgoing_session_invalid_body_does_not_echo(api, owa):
    r = api.put(OUTGOING, json={"session_id": "has space"})
    assert r.status_code == 422 and "has space" not in r.text


def test_pick_session_does_not_touch_outgoing(api, owa):
    configured(api, owa)
    api.put(OUTGOING, json={"session_id": "spare"})
    api.post("/api/openwa/setup/session", json={"session_id": "main"})
    assert status(api)["outgoing_session_id"] == "spare"


def test_reconnecting_to_another_url_drops_the_outgoing_session(api, owa):
    configured(api, owa)
    api.put(OUTGOING, json={"session_id": "spare"})
    connect(api, url="http://openwa-b:2785", api_key=API_KEY)
    s = status(api)
    assert s["same_as_incoming"] is True and s["outgoing_session_id"] is None


def test_chats_and_test_photo_use_the_outgoing_session(api, owa):
    configured(api, owa)
    assert api.get("/api/openwa/chats").status_code == 200
    assert owa.calls[-1][1] == "/api/sessions/main/chats"  # unchanged without an outgoing one
    assert api.post("/api/forward/test", json={"chat_id": DEST}).status_code == 200
    assert owa.calls[-1][1] == "/api/sessions/main/messages/send-image"

    api.put(OUTGOING, json={"session_id": "spare"})
    assert api.get("/api/openwa/chats").status_code == 200
    assert owa.calls[-1][1] == "/api/sessions/spare/chats"
    api.app.state.forward_test_at = None
    assert api.post("/api/forward/test", json={"chat_id": DEST}).status_code == 200
    assert owa.calls[-1][1] == "/api/sessions/spare/messages/send-image"

    api.put(OUTGOING, json={"session_id": None})  # back to the incoming session
    assert api.get("/api/openwa/chats").status_code == 200
    assert owa.calls[-1][1] == "/api/sessions/main/chats"


def test_incoming_users_stay_on_the_incoming_session(api, owa):
    configured(api, owa)
    api.put(OUTGOING, json={"session_id": "spare"})
    r = api.get("/api/openwa/webhook")
    assert r.status_code == 200
    assert owa.calls[-1][1] == "/api/sessions/main/webhooks"
    assert r.status_code == 200 and "spare" not in str([p for _, p, _ in owa.calls[-1:]])
    api.post("/api/openwa/webhook/register", json={})
    assert all("/spare/" not in p for _, p, _ in owa.calls[-4:])
    assert owa.calls[-1][1].startswith("/api/sessions/main/webhooks")
