import base64
import json

import httpx
import pytest

from kino.openwa.client import OpenWAClient, OpenWAError


def make(handler) -> OpenWAClient:
    return OpenWAClient("http://owa:2785", "key123", "sess", transport=httpx.MockTransport(handler))


async def test_list_groups_and_name():
    seen = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["key"] = req.headers["x-api-key"]
        return httpx.Response(200, json=[{"id": "1@g.us", "name": "Class"}])

    c = make(h)
    groups = await c.list_groups()
    assert seen == {"path": "/api/sessions/sess/groups", "key": "key123"}
    assert groups[0]["name"] == "Class"
    assert await c.get_group_name("1@g.us") == "Class"
    assert await c.get_group_name("9@g.us") is None


async def test_get_media():
    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/sessions/sess/messages/1@g.us/MID/media"
        return httpx.Response(200, content=b"\xff\xd8bytes")

    assert await make(h).get_media("1@g.us", "MID") == b"\xff\xd8bytes"


async def test_send_text_and_image():
    bodies = []

    def h(req: httpx.Request) -> httpx.Response:
        bodies.append((req.url.path, json.loads(req.content)))
        return httpx.Response(201, json={"messageId": "true_x_ID", "timestamp": 1})

    c = make(h)
    assert await c.send_text("c@c.us", "hi") == "true_x_ID"
    assert await c.send_image("c@c.us", b"abc", "image/jpeg", "cap") == "true_x_ID"
    assert bodies[0] == (
        "/api/sessions/sess/messages/send-text",
        {"chatId": "c@c.us", "text": "hi"},
    )
    p, b = bodies[1]
    assert p == "/api/sessions/sess/messages/send-image"
    assert b["base64"] == base64.b64encode(b"abc").decode()
    assert b["mimetype"] == "image/jpeg" and b["caption"] == "cap"


async def test_list_chats_filters():
    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"id": "a@c.us", "name": "Alice", "isGroup": False},
                {"id": "b@g.us", "name": "Bob Team", "isGroup": True},
            ],
        )

    assert [x["id"] for x in await make(h).list_chats("bob")] == ["b@g.us"]
    assert len(await make(h).list_chats()) == 2


async def test_get_media_error_is_sanitised():
    c = make(lambda r: httpx.Response(404, json={"message": "nope 972501234567"}))
    with pytest.raises(OpenWAError) as e:
        await c.get_media("972501234567@c.us", "MID")
    for text in (str(e.value), repr(e.value)):
        assert "972501234567" not in text and "key123" not in text
    assert e.value.status == 404 and "9725****567@c.us" in str(e.value)


async def test_transport_error_and_bad_json_wrapped():
    def boom(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x 972501234567@c.us", request=r)

    with pytest.raises(OpenWAError) as e:
        await make(boom).get_media("972501234567@c.us", "M")
    assert "972501234567" not in str(e.value) and e.value.status is None
    with pytest.raises(OpenWAError):
        await make(lambda r: httpx.Response(200, content=b"<html>")).list_groups()
    with pytest.raises(OpenWAError):
        await make(lambda r: httpx.Response(201, json={})).send_text("c@c.us", "x")
