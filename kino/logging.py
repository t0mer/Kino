"""loguru setup."""

from __future__ import annotations

import sys

from loguru import logger

from kino.config import Settings


def setup_logging(settings: Settings) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level.upper(),
        serialize=settings.log_json,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message} {extra}",
    )
