"""/api/openwa: chat picker and webhook registration state (R5, R6). Never returns secrets."""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from kino.api.deps import openwa_client, openwa_out_client, protected_router
from kino.api.schemas import ChatOut, WebhookRegistered, WebhookRegisterIn, WebhookStatus
from kino.config import Settings
from kino.db.models import Group
from kino.db.session import get_session
from kino.openwa.client import OpenWAClient
from kino.openwa.register import EVENTS, register_webhook
from kino.services import settings_svc
from kino.services.openwa_cfg import load_openwa_config, promote_webhook_secret

router = protected_router(prefix="/api/openwa", tags=["openwa"])
Client = Annotated[OpenWAClient, Depends(openwa_client)]


def _watched_chat_ids() -> set[str]:
    with get_session() as s:
        return set(s.scalars(select(Group.chat_id).where(Group.watched.is_(True))))


@router.get("/chats", response_model=list[ChatOut])
async def list_chats(
    client: Annotated[OpenWAClient, Depends(openwa_out_client)],
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> list[ChatOut]:
    """Contacts and groups of the sending (outgoing) session, for the destination pickers;
    watched groups are not selectable."""
    chats = await client.list_chats(q.strip() if q else None)
    watched = await run_in_threadpool(_watched_chat_ids)
    out = []
    for c in chats:
        chat_id = str(c.get("id") or "")
        if not chat_id:
            continue
        is_group = bool(c.get("isGroup")) or chat_id.endswith("@g.us")
        name = c.get("name")
        out.append(
            ChatOut(
                id=chat_id,
                name=str(name) if name else None,
                is_group=is_group,
                watched=chat_id in watched,
                selectable=chat_id not in watched,
            )
        )
    return out


def _ours(hooks: list[dict[str, Any]], url: str | None) -> dict[str, Any] | None:
    """Kino's hook: the last registered URL, else any hook pointing at the webhook path."""
    for hook in hooks:
        hook_url = str(hook.get("url") or "")
        if (hook_url == url) if url else urlsplit(hook_url).path == settings_svc.WEBHOOK_PATH:
            return hook
    return None


def _stored_url() -> str | None:
    with get_session() as s:
        return settings_svc.stored_webhook_url(s)


@router.get("/webhook", response_model=WebhookStatus)
async def webhook_status(client: Client) -> WebhookStatus:
    hook = _ours(await client.list_webhooks(), await run_in_threadpool(_stored_url))
    if hook is None:
        return WebhookStatus(registered=False)
    active = hook.get("active")
    return WebhookStatus(
        registered=True,
        active=active if isinstance(active, bool) else None,
        id=str(hook.get("id")) if hook.get("id") is not None else None,
        url=str(hook.get("url") or ""),
        events=[str(e) for e in hook.get("events") or []],
    )


@router.post("/webhook/register", response_model=WebhookRegistered)
async def webhook_register(
    request: Request, client: Client, body: WebhookRegisterIn
) -> WebhookRegistered:
    """Create or repair Kino's webhook in OpenWA (same as `kino openwa register-webhook`)."""
    env: Settings = request.app.state.settings

    def default_url() -> str:
        with get_session() as s:
            return settings_svc.default_webhook_url(s, env)

    secret = (await run_in_threadpool(load_openwa_config, env)).registration_secret()
    previous = await run_in_threadpool(_stored_url)
    url = body.url if body.url else await run_in_threadpool(default_url)
    action, hook_id = await register_webhook(client, url, secret, previous_url=previous)

    def remember() -> None:
        with get_session() as s:
            settings_svc.remember_webhook_url(s, url)
            promote_webhook_secret(s, env, secret)

    await run_in_threadpool(remember)
    return WebhookRegistered(action=action, id=hook_id, url=url, events=list(EVENTS))
