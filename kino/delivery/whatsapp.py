"""WhatsApp delivery through OpenWA.

OpenWA errors propagate unchanged so stored `last_error` text keeps its existing form.
"""

from __future__ import annotations

from typing import Any

from kino.delivery.base import SentRef


class WhatsAppChannel:
    def __init__(self, client: Any) -> None:
        self._client = client  # an OpenWAClient (or a compatible fake)

    async def send_photo(self, dest: str, data: bytes, mimetype: str, caption: str) -> SentRef:
        return SentRef(str(await self._client.send_image(dest, data, mimetype, caption)))

    async def send_text(self, dest: str, text: str) -> SentRef:
        return SentRef(str(await self._client.send_text(dest, text)))

    async def test_connection(self) -> str:
        await self._client.list_chats()
        return "WhatsApp (OpenWA)"

    async def delete(self, dest: str, ref: SentRef) -> None:
        raise NotImplementedError("deleting sent WhatsApp messages is not supported")

    async def aclose(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()
