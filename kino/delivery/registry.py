"""Channel lookup by name."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from kino.config import Settings, get_settings
from kino.delivery.base import DeliveryChannel, DeliveryError
from kino.delivery.telegram import TelegramChannel
from kino.delivery.whatsapp import WhatsAppChannel
from kino.openwa.client import OpenWAClient
from kino.services.openwa_cfg import load_openwa_config


def get_channel(
    name: str,
    *,
    settings: Settings | None = None,
    client_factory: Callable[[], Any] | None = None,
    telegram_config: Any | None = None,
) -> DeliveryChannel:
    """Build the named channel. `client_factory` overrides the underlying client (tests).

    Raises ValueError for unknown names and OpenWANotConfiguredError when WhatsApp has no config.
    """
    if name == "whatsapp":
        if client_factory is not None:
            return WhatsAppChannel(client_factory())
        cfg = load_openwa_config(settings or get_settings())
        return WhatsAppChannel(OpenWAClient(*cfg.outgoing_client_args()))
    if name == "telegram":
        tcfg = telegram_config
        if tcfg is None:
            from kino.services.telegram_cfg import load_telegram_config

            tcfg = load_telegram_config(settings or get_settings())
        if not tcfg.bot_token:
            raise DeliveryError("Telegram bot token is not configured", permanent=True)
        return TelegramChannel(tcfg.bot_token, tcfg.api_url, send_as_file=tcfg.send_as_file)
    raise ValueError(f"unknown delivery channel: {name!r}")
