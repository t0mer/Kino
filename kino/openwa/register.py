"""Register Kino's webhook in OpenWA (idempotent: an existing hook with the same URL is updated)."""

from __future__ import annotations

from typing import Any

from kino.openwa.client import OpenWAClient

EVENTS = ["message.received"]
MIN_SECRET_LEN = 16  # documented minimum for the webhook signing secret

# Filter grammar confirmed against the live OpenWA 0.24.0 OpenAPI spec (WebhookFiltersDto):
# message-family fields include `type` and `isGroup`, operator `is`, and all conditions must
# match (AND). `type` takes a list value (a bare string is a 400 live).
# `fromMe` is also filterable, but it is
# deliberately NOT used: the user's own photos in a watched group must be processed too;
# Kino's own forwards are dropped by the webhook echo guard instead.
# Kino also drops non-image / non-group messages itself, so a wrong filter can only cause
# missed events (visible as no traffic), never wrong processing.
FILTERS: dict[str, Any] = {
    "conditions": [
        {"field": "type", "operator": "is", "value": ["image"]},
        {"field": "isGroup", "operator": "is", "value": True},
    ]
}


class RegisterError(RuntimeError):
    """Registration cannot proceed (bad secret, ...)."""


async def register_webhook(
    client: OpenWAClient, url: str, secret: str, previous_url: str | None = None
) -> tuple[str, str]:
    """Create or update the webhook for `url`; returns (action, webhook id).

    When `previous_url` (the URL registered last time) is given and its hook still exists,
    that hook is moved to `url` in place, so the old endpoint does not stay registered.
    PUT accepts `url` per the OpenWA notes in kino/openwa/client.py (not yet verified live).
    """
    if len(secret) < MIN_SECRET_LEN:
        raise RegisterError(f"KINO_WEBHOOK_SECRET must be at least {MIN_SECRET_LEN} characters")
    body: dict[str, Any] = {"events": EVENTS, "secret": secret, "filters": FILTERS}
    hooks = await client.list_webhooks()
    for hook in hooks:
        if hook.get("url") == url:
            hook_id = str(hook["id"])
            await client.update_webhook(hook_id, {**body, "active": True})
            return "updated", hook_id
    if previous_url and previous_url != url:
        for hook in hooks:
            if hook.get("url") == previous_url:
                hook_id = str(hook["id"])
                await client.update_webhook(hook_id, {**body, "url": url, "active": True})
                return "updated", hook_id
    return "created", await client.create_webhook({"url": url, **body})
