import io
from typing import Any

import httpx
import pytest
from PIL import Image

from kino.delivery.base import DeliveryError, SentRef
from kino.delivery.registry import get_channel
from kino.delivery.telegram import TelegramChannel

TOKEN = "123456:SECRET-token_abc"


def _png(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("L", (w, h)).save(buf, "PNG")
    return buf.getvalue()


def _make(handler, **kw) -> TelegramChannel:
    return TelegramChannel(TOKEN, transport=httpx.MockTransport(handler), **kw)


class Rec:
    def __init__(self, body: Any = None, status: int = 200):
        self.body = body if body is not None else {"ok": True, "result": {"message_id": 7}}
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)

    @property
    def method(self) -> str:
        return self.requests[-1].url.path.rsplit("/", 1)[1]

    @property
    def text(self) -> str:
        return self.requests[-1].content.decode("latin-1")


def _err(status: int, desc: str, **params: Any) -> Rec:
    body: dict[str, Any] = {"ok": False, "error_code": status, "description": desc}
    if params:
        body["parameters"] = params
    return Rec(body, status)


async def test_send_photo_ok():
    rec = Rec()
    ch = _make(rec)
    ref = await ch.send_photo("-100", _png(10, 10), "image/png", "hello")
    assert ref == SentRef("7")
    assert rec.method == "sendPhoto"
    assert 'name="caption"' in rec.text and "hello" in rec.text
    assert "parse_mode" not in rec.text
    assert 'name="photo"' in rec.text


@pytest.mark.parametrize(
    ("data", "kw"),
    [
        (_png(10001, 1), {}),
        (_png(2000, 20), {}),
        (_png(10, 10), {"send_as_file": True}),
        (_png(10, 10) + b"\0" * (10 * 1024 * 1024), {}),
        (b"not an image", {}),
    ],
)
async def test_document_fallback(data, kw):
    rec = Rec()
    ch = _make(rec, **kw)
    await ch.send_photo("1", data, "image/png", "c")
    assert rec.method == "sendDocument"
    assert 'name="document"' in rec.text


async def test_over_50mb_permanent():
    ch = _make(Rec())
    with pytest.raises(DeliveryError) as ei:
        await ch.send_photo("1", b"\0" * (50 * 1024 * 1024 + 1), "image/png", "")
    assert ei.value.permanent


async def test_caption_truncated_utf16():
    rec = Rec()
    ch = _make(rec)
    await ch.send_photo("1", _png(5, 5), "image/png", "a" * 1023 + "😀")
    assert "a" * 1023 in rec.text and "😀" not in rec.text
    await ch.send_photo("1", _png(5, 5), "image/png", "b" * 2000)
    assert "b" * 1024 in rec.text and "b" * 1025 not in rec.text


async def test_send_text():
    rec = Rec()
    ch = _make(rec)
    assert await ch.send_text("1", "x" * 5000) == SentRef("7")
    assert rec.method == "sendMessage"
    assert "x" * 4096 in rec.text and "x" * 4097 not in rec.text
    assert "parse_mode" not in rec.text


async def test_get_me():
    ch = _make(Rec({"ok": True, "result": {"username": "kino_bot"}}))
    assert await ch.test_connection() == "@kino_bot"


async def test_delete_and_get_chat():
    rec = Rec({"ok": True, "result": True})
    ch = _make(rec)
    await ch.delete("-5", SentRef("42"))
    assert rec.method == "deleteMessage" and "42" in rec.text and "-5" in rec.text
    rec.body = {"ok": True, "result": {"id": -5, "title": "T"}}
    assert (await ch.get_chat("-5"))["title"] == "T"
    assert rec.method == "getChat"


async def test_get_updates_params():
    rec = Rec({"ok": True, "result": [{"update_id": 1}]})
    ch = _make(rec)
    assert await ch.get_updates(5, timeout=10) == [{"update_id": 1}]
    assert rec.method == "getUpdates"
    assert "offset=5" in rec.text and "timeout=10" in rec.text
    assert "my_chat_member" in rec.text or "my_chat_member" in rec.requests[-1].content.decode()
    await ch.get_updates(None)
    assert "offset" not in rec.text


def _cases() -> list[tuple[Rec, bool, float | None]]:
    return [
        (_err(429, "Too Many Requests: retry after 9", retry_after=9), False, 9.0),
        (_err(403, "Forbidden: bot was blocked by the user"), True, None),
        (_err(403, "Forbidden: bot was kicked from the group chat"), True, None),
        (_err(400, "Bad Request: chat not found"), True, None),
        (_err(400, "Bad Request: have no rights to send a message"), True, None),
        (_err(400, "Bad Request: group chat was upgraded", migrate_to_chat_id=-9), True, None),
        (_err(401, "Unauthorized"), True, None),
        (_err(500, "Internal"), False, None),
        (_err(502, f"bad gateway bot{TOKEN}"), False, None),
    ]


@pytest.mark.parametrize("idx", range(9))
async def test_error_mapping_and_no_token_leak(idx):
    rec, permanent, retry = _cases()[idx]
    ch = _make(rec)
    with pytest.raises(DeliveryError) as ei:
        await ch.send_text("1", "x")
    e = ei.value
    assert e.permanent is permanent
    assert e.retry_after == retry
    for text in (str(e), repr(e), repr(ch), str(e.__cause__), str(e.__context__)):
        assert TOKEN not in text and "SECRET" not in text


async def test_reasons():
    ch = _make(_err(403, "Forbidden: bot was blocked by the user"))
    with pytest.raises(DeliveryError, match="blocked or kicked"):
        await ch.send_text("1", "x")
    ch = _make(_err(400, "Bad Request: chat not found"))
    with pytest.raises(DeliveryError, match="chat not found"):
        await ch.send_text("1", "x")


async def test_connect_error_no_url():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect {request.url}")

    ch = _make(boom)
    with pytest.raises(DeliveryError) as ei:
        await ch.send_text("1", "x")
    e = ei.value
    assert str(e) == "Telegram unreachable: ConnectError"
    assert not e.permanent
    for text in (str(e), repr(e), str(e.__cause__), str(e.__context__)):
        assert TOKEN not in text and "api.telegram.org" not in text


async def test_non_json_body():
    ch = _make(lambda r: httpx.Response(502, text=f"oops {TOKEN}"))
    with pytest.raises(DeliveryError) as ei:
        await ch.send_text("1", "x")
    assert not ei.value.permanent and TOKEN not in str(ei.value)


def test_registry_telegram():
    class Cfg:
        bot_token = TOKEN
        api_url = "https://example.test"
        send_as_file = True

    assert isinstance(get_channel("telegram", telegram_config=Cfg()), TelegramChannel)

    class Empty(Cfg):
        bot_token = ""

    with pytest.raises(DeliveryError) as ei:
        get_channel("telegram", telegram_config=Empty())
    assert ei.value.permanent
