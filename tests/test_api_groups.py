"""REST: /api/groups (CLAUDE.md §11.7, §12)."""

from __future__ import annotations

import httpx
from sqlalchemy import select

from kino.db import session as dbs
from kino.db.models import Group, Kid, KidGroup
from kino.openwa.client import OpenWAClient

GID = "120363000111222333@g.us"


def seed(chat_id: str = GID, watched: bool = False) -> int:
    with dbs.get_session() as s:
        g = Group(chat_id=chat_id, name="Class", slug="class", watched=watched)
        s.add(g)
        s.flush()
        return g.id


def kid(name: str) -> int:
    with dbs.get_session() as s:
        k = Kid(display_name=name, slug=name.lower())
        s.add(k)
        s.flush()
        return k.id


def use_openwa(api, handler) -> None:
    api.app.state.openwa = OpenWAClient(
        "http://owa:2785", "secret-api-key", "sess", transport=httpx.MockTransport(handler)
    )


def test_requires_auth(api):
    api.cookies.clear()
    assert api.get("/api/groups").status_code == 401


def test_list(api):
    gid = seed()
    noa = kid("Noa")
    with dbs.get_session() as s:
        s.add(KidGroup(kid_id=noa, group_id=gid))
    rows = api.get("/api/groups").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == gid and row["chat_id"] == GID and row["name"] == "Class"
    assert row["watched"] is False and row["kid_ids"] == [noa]
    assert row["is_forward_destination"] is False


def test_patch_watch_and_kids(api):
    gid = seed()
    noa, itai = kid("Noa"), kid("Itai")
    r = api.patch(f"/api/groups/{gid}", json={"watched": True, "kid_ids": [noa, itai]})
    assert r.status_code == 200, r.text
    assert r.json()["watched"] is True and sorted(r.json()["kid_ids"]) == sorted([noa, itai])
    r = api.patch(f"/api/groups/{gid}", json={"kid_ids": [itai]})
    assert r.json()["kid_ids"] == [itai] and r.json()["watched"] is True
    r = api.patch(f"/api/groups/{gid}", json={"watched": False})
    assert r.json()["watched"] is False and r.json()["kid_ids"] == [itai]


def test_patch_validation(api):
    gid = seed()
    assert api.patch("/api/groups/999", json={"watched": True}).status_code == 404
    assert api.patch(f"/api/groups/{gid}", json={"kid_ids": [42]}).status_code == 422
    assert api.patch(f"/api/groups/{gid}", json={"watched": True, "x": 1}).status_code == 422


def test_cannot_watch_forward_destination(api):
    from kino.forward import set_default_destination

    gid = seed()
    noa = kid("Noa")
    with dbs.get_session() as s:
        set_default_destination(s, GID)
    assert api.get("/api/groups").json()[0]["is_forward_destination"] is True
    r = api.patch(f"/api/groups/{gid}", json={"watched": True, "kid_ids": [noa]})
    assert r.status_code == 409
    assert "forward destination" in r.json()["detail"]
    with dbs.get_session() as s:
        assert s.scalar(select(Group)).watched is False
        assert s.scalars(select(KidGroup)).all() == []  # whole change rolled back


def test_refresh_imports_from_openwa(api):
    seed("2@g.us")
    use_openwa(
        api,
        lambda req: httpx.Response(
            200,
            json=[
                {"id": GID, "name": "גן שמש"},
                {"id": "2@g.us", "name": "Renamed"},
                {"id": "x@c.us", "name": "a contact"},
            ],
        ),
    )
    r = api.post("/api/groups/refresh")
    assert r.status_code == 200, r.text
    assert r.json() == {"imported": 1, "total": 2}
    rows = api.get("/api/groups").json()
    names = {g["chat_id"]: (g["name"], g["slug"], g["watched"]) for g in rows}
    assert names == {GID: ("גן שמש", "gn-shmsh", False), "2@g.us": ("Renamed", "class", False)}


def test_refresh_without_openwa_is_503(api):
    r = api.post("/api/groups/refresh")
    assert r.status_code == 503 and "not configured" in r.json()["detail"]


def test_refresh_openwa_unreachable_is_502(api):
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=req)

    use_openwa(api, boom)
    r = api.post("/api/groups/refresh")
    assert r.status_code == 502
    assert r.json() == {"detail": "OpenWA is unreachable"}
    use_openwa(api, lambda req: httpx.Response(401, json={"error": "bad key secret-api-key"}))
    r = api.post("/api/groups/refresh")
    assert r.status_code == 502 and r.json() == {"detail": "OpenWA returned HTTP 401"}
    assert "secret-api-key" not in r.text
