"""Delivery channel abstraction: where forwarded photos and digests are sent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SentRef:
    """Identifies a sent message on its channel (the channel's own message id)."""

    id: str


class DeliveryError(Exception):
    """A send failed. `permanent` errors must not be retried; `retry_after` is a delay hint (s)."""

    def __init__(
        self,
        message: str,
        *,
        permanent: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.retry_after = retry_after


class DeliveryChannel(Protocol):
    """A messaging backend. Channels must not log or raise credentials or full destinations."""

    async def send_photo(self, dest: str, data: bytes, mimetype: str, caption: str) -> SentRef: ...

    async def send_text(self, dest: str, text: str) -> SentRef: ...

    async def test_connection(self) -> str:
        """Verify the channel works; returns a short human-readable label."""
        ...

    async def delete(self, dest: str, ref: SentRef) -> None:
        """Delete a sent message. Channels that cannot raise NotImplementedError."""
        ...

    async def aclose(self) -> None: ...
