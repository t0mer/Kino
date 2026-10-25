"""Telegram delivery through the Bot API (httpx, long-polling only; never setWebhook).

The bot token lives in the request URL, so every message raised or logged here is built from the
method name, HTTP status and Telegram's own `description`, passed through `_redact`. Errors are
raised outside `except` blocks so the token cannot leak through exception chaining.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Sequence
from typing import Any

import httpx
from PIL import Image

from kino.delivery.base import DeliveryError, SentRef

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
MAX_PHOTO_DIMENSION_SUM = 10000
MAX_PHOTO_ASPECT = 20.0
MAX_CAPTION = 1024
MAX_TEXT = 4096

_TOKEN_RE = re.compile(r"bot\d+:[\w-]+")

# (description fragment, human reason); matched case-insensitively on 400/403 responses.
_PERMANENT: tuple[tuple[str, str], ...] = (
    ("chat not found", "Telegram: chat not found - add the bot to the chat and link it again"),
    ("peer_id_invalid", "Telegram: chat not found - add the bot to the chat and link it again"),
    ("chat_id is empty", "Telegram: no chat id - link the chat again"),
    ("bot was blocked", "Telegram: bot was blocked or kicked"),
    ("bot was kicked", "Telegram: bot was blocked or kicked"),
    ("kicked from", "Telegram: bot was blocked or kicked"),
    ("not enough rights", "Telegram: bot has no rights to post in this chat"),
    ("have no rights", "Telegram: bot has no rights to post in this chat"),
    ("user is deactivated", "Telegram: user is deactivated"),
    ("group chat was upgraded", "Telegram: group was upgraded to a supergroup - link it again"),
)


def _redact(text: str, token: str | None = None) -> str:
    if token:
        text = text.replace(token, "bot***")
    return _TOKEN_RE.sub("bot***", text)


def _cap_utf16(text: str, limit: int) -> str:
    """Truncate to ``limit`` UTF-16 code units (how Telegram counts)."""
    units = 0
    for i, ch in enumerate(text):
        units += 2 if ord(ch) > 0xFFFF else 1
        if units > limit:
            return text[:i]
    return text


def _needs_document(data: bytes) -> bool:
    """True when the image violates sendPhoto limits (or cannot be decoded)."""
    if len(data) > MAX_PHOTO_BYTES:
        return True
    try:
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
    except Exception:  # noqa: BLE001 - any decode failure -> let Telegram take it as a file
        return True
    if w <= 0 or h <= 0:
        return True
    return w + h > MAX_PHOTO_DIMENSION_SUM or max(w / h, h / w) > MAX_PHOTO_ASPECT


class TelegramChannel:
    def __init__(
        self,
        token: str,
        api_url: str = "https://api.telegram.org",
        *,
        send_as_file: bool = False,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = token
        self._base = f"{api_url.rstrip('/')}/bot{token}"
        self._api_url = api_url.rstrip("/")
        self._send_as_file = send_as_file
        self._timeout = timeout
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)

    def __repr__(self) -> str:
        return f"TelegramChannel(api_url={self._api_url!r})"

    def _clean(self, text: str) -> str:
        return _redact(text, self._token)

    async def _call(
        self,
        method: str,
        data: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        timeout: float | None = None,
    ) -> Any:
        failure: str | None = None
        resp: httpx.Response | None = None
        try:
            resp = await self._http.post(
                f"{self._base}/{method}",
                data=data,
                files=files,
                timeout=timeout if timeout is not None else self._timeout,
            )
        except httpx.HTTPError as exc:
            failure = type(exc).__name__
        if resp is None:
            raise DeliveryError(f"Telegram unreachable: {failure}")

        body: Any = None
        try:
            body = resp.json()
        except ValueError:
            body = None
        payload: dict[str, Any] = body if isinstance(body, dict) else {}
        if resp.status_code == 200 and payload.get("ok") is True:
            return payload.get("result")
        raise self._error(method, resp.status_code, payload)

    def _error(self, method: str, status: int, payload: dict[str, Any]) -> DeliveryError:
        code = payload.get("error_code")
        status = code if isinstance(code, int) else status
        desc = self._clean(str(payload.get("description") or ""))
        params = payload.get("parameters")
        params = params if isinstance(params, dict) else {}
        head = f"Telegram {method} failed ({status})"
        text = f"{head}: {desc}" if desc else head

        if status == 429:
            try:
                retry = float(params.get("retry_after") or 0) or None
            except (TypeError, ValueError):
                retry = None
            return DeliveryError(text, retry_after=retry)
        if status in (401, 404):
            return DeliveryError("Telegram: invalid bot token", permanent=True)
        if status in (400, 403):
            low = desc.lower()
            if "migrate_to_chat_id" in params and not any(p in low for p, _ in _PERMANENT):
                return DeliveryError(_PERMANENT[-1][1], permanent=True)
            for fragment, reason in _PERMANENT:
                if fragment in low:
                    return DeliveryError(reason, permanent=True)
            if "migrate_to_chat_id" in params:
                return DeliveryError(_PERMANENT[-1][1], permanent=True)
        return DeliveryError(text)

    async def send_photo(self, dest: str, data: bytes, mimetype: str, caption: str) -> SentRef:
        if len(data) > MAX_DOCUMENT_BYTES:
            raise DeliveryError("Telegram: image exceeds the 50 MB upload limit", permanent=True)
        as_doc = self._send_as_file or _needs_document(data)
        method, field = ("sendDocument", "document") if as_doc else ("sendPhoto", "photo")
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(mimetype, "bin")
        result = await self._call(
            method,
            data={"chat_id": dest, "caption": _cap_utf16(caption, MAX_CAPTION)},
            files={field: (f"photo.{ext}", data, mimetype or "application/octet-stream")},
        )
        return self._ref(result)

    async def send_text(self, dest: str, text: str) -> SentRef:
        result = await self._call(
            "sendMessage", data={"chat_id": dest, "text": _cap_utf16(text, MAX_TEXT)}
        )
        return self._ref(result)

    @staticmethod
    def _ref(result: Any) -> SentRef:
        mid = result.get("message_id") if isinstance(result, dict) else None
        if mid is None:
            raise DeliveryError("Telegram: response had no message_id")
        return SentRef(str(mid))

    async def test_connection(self) -> str:
        me = await self._call("getMe")
        username = me.get("username") if isinstance(me, dict) else None
        return f"@{username}" if username else "Telegram bot"

    async def get_updates(
        self,
        offset: int | None,
        timeout: int = 0,
        allowed_updates: Sequence[str] = ("message", "channel_post", "my_chat_member"),
    ) -> list[dict[str, Any]]:
        data: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": json.dumps(list(allowed_updates)),
        }
        if offset is not None:
            data["offset"] = offset
        result = await self._call("getUpdates", data=data, timeout=self._timeout + timeout)
        return list(result) if isinstance(result, list) else []

    async def get_chat(self, chat_id: str | int) -> dict[str, Any]:
        result = await self._call("getChat", data={"chat_id": chat_id})
        return result if isinstance(result, dict) else {}

    async def delete(self, dest: str, ref: SentRef) -> None:
        await self._call("deleteMessage", data={"chat_id": dest, "message_id": ref.id})

    async def aclose(self) -> None:
        await self._http.aclose()
