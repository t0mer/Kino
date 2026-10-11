"""REST: settings, forwarding, OpenWA helpers and stats (CLAUDE.md §9.9, §11.7, §12, R5, R6)."""

from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from PIL import Image as PILImage

from kino.db import session as dbs
from kino.db.models import Forward, Group, Image, Kid, Match, SavedPhoto, Setting
from kino.openwa.client import OpenWAClient

GID = "120363000111222333@g.us"
DEST = "972500000001@c.us"
WEBHOOK_SECRET = "api-test-webhook-secret"


def seed_group(chat_id: str = GID, watched: bool = False) -> int:
    with dbs.get_session() as s:
        g = Group(chat_id=chat_id, name="Class", slug="class", watched=watched)
        s.add(g)
        s.flush()
        return g.id


def seed_kid(name: str = "Noa") -> int:
    with dbs.get_session() as s:
        k = Kid(display_name=name, slug=name.lower())
        s.add(k)
        s.flush()
        return k.id


class FakeOpenWA:
    """Records calls; ``hooks`` is what GET /webhooks returns."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self.hooks: list[dict[str, object]] = []
        self.chats: list[dict[str, object]] = [
            {"id": DEST, "name": "Mom", "isGroup": False},
            {"id": GID, "name": "Class 3B", "isGroup": True},
            {"id": "999@g.us", "name": "Family", "isGroup": True},
        ]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        path = req.url.path.removeprefix("/api/sessions/sess/")
        self.calls.append((req.method, path, body))
        if path == "chats":
            return httpx.Response(200, json=self.chats if req.url.params["offset"] == "0" else [])
        if path == "webhooks" and req.method == "GET":
            return httpx.Response(200, json=self.hooks)
        if path == "webhooks" and req.method == "POST":
            return httpx.Response(201, json={"id": "h-1", **(body or {})})
        if path.startswith("webhooks/"):
            return httpx.Response(200, json={"id": path.split("/")[1]})
        if path == "messages/send-image":
            return httpx.Response(201, json={"messageId": "true_x_ID"})
        return httpx.Response(404)


@pytest.fixture
def owa(api):
    fake = FakeOpenWA()
    api.app.state.openwa = OpenWAClient(
        "http://owa:2785", "secret-api-key", "sess", transport=httpx.MockTransport(fake)
    )
    return fake


# --- settings: overview ---------------------------------------------------------------------


def test_settings_overview_has_sections_models_and_version(api):
    r = api.get("/api/settings")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"forward", "digest", "thresholds", "retention", "models", "version"}
    assert body["forward"]["enabled"] is False
    assert body["forward"]["caption_template"] == "📸 {kids} · {group} · {date}"
    assert body["retention"] == {"raw_days": 7, "face_days": 30}
    assert body["digest"]["cron"] == "0 20 * * *"
    assert body["thresholds"]["L"] == {"auto": 0.45, "review": 0.32}
    names = {m["name"] for m in body["models"]}
    assert names == {"scrfd_10g", "auraface_glintr100"}
    assert all(m["license"] and len(m["sha256"]) == 64 for m in body["models"])
    assert WEBHOOK_SECRET not in r.text and "api-test-cookie-key" not in r.text


def test_unknown_section_is_404(api):
    assert api.get("/api/settings/bogus").status_code == 404
    assert api.put("/api/settings/bogus", json={}).status_code in (404, 405)


def test_settings_require_csrf(api):
    api.headers.pop("X-CSRF-Token")
    assert (
        api.put("/api/settings/retention", json={"raw_days": 1, "face_days": 1}).status_code == 403
    )


# --- forward ----------------------------------------------------------------------------------


def forward_body(**over):
    body = {
        "enabled": True,
        "default_chat_id": DEST,
        "per_kid": {},
        "caption_template": "{kids} @ {group}",
        "include_review_confirmed": False,
    }
    body.update(over)
    return body


def test_put_forward_roundtrip(api):
    noa = seed_kid()
    r = api.put("/api/settings/forward", json=forward_body(per_kid={str(noa): "x@c.us"}))
    assert r.status_code == 200, r.text
    got = api.get("/api/settings/forward").json()
    assert got == {
        "enabled": True,
        "default_chat_id": DEST,
        "per_kid": {str(noa): "x@c.us"},
        "caption_template": "{kids} @ {group}",
        "include_review_confirmed": False,
    }
    # disabling keeps the destination
    api.put("/api/settings/forward", json=forward_body(enabled=False))
    got = api.get("/api/settings/forward").json()
    assert got["enabled"] is False and got["default_chat_id"] == DEST


def test_put_forward_rejects_watched_group_destination(api):
    seed_group(watched=True)
    noa = seed_kid()
    r = api.put("/api/settings/forward", json=forward_body(default_chat_id=GID))
    assert r.status_code == 409 and "watched group" in r.json()["detail"]
    r = api.put("/api/settings/forward", json=forward_body(per_kid={str(noa): GID}))
    assert r.status_code == 409
    assert api.get("/api/settings/forward").json()["enabled"] is False


def test_watching_destination_rejected_after_setting_it(api):
    gid = seed_group(watched=False)
    assert (
        api.put("/api/settings/forward", json=forward_body(default_chat_id=GID)).status_code == 200
    )
    r = api.patch(f"/api/groups/{gid}", json={"watched": True})
    assert r.status_code == 409 and "forward destination" in r.json()["detail"]


def test_put_forward_validation(api):
    assert (
        api.put("/api/settings/forward", json=forward_body(default_chat_id=None)).status_code == 422
    )
    assert (
        api.put("/api/settings/forward", json=forward_body(per_kid={"77": "x@c.us"})).status_code
        == 422
    )
    assert (
        api.put("/api/settings/forward", json=forward_body(caption_template="")).status_code == 422
    )
    assert (
        api.put("/api/settings/forward", json=forward_body(caption_template="x" * 1025)).status_code
        == 422
    )
    assert api.put("/api/settings/forward", json=forward_body(extra=1)).status_code == 422
    assert (
        api.put("/api/settings/forward", json=forward_body(default_chat_id="a b")).status_code
        == 422
    )
    # disabled with no destination is fine
    r = api.put("/api/settings/forward", json=forward_body(enabled=False, default_chat_id=None))
    assert r.status_code == 200


def test_caption_preview(api):
    seed_kid("נועה")
    r = api.post(
        "/api/settings/forward/preview", json={"caption_template": "{kids}|{group}|{sender}|{x}"}
    )
    assert r.status_code == 200, r.text
    kids, group, sender, unknown = r.json()["caption"].split("|")
    assert kids and group and sender and unknown == "{x}"


# --- thresholds / retention / digest ----------------------------------------------------------

THRESHOLDS = {
    "L": {"auto": 0.5, "review": 0.3},
    "M": {"auto": 0.55, "review": 0.36},
    "S": {"review": 0.42},
    "prior_bonus": 0.02,
    "unmapped_penalty": 0.06,
    "margin_min": 0.1,
}


def test_thresholds_roundtrip_feeds_pipeline(api):
    from kino.pipeline.decide import Thresholds

    r = api.put("/api/settings/thresholds", json=THRESHOLDS)
    assert r.status_code == 200, r.text
    got = api.get("/api/settings/thresholds").json()
    assert {k: got[k] for k in THRESHOLDS} == THRESHOLDS
    assert got["auto_floor"] == 0.3
    with dbs.get_session() as s:
        t = Thresholds.from_settings(s)
    assert (t.L_auto, t.M_review, t.S_review, t.margin_min) == (0.5, 0.36, 0.42, 0.1)


@pytest.mark.parametrize(
    "patch",
    [
        {"L": {"auto": 0.2, "review": 0.3}},  # auto below review
        {"M": {"auto": 1.5, "review": 0.3}},
        {"margin_min": -2},
        {"S": {"review": 0.4, "auto": 0.5}},  # tier S never auto-saves
        {"prior_bonus": "x"},
    ],
)
def test_thresholds_invalid(api, patch):
    assert api.put("/api/settings/thresholds", json={**THRESHOLDS, **patch}).status_code == 422
    assert api.get("/api/settings/thresholds").json()["L"]["auto"] == 0.45


def test_retention(api):
    assert (
        api.put("/api/settings/retention", json={"raw_days": 3, "face_days": 60}).status_code == 200
    )
    assert api.get("/api/settings/retention").json() == {"raw_days": 3, "face_days": 60}
    assert (
        api.put("/api/settings/retention", json={"raw_days": 0, "face_days": 60}).status_code == 422
    )
    assert api.put("/api/settings/retention", json={"raw_days": 3}).status_code == 422


def test_digest_masks_apprise_urls(api):
    body = {
        "chat_id": DEST,
        "cron": "*/30 8-20 * * 1-5",
        "apprise_urls": ["tgram://123456:SECRETTOKEN/987", "mailto://user:pw@example.com"],
        "public_base_url": "https://kino.example.com",
    }
    r = api.put("/api/settings/digest", json=body)
    assert r.status_code == 200, r.text
    got = api.get("/api/settings/digest").json()
    assert got["chat_id"] == DEST and got["cron"] == body["cron"]
    assert got["public_base_url"] == "https://kino.example.com"
    assert got["apprise_urls"] == ["tgram://****", "mailto://****"]
    assert "SECRETTOKEN" not in api.get("/api/settings").text
    # omitting apprise_urls keeps the stored ones
    r = api.put("/api/settings/digest", json={**body, "apprise_urls": None, "cron": ""})
    assert r.status_code == 200
    with dbs.get_session() as s:
        stored = s.get(Setting, "digest").value
    assert stored["apprise_urls"][0].startswith("tgram://123456")
    assert stored["cron"] == ""


@pytest.mark.parametrize(
    "patch",
    [
        {"cron": "99 * * * *"},
        {"cron": "* * *"},
        {"public_base_url": "javascript:alert(1)"},
        {"apprise_urls": ["no-scheme"]},
    ],
)
def test_digest_invalid(api, patch):
    body = {"chat_id": None, "cron": "0 20 * * *", "apprise_urls": [], "public_base_url": None}
    assert api.put("/api/settings/digest", json={**body, **patch}).status_code == 422


# --- OpenWA helpers ---------------------------------------------------------------------------


def test_chats_marks_watched_groups_unselectable(api, owa):
    seed_group(GID, watched=True)
    r = api.get("/api/openwa/chats")
    assert r.status_code == 200, r.text
    by_id = {c["id"]: c for c in r.json()}
    assert by_id[GID] == {
        "id": GID,
        "name": "Class 3B",
        "is_group": True,
        "watched": True,
        "selectable": False,
    }
    assert by_id[DEST]["selectable"] is True and by_id[DEST]["is_group"] is False
    assert [c["id"] for c in api.get("/api/openwa/chats", params={"q": "fam"}).json()] == [
        "999@g.us"
    ]


def test_chats_openwa_errors(api):
    assert api.get("/api/openwa/chats").status_code == 503

    def down(req):
        raise httpx.ConnectTimeout("timeout", request=req)

    api.app.state.openwa = OpenWAClient(
        "http://owa", "secret-api-key", "sess", transport=httpx.MockTransport(down)
    )
    r = api.get("/api/openwa/chats")
    assert r.status_code == 502 and r.json() == {"detail": "OpenWA is unreachable"}


def test_webhook_status_and_register(api, owa):
    r = api.get("/api/openwa/webhook")
    assert r.status_code == 200, r.text
    assert r.json()["registered"] is False

    r = api.post(
        "/api/openwa/webhook/register", json={"url": "https://kino.example.com/api/webhook/openwa"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "created"
    method, path, body = owa.calls[-1]
    assert (method, path) == ("POST", "webhooks")
    assert body["secret"] == WEBHOOK_SECRET  # sent to OpenWA ...
    assert WEBHOOK_SECRET not in r.text  # ... never returned

    owa.hooks = [
        {
            "id": "h-1",
            "url": "https://kino.example.com/api/webhook/openwa",
            "events": ["message.received"],
            "active": True,
            "secret": WEBHOOK_SECRET,
        },
        {"id": "h-9", "url": "http://other/hook", "events": ["x"], "active": True},
    ]
    r = api.get("/api/openwa/webhook")
    assert r.json() == {
        "registered": True,
        "active": True,
        "id": "h-1",
        "url": "https://kino.example.com/api/webhook/openwa",
        "events": ["message.received"],
    }
    assert WEBHOOK_SECRET not in r.text
    # re-register without a URL reuses the stored one and updates in place
    r = api.post("/api/openwa/webhook/register", json={})
    assert r.json()["action"] == "updated"
    assert owa.calls[-1][:2] == ("PUT", "webhooks/h-1")


def test_register_rejects_bad_url(api, owa):
    r = api.post("/api/openwa/webhook/register", json={"url": "ftp://x/y"})
    assert r.status_code == 422


def test_register_default_url_uses_public_base(api, owa):
    api.put(
        "/api/settings/digest",
        json={
            "chat_id": None,
            "cron": "",
            "apprise_urls": [],
            "public_base_url": "https://k.example",
        },
    )
    assert api.post("/api/openwa/webhook/register", json={}).status_code == 200
    assert owa.calls[-1][2]["url"] == "https://k.example/api/webhook/openwa"


def test_webhook_status_openwa_down(api):
    api.app.state.openwa = OpenWAClient(
        "http://owa",
        "secret-api-key",
        "sess",
        transport=httpx.MockTransport(lambda r: httpx.Response(500)),
    )
    r = api.get("/api/openwa/webhook")
    assert r.status_code == 502 and "secret-api-key" not in r.text


# --- forward test / forwards ------------------------------------------------------------------


def test_forward_test_sends_photo(api, owa):
    r = api.post("/api/forward/test", json={"chat_id": DEST})
    assert r.status_code == 200, r.text
    method, path, body = owa.calls[-1]
    assert path == "messages/send-image" and body["chatId"] == DEST
    img = PILImage.open(io.BytesIO(base64.b64decode(body["base64"])))
    assert img.format == "JPEG"
    assert body["mimetype"] == "image/jpeg" and "Kino" in body["caption"]


def test_forward_test_rejects_watched_group(api, owa):
    seed_group(GID, watched=True)
    r = api.post("/api/forward/test", json={"chat_id": GID})
    assert r.status_code == 409
    assert not any(p == "messages/send-image" for _, p, _ in owa.calls)


def seed_forwards() -> tuple[int, int]:
    with dbs.get_session() as s:
        img = Image(message_id="m1", status="done")
        s.add(img)
        s.flush()
        failed = Forward(
            image_id=img.id, dest_chat_id=DEST, status="failed", attempts=4, last_error="boom"
        )
        sent = Forward(image_id=img.id, dest_chat_id="o@c.us", status="sent")
        s.add_all([failed, sent])
        s.flush()
        return failed.id, sent.id


def test_forwards_list_and_retry(api, monkeypatch):
    from kino import forward

    failed, sent = seed_forwards()
    rows = api.get("/api/forwards").json()
    assert [r["id"] for r in rows] == [sent, failed]
    rows = api.get("/api/forwards", params={"status": "failed"}).json()
    assert len(rows) == 1 and rows[0]["last_error"] == "boom" and rows[0]["attempts"] == 4
    assert api.get("/api/forwards", params={"status": "bogus"}).status_code == 422

    queued: list[int] = []
    monkeypatch.setattr(forward, "send_forward", queued.append)
    r = api.post(f"/api/forwards/{failed}/retry")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending" and r.json()["attempts"] == 0
    assert queued == [failed]
    assert api.post(f"/api/forwards/{sent}/retry").status_code == 409
    assert api.post("/api/forwards/999/retry").status_code == 404
    assert queued == [failed]


# --- stats ------------------------------------------------------------------------------------


def test_stats(api):
    noa = seed_kid()
    seed_forwards()
    with dbs.get_session() as s:
        imgs = [
            Image(message_id=f"s{i}", status=st)
            for i, st in enumerate(["queued", "queued", "done"])
        ]
        s.add_all(imgs)
        s.flush()
        from kino.db.models import Face

        face = Face(
            image_id=imgs[2].id, bbox_x=0, bbox_y=0, bbox_w=1, bbox_h=1, det_score=1, size_px=90
        )
        s.add(face)
        s.flush()
        s.add(Match(face_id=face.id, kid_id=noa, score=0.4, decision="review"))
        s.add(SavedPhoto(image_id=imgs[2].id, kid_id=noa, path="/p.jpg"))
        s.add(Forward(image_id=imgs[2].id, dest_chat_id=DEST, status="pending"))
    r = api.get("/api/stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["images"]["queued"] == 2 and body["images"]["done"] == 2
    assert body["queue_depth"] == 2
    assert body["review_count"] == 1
    assert body["forwards"] == {"pending": 1, "sent": 1, "failed": 1, "cancelled": 0}
    assert body["forward_queue_depth"] == 1
    assert body["kids"] == [{"id": noa, "display_name": "Noa", "today": 1, "last_7_days": 1}]


def test_new_routes_need_auth(api):
    api.cookies.clear()
    for path in ("/api/settings", "/api/openwa/chats", "/api/forwards", "/api/stats"):
        assert api.get(path).status_code == 401, path
