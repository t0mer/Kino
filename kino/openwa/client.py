"""Async OpenWA REST client. Never logs the API key, phone numbers or media bytes."""

from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import quote

import httpx

from kino.openwa.schemas import API_KEY_HEADER


class OpenWAError(Exception):
    """OpenWA call failed. Carries only a status code and a masked path (no URL, key or body)."""

    def __init__(self, what: str, status: int | None, path: str) -> None:
        self.status = status
        self.path = path
        super().__init__(f"OpenWA {what} (status={status}, path={path})")


_ID_RE = re.compile(r"(\d{4})\d+(\d{3})(?=@)")


def mask_path(path: str) -> str:
    """Mask the digits of chat/phone ids in a URL path, e.g. 9725****123@c.us."""
    return _ID_RE.sub(lambda m: f"{m.group(1)}****{m.group(2)}", path)


class OpenWAClient:
    def __init__(
        self,
        url: str,
        api_key: str,
        session_id: str,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._session = quote(session_id, safe="")
        self._http = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            headers={API_KEY_HEADER: api_key},
            timeout=timeout,
            transport=transport,
        )

    def _p(self, suffix: str) -> str:
        return f"/api/sessions/{self._session}/{suffix}"

    async def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            r = await self._http.request(method, path, **kw)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise OpenWAError("http error", e.response.status_code, mask_path(path)) from None
        except httpx.HTTPError as e:
            raise OpenWAError(type(e).__name__, None, mask_path(path)) from None
        return r

    async def _json(self, method: str, path: str, **kw: Any) -> Any:
        r = await self._request(method, path, **kw)
        try:
            return r.json()
        except ValueError:
            raise OpenWAError("invalid json", r.status_code, mask_path(path)) from None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def list_groups(self) -> list[dict[str, Any]]:
        return list(await self._json("GET", self._p("groups")))

    async def get_group_name(self, chat_id: str) -> str | None:
        for g in await self.list_groups():
            if g.get("id") == chat_id:
                return g.get("name")
        return None

    async def list_chats(self, q: str | None = None) -> list[dict[str, Any]]:
        chats = list(await self._json("GET", self._p("chats")))
        if q:
            needle = q.casefold()
            chats = [c for c in chats if needle in str(c.get("name") or "").casefold()]
        return chats

    async def get_media(self, chat_id: str, message_id: str) -> bytes:
        path = self._p(f"messages/{quote(chat_id, safe='@')}/{quote(message_id, safe='')}/media")
        return (await self._request("GET", path)).content

    def _message_id(self, res: Any, what: str) -> str:
        try:
            return str(res["messageId"])
        except (KeyError, TypeError):
            raise OpenWAError(f"{what}: no messageId in response", None, what) from None

    async def send_text(self, chat_id: str, text: str) -> str:
        res = await self._json(
            "POST", self._p("messages/send-text"), json={"chatId": chat_id, "text": text}
        )
        return self._message_id(res, "send-text")

    async def send_image(self, chat_id: str, data: bytes, mimetype: str, caption: str) -> str:
        res = await self._json(
            "POST",
            self._p("messages/send-image"),
            json={
                "chatId": chat_id,
                "base64": base64.b64encode(data).decode("ascii"),
                "mimetype": mimetype,
                "caption": caption,
            },
        )
        return self._message_id(res, "send-image")
