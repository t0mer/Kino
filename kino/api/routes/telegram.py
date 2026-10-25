"""/api/settings/telegram and /api/telegram: bot token, connection test, chat linking.

The bot token is write-only: responses carry ``has_token`` and the cached bot username, never
the token. Errors are redacted with :func:`redact_token` before they reach a response or a log.
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from kino.api.deps import protected_router
from kino.api.routes.openwa_setup import display_url
from kino.api.schemas import (
    TelegramChatOut,
    TelegramLinkOut,
    TelegramLinkStatus,
    TelegramSettingsIn,
    TelegramSettingsOut,
    TelegramTestOut,
)
from kino.config import Settings
from kino.db.models import TelegramChat
from kino.db.session import get_session
from kino.delivery.base import DeliveryError
from kino.delivery.telegram import TelegramChannel
from kino.forward import get_forward_config, put_setting
from kino.services import ConflictError, InvalidError, NotFoundError, settings_svc
from kino.services import telegram_cfg as tcfg
from kino.services import telegram_link as link
from kino.services.telegram_cfg import TelegramConfig, redact_token

router = protected_router(tags=["telegram"])

POLL_TIMEOUT = 8  # seconds of Telegram long-polling per link-status request (<= 10)


def _env(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def make_channel(
    app: FastAPI, cfg: TelegramConfig, *, send_as_file: bool = False
) -> TelegramChannel:
    """A channel for ``cfg``; tests inject ``app.state.telegram_factory(token, api_url, file)``."""
    if not cfg.bot_token:
        raise InvalidError("The Telegram bot token is not configured")
    factory = getattr(app.state, "telegram_factory", None)
    if factory is not None:
        channel: TelegramChannel = factory(cfg.bot_token, cfg.api_url, send_as_file)
        return channel
    return TelegramChannel(cfg.bot_token, cfg.api_url, send_as_file=send_as_file)


def delivery_http(e: DeliveryError, token: str | None) -> HTTPException:
    """HTTP error for a failed Telegram call: 422 when retrying cannot help, else 502."""
    detail = redact_token(str(e), token)
    return HTTPException(422 if e.permanent else 502, detail)


def raise_delivery(e: DeliveryError, token: str | None) -> NoReturn:
    raise delivery_http(e, token)


def telegram_summary(session: Session, env: Settings) -> TelegramSettingsOut:
    cfg = tcfg.get_telegram_config(session, env)
    return TelegramSettingsOut(
        has_token=cfg.has_token,
        bot_username=cfg.bot_username,
        api_url=display_url(cfg.api_url) or "",
        rate_per_min=cfg.rate_per_min,
        send_as_file=cfg.send_as_file,
        delete_on_remove=cfg.delete_on_remove,
        env_overrides=tcfg.env_overrides(env),
    )


def _username(label: str) -> str | None:
    name = label.lstrip("@")
    return None if label == "Telegram bot" or not name else name


def _chat_out(row: TelegramChat) -> TelegramChatOut:
    return TelegramChatOut(
        id=row.id, chat_id=row.chat_id, title=row.title, type=row.type, linked_at=row.linked_at
    )


def _config(env: Settings) -> TelegramConfig:
    with get_session() as s:
        return tcfg.get_telegram_config(s, env)


async def _get_me(app: FastAPI, cfg: TelegramConfig) -> str | None:
    """Run getMe; returns the bot username (without @). Raises HTTPException on failure."""
    channel = make_channel(app, cfg)
    failure: DeliveryError | None = None
    label = ""
    try:
        label = await channel.test_connection()
    except DeliveryError as e:
        failure = e
    finally:
        await channel.aclose()
    if failure is not None:
        raise_delivery(failure, cfg.bot_token)
    return _username(label)


def _save_username(username: str | None) -> None:
    if username is None:
        return
    with get_session() as s:
        put_setting(s, tcfg.K_BOT_USERNAME, username)


@router.get("/api/settings/telegram", response_model=TelegramSettingsOut)
def get_telegram(request: Request) -> TelegramSettingsOut:
    with get_session() as s:
        return telegram_summary(s, _env(request))


@router.put("/api/settings/telegram", response_model=TelegramSettingsOut)
async def put_telegram(request: Request, body: TelegramSettingsIn) -> TelegramSettingsOut:
    env = _env(request)
    fields = body.model_fields_set
    token = (body.bot_token or "").strip()
    username: str | None = None
    if "bot_token" in fields:
        tcfg.ensure_unlocked(env)
        if token:
            tcfg.check_token_format(token)
            current = await run_in_threadpool(_config, env)
            probe = TelegramConfig(bot_token=token, api_url=current.api_url, rate_per_min=1)
            username = await _get_me(request.app, probe)  # nothing is saved when this fails

    def save() -> TelegramSettingsOut:
        with get_session() as s:
            if "bot_token" in fields:
                tcfg.save_telegram_token(s, env, token or None, username)
                logger.info("Telegram bot token {}", "saved" if token else "cleared")
            current = tcfg.get_telegram_config(s, env)
            if body.send_as_file is not None or body.delete_on_remove is not None:
                tcfg.save_telegram_options(
                    s,
                    env,
                    current.send_as_file if body.send_as_file is None else body.send_as_file,
                    current.delete_on_remove
                    if body.delete_on_remove is None
                    else body.delete_on_remove,
                )
            return telegram_summary(s, env)

    return await run_in_threadpool(save)


@router.post("/api/telegram/test", response_model=TelegramTestOut)
async def test_telegram(request: Request) -> TelegramTestOut:
    """getMe with the effective configuration (environment or saved)."""
    env = _env(request)
    cfg = await run_in_threadpool(_config, env)
    if not cfg.has_token:
        raise InvalidError("The Telegram bot token is not configured")
    username = await _get_me(request.app, cfg)
    if username != cfg.bot_username:
        await run_in_threadpool(_save_username, username)
    return TelegramTestOut(ok=True, bot_username=username)


# --- linking chats ------------------------------------------------------------------------


def _store(app: FastAPI) -> link.LinkStore:
    store: link.LinkStore | None = getattr(app.state, "telegram_links", None)
    if store is None:
        store = app.state.telegram_links = link.LinkStore()
    return store


@router.post("/api/telegram/link", response_model=TelegramLinkOut)
async def start_link(request: Request) -> TelegramLinkOut:
    env = _env(request)
    cfg = await run_in_threadpool(_config, env)
    if not cfg.has_token:
        raise InvalidError("Configure the Telegram bot token before linking a chat")
    username = cfg.bot_username  # cache; may be stale (token from env / changed)
    try:
        fresh = await _get_me(request.app, cfg)
    except HTTPException:
        pass  # keep the cached value; the poll will surface the real problem
    else:
        if fresh != username:
            await run_in_threadpool(_save_username, fresh)
        username = fresh
    code = _store(request.app).create()
    bot = f"@{username}" if username else "your bot"
    return TelegramLinkOut(
        code=code,
        bot_username=username,
        expires_in=link.CODE_TTL,
        instructions=(
            f"Send /start {code} to {bot} in a private chat. For a group or channel, add "
            f"{bot} first, then send a message containing {code} (mention the bot or use "
            f"/start@{username or 'bot'} {code} if privacy mode hides plain messages)."
        ),
    )


def _apply_updates(updates: list[dict[str, object]], codes: list[str]) -> list[tuple[str, str]]:
    """Persist the offset and upsert chats; returns (code, chat_id) for every link made."""
    made: list[tuple[str, str]] = []
    with get_session() as s:
        for code, chat in link.find_links(updates, codes):
            row = link.upsert_chat(s, chat)
            if row is not None:
                made.append((code, row.chat_id))
        if (off := link.next_offset(updates)) is not None:
            link.set_offset(s, off)
    return made


def _load_chat(chat_id: str) -> TelegramChatOut | None:
    with get_session() as s:
        row = s.scalar(select(TelegramChat).where(TelegramChat.chat_id == chat_id))
        return _chat_out(row) if row is not None else None


async def _status(store: link.LinkStore, code: str) -> TelegramLinkStatus | None:
    state = store.state(code)
    if state == "linked":
        chat_id = store.chat_id(code)
        chat = await run_in_threadpool(_load_chat, chat_id) if chat_id else None
        if chat is not None:
            return TelegramLinkStatus(status="linked", chat=chat)
        return TelegramLinkStatus(status="expired")  # the chat was unlinked meanwhile
    if state == "expired":
        return TelegramLinkStatus(status="expired")
    return None


@router.get("/api/telegram/link/{code}", response_model=TelegramLinkStatus)
async def poll_link(request: Request, code: str) -> TelegramLinkStatus:
    """One short getUpdates round (<= POLL_TIMEOUT s) looking for the code in new messages."""
    env = _env(request)
    store = _store(request.app)
    code = code.upper()
    if store.state(code) == "unknown":
        raise NotFoundError("Unknown or expired link code")
    if (done := await _status(store, code)) is not None:
        return done
    async with store.lock:
        if (done := await _status(store, code)) is not None:  # linked by a concurrent poll
            return done
        cfg = await run_in_threadpool(_config, env)
        if not cfg.has_token:
            raise InvalidError("The Telegram bot token is not configured")
        offset = await run_in_threadpool(_offset)
        channel = make_channel(request.app, cfg)
        failure: DeliveryError | None = None
        updates: list[dict[str, object]] = []
        try:
            updates = await channel.get_updates(
                offset,
                timeout=POLL_TIMEOUT,
                allowed_updates=("message", "channel_post", "my_chat_member"),
            )
        except DeliveryError as e:
            failure = e
        finally:
            await channel.aclose()
        if failure is not None:
            if "(409)" in str(failure):
                raise ConflictError(
                    "Telegram refuses getUpdates because another consumer or a webhook is "
                    "attached to this bot. Remove the webhook (or stop the other poller) "
                    "and try again."
                )
            raise_delivery(failure, cfg.bot_token)
        made = await run_in_threadpool(_apply_updates, updates, store.pending())
        for made_code, chat_id in made:
            store.mark_linked(made_code, chat_id)
    return await _status(store, code) or TelegramLinkStatus(status="pending")


def _offset() -> int | None:
    with get_session() as s:
        return link.get_offset(s)


@router.get("/api/telegram/chats", response_model=list[TelegramChatOut])
def list_chats() -> list[TelegramChatOut]:
    with get_session() as s:
        return [_chat_out(r) for r in s.scalars(select(TelegramChat).order_by(TelegramChat.id))]


@router.delete("/api/telegram/chats/{chat_pk}", status_code=204)
def delete_chat(request: Request, chat_pk: int) -> Response:
    env = _env(request)
    with get_session() as s:
        row = s.get(TelegramChat, chat_pk)
        if row is None:
            raise NotFoundError(f"Telegram chat {chat_pk} does not exist")
        fwd = get_forward_config(s)
        digest = settings_svc.get_digest(s, env)
        if ("telegram", row.chat_id) in fwd.destinations():
            raise ConflictError(
                "This chat is a forwarding destination; change the destination first"
            )
        if digest.channel == "telegram" and digest.chat_id == row.chat_id:
            raise ConflictError("This chat is the digest destination; change the destination first")
        s.delete(row)
    return Response(status_code=204)
