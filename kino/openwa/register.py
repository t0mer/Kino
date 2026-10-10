"""Register Kino's webhook in OpenWA (idempotent: an existing hook with the same URL is updated)."""

from __future__ import annotations

from typing import Any

from kino.openwa.client import OpenWAClient

EVENTS = ["message.received"]
MIN_SECRET_LEN = 16  # documented minimum for the webhook signing secret

# Filter grammar confirmed against the live OpenWA 0.24.0 OpenAPI spec (WebhookFiltersDto):
# message-family fields include `type` and `isGroup`, operator `is`, and all conditions must
# match (AND). Not yet exercised by a real delivery. `fromMe` is also filterable, but it is
# deliberately NOT used: the user's own photos in a watched group must be processed too;
# Kino's own forwards are dropped by the webhook echo guard instead.
# Kino also drops non-image / non-group messages itself, so a wrong filter can only cause
# missed events (visible as no traffic), never wrong processing.
FILTERS: dict[str, Any] = {
    "conditions": [
        {"field": "type", "operator": "is", "value": "image"},
        {"field": "isGroup", "operator": "is", "value": True},
    ]
}


class RegisterError(RuntimeError):
    """Registration cannot proceed (bad secret, ...)."""


async def register_webhook(client: OpenWAClient, url: str, secret: str) -> tuple[str, str]:
    """Create or update the webhook for `url`; returns (action, webhook id)."""
    if len(secret) < MIN_SECRET_LEN:
        raise RegisterError(f"KINO_WEBHOOK_SECRET must be at least {MIN_SECRET_LEN} characters")
    body: dict[str, Any] = {"events": EVENTS, "secret": secret, "filters": FILTERS}
    for hook in await client.list_webhooks():
        if hook.get("url") == url:
            hook_id = str(hook["id"])
            await client.update_webhook(hook_id, {**body, "active": True})
            return "updated", hook_id
    return "created", await client.create_webhook({"url": url, **body})
