"""Channel lookup by name."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from kino.config import Settings, get_settings
from kino.delivery.base import DeliveryChannel
from kino.delivery.whatsapp import WhatsAppChannel
from kino.openwa.client import OpenWAClient
from kino.services.openwa_cfg import load_openwa_config


def get_channel(
    name: str,
    *,
    settings: Settings | None = None,
    client_factory: Callable[[], Any] | None = None,
) -> DeliveryChannel:
    """Build the named channel. `client_factory` overrides the underlying client (tests).

    Raises ValueError for unknown names and OpenWANotConfiguredError when WhatsApp has no config.
    """
    if name == "whatsapp":
        if client_factory is not None:
            return WhatsAppChannel(client_factory())
        cfg = load_openwa_config(settings or get_settings())
        return WhatsAppChannel(OpenWAClient(*cfg.outgoing_client_args()))
    raise ValueError(f"unknown delivery channel: {name!r}")
