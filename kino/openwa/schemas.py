"""OpenWA webhook payload models and signature verification.

Sources (doc-derived; no live instance was available when this was written):
  https://github.com/rmyndharis/openwa/blob/main/docs/06-api-specification.md
    - section 6.1  auth: header `X-API-Key`
    - section 6.6  webhook envelope + `message.received` fields
    - REST paths used by client.py (all under `/api`, `{sid}` = session id):
        GET  /sessions/{sid}/groups                           -> [{id, name, linkedParentJID}]
        GET  /sessions/{sid}/chats                            -> [{id, name, isGroup, kind, ...}]
        GET  /sessions/{sid}/messages/{chatId}/{messageId}/media -> raw bytes
        POST /sessions/{sid}/messages/send-text   {chatId, text}      -> 201 {messageId, timestamp}
        POST /sessions/{sid}/messages/send-image  {chatId, base64, mimetype, caption}
                                                                      -> 201 {messageId, timestamp}
  https://github.com/rmyndharis/openwa/blob/main/docs/examples/webhook-signature-verification.md
    - header `X-OpenWA-Signature: sha256=<hex>`, HMAC-SHA256 over the exact raw body,
      keyed by the webhook secret. Header is omitted when no secret is set.

Envelope: {event, timestamp, sessionId, idempotencyKey, deliveryId, data}.
`data` for message.received: id, from, to, body, type, timestamp (epoch SECONDS), isGroup,
kind, fromMe, chatId, media{mimetype, filename?, data?, omitted?, sizeBytes?}, contact{name?,
pushName?}. Media over WEBHOOK_MEDIA_INLINE_MAX_BYTES (default 1 MiB) arrives as
`media.omitted=true` with no data -> fetch via get-media.

UNVERIFIED -- check against live instance:
  * For group messages the sender is read from `data.author` (docs: "real sender of a group
    message, where from holds the group id" is documented for stored messages; the webhook
    field list does not list `author` explicitly). Falls back to `data.from`.
  * Whether the envelope `sessionId` is the session id used in REST paths (assumed yes).
  * Whether the media endpoint expects the bare message id or a `true_..._` serialized id
    (assumed: the `data.id` value as delivered).
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

SIGNATURE_HEADER = "X-OpenWA-Signature"
SIGNATURE_PREFIX = "sha256="
API_KEY_HEADER = "X-API-Key"
EVENT_MESSAGE_RECEIVED = "message.received"
# UNVERIFIED -- check against live instance
SENDER_FIELD_CANDIDATES = ("author", "from")


def verify_signature(secret: str, body: bytes, header_value: str | None) -> bool:
    """Constant-time check of an `X-OpenWA-Signature` header against the raw body."""
    if not secret or not header_value:
        return False
    received = header_value.strip()
    if received.startswith(SIGNATURE_PREFIX):
        received = received[len(SIGNATURE_PREFIX) :]
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(received.lower().encode(), expected.encode())


class WebhookEvent(BaseModel):
    """Flattened view of a `message.received` delivery."""

    model_config = ConfigDict(extra="ignore")

    session_id: str
    message_id: str
    chat_id: str
    is_group: bool
    sender_id: str | None = None
    sender_name: str | None = None
    timestamp: datetime
    media_type: str
    media_b64: str | None = None
    mimetype: str | None = None
    from_me: bool = False


def parse_webhook(payload: dict[str, Any]) -> WebhookEvent:
    """Build a WebhookEvent from the raw envelope; raises ValueError if malformed."""
    try:
        data = payload["data"]
        media = data.get("media") or {}
        contact = data.get("contact") or {}
        sender = next((data[k] for k in SENDER_FIELD_CANDIDATES if data.get(k)), None)
        return WebhookEvent(
            session_id=payload["sessionId"],
            message_id=data["id"],
            chat_id=data["chatId"],
            is_group=bool(data.get("isGroup", False)),
            sender_id=sender,
            sender_name=contact.get("pushName") or contact.get("name"),
            timestamp=datetime.fromtimestamp(float(data["timestamp"]), tz=UTC),
            media_type=data["type"],
            media_b64=media.get("data") or None,
            mimetype=media.get("mimetype") or None,
            from_me=bool(data.get("fromMe", False)),
        )
    except (KeyError, TypeError, AttributeError) as e:
        raise ValueError(f"malformed OpenWA webhook payload: missing/invalid {e}") from e


__all__ = ["WebhookEvent", "parse_webhook", "verify_signature"]
