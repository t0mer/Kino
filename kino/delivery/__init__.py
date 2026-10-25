"""Delivery channels (WhatsApp today) behind one interface."""

from kino.delivery.base import DeliveryChannel, DeliveryError, SentRef
from kino.delivery.registry import get_channel
from kino.delivery.whatsapp import WhatsAppChannel

__all__ = ["DeliveryChannel", "DeliveryError", "SentRef", "WhatsAppChannel", "get_channel"]
