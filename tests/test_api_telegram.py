"""REST: Telegram delivery settings, chat linking and Telegram destinations."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from loguru import logger

from kino.api.routes import forwards as fwd_route
from kino.db import session as dbs
from kino.db.models import Forward, Image, Kid, Setting, TelegramChat
from kino.delivery.telegram import TelegramChannel
from kino.services import telegram_link as link

TOKEN = "123456789:AAHabcdefghijklmnopqrstuvwxyz012345"
OWA_DEST = "972500000001@c.us"
GROUP = {"id": -1001234567890, "type": "supergroup", "title": "Class 3B parents"}
PRIVATE = {"id": 4242, "type": "private", "first_name": "Dana", "last_name": "Levi"}


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, list[str]]]] = []
        self.updates: list[dict] = []
        self.me_status = 200
        self.updates_status = 200

    def __call__(self, req: httpx.Request) -> httpx.Response:
        method = req.url.path.rsplit("/", 1)[-1]
        ctype = req.headers.get("content-type", "")
        form = parse_qs(req.content.decode()) if "urlencoded" in ctype else {}
        self.calls.append((method, form))
        if method == "getMe":
            if self.me_status != 200:
                return httpx.Response(
                    self.me_status, json={"ok": False, "description": "Unauthorized"}
                )
            return httpx.Response(200, json={"ok": True, "result": {"username": "kino_bot"}})
        if method == "getUpdates":
            if self.updates_status != 200:
                return httpx.Response(
                    self.updates_status,
                    json={
                        "ok": False,
                        "error_code": self.updates_status,
                        "description": "Conflict",
                    },
                )
            return httpx.Response(200, json={"ok": True, "result": self.updates})
        if method in ("sendPhoto", "sendDocument"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})
        return httpx.Response(404, json={"ok": False, "description": "nope"})

    def names(self) -> list[str]:
        return [m for m, _ in self.calls]


@pytest.fixture
def tg(api):
    fake = FakeTelegram()

    def factory(token: str, api_url: str, send_as_file: bool) -> TelegramChannel:
        return TelegramChannel(
            token, api_url, send_as_file=send_as_file, transport=httpx.MockTransport(fake)
        )

    api.app.state.telegram_factory = factory
    return fake


@pytest.fixture
def configured(api, tg):
    r = api.put("/api/settings/telegram", json={"bot_token": TOKEN})
    assert r.status_code == 200, r.text
    return tg


def msg(chat: dict, text: str, update_id: int = 1) -> dict:
    return {"update_id": update_id, "message": {"message_id": 5, "chat": chat, "text": text}}


def link_code(api) -> str:
    r = api.post("/api/telegram/link")
    assert r.status_code == 200, r.text
    return r.json()["code"]


def add_chat(chat_id: str = "-100777", title: str = "Family", type_: str = "group") -> int:
    with dbs.get_session() as s:
        row = TelegramChat(chat_id=chat_id, title=title, type=type_)
        s.add(row)
        s.flush()
        return row.id


def fwd_body(**over):
    body = {
        "enabled": True,
        "caption_template": "{kids}",
        "include_review_confirmed": False,
        "send_caption": True,
    }
    body.update(over)
    return body


# --- auth / csrf ------------------------------------------------------------------------------

ROUTES = [
    ("get", "/api/settings/telegram"),
    ("put", "/api/settings/telegram"),
    ("post", "/api/telegram/test"),
    ("post", "/api/telegram/link"),
    ("get", "/api/telegram/link/ABC234"),
    ("get", "/api/telegram/chats"),
    ("delete", "/api/telegram/chats/1"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_requires_auth(api, method, path):
    anon = TestClient(api.app)
    anon.cookies.set("kino_csrf", "x")
    r = anon.request(
        method, path, headers={"X-CSRF-Token": "x"}, json={} if method == "put" else None
    )
    assert r.status_code == 401


@pytest.mark.parametrize(("method", "path"), [r for r in ROUTES if r[0] != "get"])
def test_requires_csrf(api, method, path):
    api.headers.pop("X-CSRF-Token")
    r = api.request(method, path, json={} if method == "put" else None)
    assert r.status_code == 403


# --- settings ---------------------------------------------------------------------------------


def test_defaults(api, tg):
    body = api.get("/api/settings/telegram").json()
    assert body["has_token"] is False and body["bot_username"] is None
    assert body["send_as_file"] is False and body["delete_on_remove"] is False
    assert body["env_overrides"] == [] and body["api_url"].startswith("https://")
    assert set(body) == {
        "has_token",
        "bot_username",
        "api_url",
        "rate_per_min",
        "send_as_file",
        "delete_on_remove",
        "env_overrides",
    }
    assert api.get("/api/settings").json()["telegram"] == body


def test_save_token_validates_stores_encrypted_and_never_leaks(api, tg):
    sink: list[str] = []
    hid = logger.add(lambda m: sink.append(str(m)), level="DEBUG")
    try:
        r = api.put("/api/settings/telegram", json={"bot_token": f" {TOKEN} "})
        assert r.status_code == 200, r.text
        assert r.json()["has_token"] is True and r.json()["bot_username"] == "kino_bot"
        assert tg.names() == ["getMe"]
        with dbs.get_session() as s:
            stored = s.get(Setting, "telegram.bot_token_enc").value
        assert TOKEN not in str(stored)
        bodies = [
            api.get("/api/settings/telegram").text,
            api.get("/api/settings").text,
            api.post("/api/telegram/test").text,
            api.post("/api/telegram/link").text,
        ]
        assert not any(TOKEN in b for b in bodies)
    finally:
        logger.remove(hid)
    assert not any(TOKEN in line for line in sink)


def test_getme_failure_is_422_and_nothing_saved(api, tg):
    tg.me_status = 401
    r = api.put("/api/settings/telegram", json={"bot_token": TOKEN})
    assert r.status_code == 422 and TOKEN not in r.text
    assert api.get("/api/settings/telegram").json()["has_token"] is False
    with dbs.get_session() as s:
        assert s.get(Setting, "telegram.bot_token_enc") is None


def test_bad_token_shape_is_422_without_network(api, tg):
    r = api.put("/api/settings/telegram", json={"bot_token": "not a token"})
    assert r.status_code == 422 and tg.calls == []


def test_clear_token_and_options(api, configured):
    r = api.put("/api/settings/telegram", json={"send_as_file": True, "delete_on_remove": True})
    assert r.json()["send_as_file"] and r.json()["delete_on_remove"] and r.json()["has_token"]
    r = api.put("/api/settings/telegram", json={"bot_token": None})
    body = r.json()
    assert body["has_token"] is False and body["bot_username"] is None
    assert body["send_as_file"] is True  # options survive a token change
    api.put("/api/settings/telegram", json={"bot_token": TOKEN})
    assert api.put("/api/settings/telegram", json={"bot_token": ""}).json()["has_token"] is False


def test_env_locked_token(api, tg):
    from pydantic import SecretStr

    api.app.state.settings = api.app.state.settings.model_copy(
        update={"telegram_bot_token": SecretStr(TOKEN)}
    )
    r = api.put("/api/settings/telegram", json={"bot_token": "999:" + "A" * 30})
    assert r.status_code == 409 and "KINO_TELEGRAM_BOT_TOKEN" in r.json()["detail"]
    assert api.put("/api/settings/telegram", json={"bot_token": None}).status_code == 409
    assert tg.calls == []
    body = api.get("/api/settings/telegram").json()
    assert body["env_overrides"] == ["bot_token"] and body["has_token"] is True
    # options stay editable
    assert api.put("/api/settings/telegram", json={"send_as_file": True}).status_code == 200


def test_test_endpoint(api, configured):
    r = api.post("/api/telegram/test")
    assert r.status_code == 200 and r.json() == {"ok": True, "bot_username": "kino_bot"}
    configured.me_status = 401
    r = api.post("/api/telegram/test")
    assert r.status_code == 422 and "invalid bot token" in r.json()["detail"]


def test_test_endpoint_without_token(api, tg):
    assert api.post("/api/telegram/test").status_code == 422


# --- linking ----------------------------------------------------------------------------------


def test_link_needs_token(api, tg):
    assert api.post("/api/telegram/link").status_code == 422


def test_link_start_shape(api, configured):
    r = api.post("/api/telegram/link")
    body = r.json()
    assert set(body) == {"code", "bot_username", "expires_in", "instructions"}
    assert len(body["code"]) == 6 and body["code"].isalnum() and body["code"].isupper()
    assert body["bot_username"] == "kino_bot" and body["expires_in"] == 600
    assert body["code"] in body["instructions"]


def test_link_private_chat_and_offset(api, configured):
    code = link_code(api)
    assert api.get(f"/api/telegram/link/{code}").json() == {"status": "pending", "chat": None}
    configured.updates = [msg({"id": 1, "type": "private", "first_name": "X"}, "hi", 10)]
    configured.updates = [*configured.updates, msg(PRIVATE, f"/start {code}", 11)]
    r = api.get(f"/api/telegram/link/{code}")
    body = r.json()
    assert body["status"] == "linked"
    assert body["chat"]["chat_id"] == "4242" and body["chat"]["title"] == "Dana Levi"
    assert body["chat"]["type"] == "private"
    # offset advanced past the last update; next poll for another code asks from there
    configured.calls.clear()
    code2 = link_code(api)
    configured.updates = []
    api.get(f"/api/telegram/link/{code2}")
    gu = [f for m, f in configured.calls if m == "getUpdates"][0]
    assert gu["offset"] == ["12"] and int(gu["timeout"][0]) <= 10
    assert json.loads(gu["allowed_updates"][0]) == ["message", "channel_post", "my_chat_member"]
    # a linked code keeps answering without polling again
    configured.calls.clear()
    assert api.get(f"/api/telegram/link/{code}").json()["status"] == "linked"
    assert configured.calls == []
    assert [c["chat_id"] for c in api.get("/api/telegram/chats").json()] == ["4242"]


def test_link_supergroup_with_bot_mention(api, configured):
    code = link_code(api)
    configured.updates = [msg(GROUP, f"/start@kino_bot {code.lower()}")]
    body = api.get(f"/api/telegram/link/{code}").json()
    assert body["status"] == "linked"
    assert body["chat"]["chat_id"] == "-1001234567890"
    assert body["chat"]["title"] == "Class 3B parents" and body["chat"]["type"] == "supergroup"


def test_link_channel_post(api, configured):
    code = link_code(api)
    chan = {"id": -1009876543210, "type": "channel", "title": "Announcements"}
    configured.updates = [
        {"update_id": 1, "channel_post": {"message_id": 5, "chat": chan, "text": code}}
    ]
    body = api.get(f"/api/telegram/link/{code}").json()
    assert body["status"] == "linked"
    assert body["chat"]["type"] == "channel" and body["chat"]["title"] == "Announcements"


def test_link_two_codes_one_poll_and_relink_updates_title(api, configured):
    a, b = link_code(api), link_code(api)
    configured.updates = [msg(PRIVATE, f"/start {a}", 1), msg(GROUP, f"hello {b}", 2)]
    assert api.get(f"/api/telegram/link/{a}").json()["status"] == "linked"
    configured.calls.clear()
    assert api.get(f"/api/telegram/link/{b}").json()["status"] == "linked"
    assert configured.calls == []  # b was consumed by a's poll
    c = link_code(api)
    configured.updates = [msg({**GROUP, "title": "Renamed"}, f"/start {c}", 3)]
    api.get(f"/api/telegram/link/{c}")
    chats = api.get("/api/telegram/chats").json()
    assert len(chats) == 2 and {x["title"] for x in chats} == {"Dana Levi", "Renamed"}


def test_link_unknown_expired_and_no_webhook_calls(api, configured, monkeypatch):
    assert api.get("/api/telegram/link/ZZZZZZ").status_code == 404
    now = [1000.0]
    monkeypatch.setattr(
        api.app.state, "telegram_links", link.LinkStore(clock=lambda: now[0]), raising=False
    )
    code = link_code(api)
    now[0] += link.CODE_TTL + 1
    configured.calls.clear()
    assert api.get(f"/api/telegram/link/{code}").json() == {"status": "expired", "chat": None}
    assert configured.calls == []
    now[0] += link.CODE_TTL + 1  # tombstone purged
    assert api.get(f"/api/telegram/link/{code}").status_code == 404
    assert not any("webhook" in n.lower() for n in configured.names())


def test_link_conflict_with_webhook_is_409(api, configured):
    code = link_code(api)
    configured.updates_status = 409
    r = api.get(f"/api/telegram/link/{code}")
    assert r.status_code == 409 and "webhook" in r.json()["detail"]
    assert not any("webhook" in n.lower() for n in configured.names())


def test_live_codes_are_capped(api, configured):
    codes = [link_code(api) for _ in range(link.MAX_CODES + 3)]
    store = api.app.state.telegram_links
    assert len(store.pending()) == link.MAX_CODES
    assert codes[-1] in store.pending() and codes[0] not in store.pending()


# --- chats ------------------------------------------------------------------------------------


def test_chats_list_and_delete(api, configured):
    pk = add_chat()
    got = api.get("/api/telegram/chats").json()
    assert set(got[0]) == {"id", "chat_id", "title", "type", "linked_at"} and got[0]["id"] == pk
    assert api.delete(f"/api/telegram/chats/{pk}").status_code == 204
    assert api.get("/api/telegram/chats").json() == []
    assert api.delete(f"/api/telegram/chats/{pk}").status_code == 404


def test_delete_chat_refused_while_in_use(api, configured):
    pk = add_chat("-100777")
    kid = Kid(display_name="Noa", slug="noa")
    with dbs.get_session() as s:
        s.add(kid)
        s.flush()
        kid_id = kid.id
    other = add_chat("-100888", "Other")
    body = fwd_body(
        default={"channel": "telegram", "chat_id": "-100777"},
        per_kid_destinations={str(kid_id): {"channel": "telegram", "chat_id": "-100888"}},
    )
    assert api.put("/api/settings/forward", json=body).status_code == 200
    assert api.delete(f"/api/telegram/chats/{pk}").status_code == 409
    assert api.delete(f"/api/telegram/chats/{other}").status_code == 409
    third = add_chat("-100999", "Digest")
    d = {"cron": "", "destination": {"channel": "telegram", "chat_id": "-100999"}}
    assert api.put("/api/settings/digest", json=d).status_code == 200
    r = api.delete(f"/api/telegram/chats/{third}")
    assert r.status_code == 409 and "digest" in r.json()["detail"]
    api.put("/api/settings/digest", json={"cron": ""})
    assert api.delete(f"/api/telegram/chats/{third}").status_code == 204


# --- destinations -----------------------------------------------------------------------------


def test_forward_telegram_destination_round_trip(api, configured):
    add_chat("-100777")
    with dbs.get_session() as s:
        k = Kid(display_name="Noa", slug="noa")
        s.add(k)
        s.flush()
        kid_id = k.id
    body = fwd_body(
        default={"channel": "telegram", "chat_id": "-100777"},
        per_kid_destinations={str(kid_id): {"channel": "whatsapp", "chat_id": OWA_DEST}},
    )
    r = api.put("/api/settings/forward", json=body)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["default"] == {"channel": "telegram", "chat_id": "-100777"}
    assert got["per_kid_destinations"] == {
        str(kid_id): {"channel": "whatsapp", "chat_id": OWA_DEST}
    }
    # legacy view: WhatsApp destinations only
    assert got["default_chat_id"] is None and got["per_kid"] == {str(kid_id): OWA_DEST}
    assert api.get("/api/settings/forward").json() == got
    assert api.get("/api/settings").json()["forward"] == got
    # echoing the whole response back is accepted and stable
    assert api.put("/api/settings/forward", json=got).json() == got


def test_forward_legacy_payload_is_whatsapp(api, tg):
    r = api.put(
        "/api/settings/forward",
        json=fwd_body(default_chat_id=OWA_DEST, per_kid={}),
    )
    assert r.status_code == 200
    assert r.json()["default"] == {"channel": "whatsapp", "chat_id": OWA_DEST}
    assert r.json()["default_chat_id"] == OWA_DEST


def test_forward_telegram_validation(api, tg):
    add_chat("-100777")
    dest = {"channel": "telegram", "chat_id": "-100777"}
    r = api.put("/api/settings/forward", json=fwd_body(default=dest))
    assert r.status_code == 422 and "token" in r.json()["detail"]  # no bot token yet
    api.put("/api/settings/telegram", json={"bot_token": TOKEN})
    bad = {"channel": "telegram", "chat_id": "-1"}
    assert api.put("/api/settings/forward", json=fwd_body(default=bad)).status_code == 422
    assert api.put("/api/settings/forward", json=fwd_body(default=dest)).status_code == 200
    bogus = {"channel": "sms", "chat_id": "1"}
    assert api.put("/api/settings/forward", json=fwd_body(default=bogus)).status_code == 422


def test_digest_destination_round_trip_and_legacy(api, configured):
    add_chat("-100777")
    r = api.put(
        "/api/settings/digest",
        json={"cron": "0 20 * * *", "destination": {"channel": "telegram", "chat_id": "-100777"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["destination"] == {"channel": "telegram", "chat_id": "-100777"}
    assert r.json()["chat_id"] is None
    assert api.get("/api/settings/digest").json() == r.json()
    r = api.put("/api/settings/digest", json={"cron": "", "chat_id": OWA_DEST})
    assert r.json()["destination"] == {"channel": "whatsapp", "chat_id": OWA_DEST}
    assert r.json()["chat_id"] == OWA_DEST
    r = api.put(
        "/api/settings/digest",
        json={"cron": "", "destination": {"channel": "telegram", "chat_id": "-1"}},
    )
    assert r.status_code == 422
    r = api.put("/api/settings/digest", json={"cron": "", "destination": None})
    assert r.json()["destination"] is None


# --- forward test -----------------------------------------------------------------------------


def test_forward_test_telegram(api, configured, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(fwd_route, "_clock", lambda: now[0])
    add_chat("-100777")
    unlinked = api.post("/api/forward/test", json={"chat_id": "-5", "channel": "telegram"})
    assert unlinked.status_code == 422 and "sendPhoto" not in configured.names()
    r = api.post("/api/forward/test", json={"chat_id": "-100777", "channel": "telegram"})
    assert r.status_code == 200 and r.json() == {"sent": True}
    assert configured.names()[-1] == "sendPhoto"
    now[0] += 5
    assert (
        api.post(
            "/api/forward/test", json={"chat_id": "-100777", "channel": "telegram"}
        ).status_code
        == 429
    )
    now[0] += 6
    api.put("/api/settings/telegram", json={"send_as_file": True})
    assert (
        api.post(
            "/api/forward/test", json={"chat_id": "-100777", "channel": "telegram"}
        ).status_code
        == 200
    )
    assert configured.names()[-1] == "sendDocument"


def test_forward_test_telegram_needs_no_openwa(api, configured):
    add_chat("-100777")
    r = api.post("/api/forward/test", json={"chat_id": "-100777", "channel": "telegram"})
    assert r.status_code == 200
    r = api.post("/api/forward/test", json={"chat_id": OWA_DEST})  # legacy body, no OpenWA
    assert r.status_code == 503


# --- forward rows -----------------------------------------------------------------------------


def test_forward_out_has_channel(api, tg):
    with dbs.get_session() as s:
        img = Image(message_id="t1", status="done")
        s.add(img)
        s.flush()
        s.add(Forward(image_id=img.id, channel="telegram", dest_chat_id="-1001234567890"))
        s.add(Forward(image_id=img.id, dest_chat_id="972501234567@c.us"))
        image_id = img.id
    rows = {f["channel"]: f for f in api.get("/api/forwards").json()}
    assert rows["telegram"]["dest_chat_id"] == "-1001234567890"
    assert "****" in rows["whatsapp"]["dest_chat_id"]
    detail = api.get(f"/api/images/{image_id}").json()
    assert sorted(f["channel"] for f in detail["forwards"]) == ["telegram", "whatsapp"]
