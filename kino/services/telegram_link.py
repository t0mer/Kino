"""Linking Telegram chats: one-time codes the user sends to the bot (``/start <code>``).

Codes live in memory only (:class:`LinkStore`, one per app) with a short TTL. The update offset
of the single ``getUpdates`` consumer is persisted so confirmed updates are not read again.
"""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kino.db.models import TELEGRAM_CHAT_TYPES, Setting, TelegramChat

CODE_TTL = 600  # seconds a code stays valid
MAX_CODES = 20
CODE_LENGTH = 6
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
K_OFFSET = "telegram.update_offset"


@dataclass
class _Entry:
    expires: float
    chat_id: str | None = None  # set once linked


class LinkStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._lock: Any = None  # asyncio.Lock, created on first use inside the running loop

    @property
    def lock(self) -> Any:
        import asyncio

        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _purge(self) -> None:
        now = self._clock()
        for code in [c for c, e in self._entries.items() if e.expires + CODE_TTL < now]:
            del self._entries[code]

    def create(self) -> str:
        self._purge()
        live = sorted(
            (c for c, e in self._entries.items() if e.expires >= self._clock()),
            key=lambda c: self._entries[c].expires,
        )
        for code in live[: max(0, len(live) - MAX_CODES + 1)]:
            del self._entries[code]
        while True:
            code = "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))
            if code not in self._entries:
                break
        self._entries[code] = _Entry(expires=self._clock() + CODE_TTL)
        return code

    def state(self, code: str) -> str:
        """``unknown``, ``expired``, ``pending`` or ``linked``."""
        self._purge()
        entry = self._entries.get(code)
        if entry is None:
            return "unknown"
        if entry.chat_id is not None:
            return "linked"
        return "expired" if entry.expires < self._clock() else "pending"

    def chat_id(self, code: str) -> str | None:
        entry = self._entries.get(code)
        return entry.chat_id if entry else None

    def pending(self) -> list[str]:
        return [c for c in self._entries if self.state(c) == "pending"]

    def mark_linked(self, code: str, chat_id: str) -> None:
        self._entries[code].chat_id = chat_id


def get_offset(session: Session) -> int | None:
    row = session.get(Setting, K_OFFSET)
    return row.value if row is not None and isinstance(row.value, int) else None


def set_offset(session: Session, offset: int) -> None:
    row = session.get(Setting, K_OFFSET)
    if row is None:
        session.add(Setting(key=K_OFFSET, value=offset))
    else:
        row.value = offset
    session.flush()


def next_offset(updates: list[dict[str, Any]]) -> int | None:
    ids = [u["update_id"] for u in updates if isinstance(u.get("update_id"), int)]
    return max(ids) + 1 if ids else None


def _chat_title(chat: dict[str, Any]) -> str:
    if chat.get("type") == "private":
        name = " ".join(p for p in (chat.get("first_name"), chat.get("last_name")) if p)
        return str(name or chat.get("username") or chat.get("id"))
    return str(chat.get("title") or chat.get("username") or chat.get("id"))


def upsert_chat(session: Session, chat: dict[str, Any]) -> TelegramChat | None:
    """Create or refresh a linked chat from a Telegram ``chat`` object (None if unusable)."""
    if chat.get("id") is None or chat.get("type") not in TELEGRAM_CHAT_TYPES:
        return None
    chat_id = str(chat["id"])
    row = session.scalar(select(TelegramChat).where(TelegramChat.chat_id == chat_id))
    title = _chat_title(chat)[:200]
    if row is None:
        row = TelegramChat(chat_id=chat_id, title=title, type=chat["type"])
        session.add(row)
    else:
        row.title, row.type = title, chat["type"]
    session.flush()
    return row


def find_links(updates: list[dict[str, Any]], codes: list[str]) -> list[tuple[str, dict[str, Any]]]:
    """(code, chat) for every message that carries one of ``codes`` (``/start <code>``, a
    mention with the code, or the bare code); codes are matched case-insensitively."""
    out: list[tuple[str, dict[str, Any]]] = []
    for update in updates:
        msg = update.get("message") or update.get("channel_post")
        if not isinstance(msg, dict) or not isinstance(msg.get("chat"), dict):
            continue
        text = str(msg.get("text") or msg.get("caption") or "").upper()
        for code in codes:
            if re.search(rf"(?<![A-Z0-9]){code}(?![A-Z0-9])", text):
                out.append((code, msg["chat"]))
    return out
