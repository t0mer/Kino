"""Async OpenWA REST client. Never logs the API key, phone numbers or media bytes."""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime
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


HISTORY_LIMIT = 2000  # deep window (spec: 2000 ceiling with deep=true)
PAGE_SIZE = 1000  # list endpoints are paged by limit/offset

_ID_RE = re.compile(r"(\d{4})\d{2,}(\d{3})")


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
        failure: OpenWAError | None = None
        try:
            r = await self._http.request(method, path, **kw)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            failure = OpenWAError("http error", e.response.status_code, mask_path(path))
        except httpx.HTTPError as e:
            failure = OpenWAError(type(e).__name__, None, mask_path(path))
        if failure is not None:
            raise failure  # raised outside except: __context__ stays None
        return r

    async def _json(self, method: str, path: str, **kw: Any) -> Any:
        r = await self._request(method, path, **kw)
        try:
            return r.json()
        except ValueError:
            failure = OpenWAError("invalid json", r.status_code, mask_path(path))
        raise failure

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _paged(self, what: str) -> list[dict[str, Any]]:
        """GET a list endpoint page by page (`limit`/`offset`, per the live OpenAPI spec)."""
        out: list[dict[str, Any]] = []
        while True:
            page = list(
                await self._json(
                    "GET", self._p(what), params={"limit": PAGE_SIZE, "offset": len(out)}
                )
            )
            out.extend(page)
            if len(page) < PAGE_SIZE:
                return out

    async def list_groups(self) -> list[dict[str, Any]]:
        return await self._paged("groups")

    async def get_group_name(self, chat_id: str) -> str | None:
        for g in await self.list_groups():
            if g.get("id") == chat_id:
                return g.get("name")
        return None

    async def list_chats(self, q: str | None = None) -> list[dict[str, Any]]:
        chats = await self._paged("chats")
        if q:
            needle = q.casefold()
            chats = [c for c in chats if needle in str(c.get("name") or "").casefold()]
        return chats

    async def get_media(self, chat_id: str, message_id: str) -> bytes:
        path = self._p(f"messages/{quote(chat_id, safe='@')}/{quote(message_id, safe='')}/media")
        return (await self._request("GET", path)).content

    async def list_chat_messages(
        self,
        chat_id: str,
        *,
        since: datetime | None = None,
        limit: int = HISTORY_LIMIT,
        image_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Recent messages of a chat, as tolerant dicts (newest or oldest first: not relied on).

        Live endpoint (OpenWA 0.24.0 spec): ``GET /sessions/{sid}/messages/{chatId}/history``
        with ``limit`` (100 max, 2000 with ``deep=true``), ``includeMedia`` (default false, so
        media is fetched per message with :meth:`get_media`) and no cursor: the whole window
        arrives in one response. ``since`` (naive UTC) and ``image_only`` filter client-side.
        """
        path = self._p(f"messages/{quote(chat_id, safe='@')}/history")
        params: dict[str, Any] = {"limit": limit, "includeMedia": "false"}
        if limit > 100:
            params["deep"] = "true"
        res = await self._json("GET", path, params=params)
        if isinstance(res, dict):
            res = res.get("messages") or res.get("data") or []
        if not isinstance(res, list):
            failure = OpenWAError("unexpected history response", None, mask_path(path))
            raise failure
        floor = since.replace(tzinfo=UTC).timestamp() if since is not None else None
        out: list[dict[str, Any]] = []
        for m in res:
            if not isinstance(m, dict):
                continue
            if image_only and m.get("type") != "image":
                continue
            ts = m.get("timestamp")
            if floor is not None and (not isinstance(ts, int | float) or ts < floor):
                continue
            out.append(m)
        return out

    def _message_id(self, res: Any, what: str) -> str:
        try:
            return str(res["messageId"])
        except (KeyError, TypeError):
            failure = OpenWAError("no messageId in response", None, self._p(f"messages/{what}"))
        raise failure

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

    # --- webhook management -------------------------------------------------------------
    # Source: https://github.com/rmyndharis/openwa/blob/main/docs/06-api-specification.md
    # section 6.4.8 (read 2026-10): GET/POST /api/sessions/:sessionId/webhooks,
    # PUT /api/sessions/:sessionId/webhooks/:id (OPERATOR role). Body: url, events[], secret
    # (>= 16 chars, write-only), filters {conditions:[{field, operator, value}]}, active (update).

    async def list_webhooks(self) -> list[dict[str, Any]]:
        return list(await self._json("GET", self._p("webhooks")))

    async def create_webhook(self, body: dict[str, Any]) -> str:
        res = await self._json("POST", self._p("webhooks"), json=body)
        return self._webhook_id(res, "webhooks")

    async def update_webhook(self, webhook_id: str, body: dict[str, Any]) -> str:
        res = await self._json("PUT", self._p(f"webhooks/{quote(webhook_id, safe='')}"), json=body)
        return self._webhook_id(res, "webhooks/{id}")

    def _webhook_id(self, res: Any, what: str) -> str:
        try:
            return str(res["id"])
        except (KeyError, TypeError):
            failure = OpenWAError("no id in response", None, self._p(what))
        raise failure
