import base64
import json

import httpx
import pytest

from kino.delivery import SentRef, WhatsAppChannel, get_channel
from kino.openwa.client import OpenWAClient, OpenWAError


def make(handler) -> WhatsAppChannel:
    client = OpenWAClient(
        "http://owa:2785", "key123", "sess", transport=httpx.MockTransport(handler)
    )
    return WhatsAppChannel(client)


async def test_send_photo_and_text_delegate():
    bodies = []

    def h(req: httpx.Request) -> httpx.Response:
        bodies.append((req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"messageId": "true_1@g.us_ABC"})

    ch = make(h)
    ref = await ch.send_photo("1@g.us", b"img", "image/jpeg", "cap")
    assert ref == SentRef("true_1@g.us_ABC")
    assert (await ch.send_text("1@g.us", "hi")).id == "true_1@g.us_ABC"
    assert bodies[0][0].endswith("/messages/send-image")
    assert base64.b64decode(bodies[0][1]["base64"]) == b"img"
    assert bodies[0][1]["caption"] == "cap"
    assert bodies[1][1] == {"chatId": "1@g.us", "text": "hi"}
    await ch.aclose()


async def test_openwa_error_propagates_unchanged():
    ch = make(lambda req: httpx.Response(500, json={"message": "boom"}))
    with pytest.raises(OpenWAError):
        await ch.send_text("1@g.us", "hi")
    with pytest.raises(NotImplementedError):
        await ch.delete("1@g.us", SentRef("x"))


def test_registry_unknown_name():
    with pytest.raises(ValueError):
        get_channel("carrier-pigeon")
