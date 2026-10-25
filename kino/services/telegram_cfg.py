"""Telegram bot settings: environment first, then the database (Settings page).

The bot token resolves like the OpenWA secrets (:mod:`kino.services.openwa_cfg`): a set
``KINO_TELEGRAM_BOT_TOKEN`` wins, otherwise the value saved in the UI, stored AES-256-GCM
encrypted; a token that cannot be decrypted reads as "not set". The API URL and rate come from
the environment / config.yaml only. The options and the cached bot username are plain
database settings.

Everything is read per call, so workers pick up a change without a restart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session

from kino import secrets_store
from kino.config import Settings
from kino.db.models import Setting
from kino.db.session import get_session
from kino.services import InvalidError
from kino.services.openwa_cfg import EnvLockedError, _delete, _get, _load_key, _put

K_BOT_TOKEN = "telegram.bot_token_enc"
K_BOT_USERNAME = "telegram.bot_username"
K_SEND_AS_FILE = "telegram.send_as_file"
K_DELETE_ON_REMOVE = "telegram.delete_on_remove"
ENV_TOKEN = "KINO_TELEGRAM_BOT_TOKEN"

_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
_BOT_URL_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")

__all__ = [
    "EnvLockedError",
    "TelegramConfig",
    "env_overrides",
    "check_token_format",
    "ensure_unlocked",
    "get_telegram_config",
    "load_telegram_config",
    "redact_token",
    "save_telegram_options",
    "save_telegram_token",
]


@dataclass(frozen=True, repr=False)
class TelegramConfig:
    bot_token: str | None
    api_url: str
    rate_per_min: int
    send_as_file: bool = False
    delete_on_remove: bool = False
    bot_username: str | None = None

    @property
    def has_token(self) -> bool:
        return bool(self.bot_token)

    def __repr__(self) -> str:
        return (
            f"TelegramConfig(bot_token={'set' if self.bot_token else None}, "
            f"api_url={self.api_url!r}, rate_per_min={self.rate_per_min}, "
            f"send_as_file={self.send_as_file}, delete_on_remove={self.delete_on_remove}, "
            f"bot_username={self.bot_username!r})"
        )


def redact_token(text: str, token: str | None = None) -> str:
    """Replace the token and any ``bot<id>:<secret>`` URL fragment with ``bot***``."""
    text = _BOT_URL_RE.sub("bot***", text)
    return text.replace(token, "bot***") if token else text


def _env_token(env: Settings) -> str | None:
    value = env.telegram_bot_token
    return (value.get_secret_value() if value is not None else "") or None


def env_overrides(env: Settings) -> list[str]:
    """Fields set by environment variables or config.yaml (read-only in the UI)."""
    return ["bot_token"] if _env_token(env) else []


def ensure_unlocked(env: Settings) -> None:
    if _env_token(env):
        raise EnvLockedError(
            f"The bot token is set in the environment or config.yaml ({ENV_TOKEN}) "
            "and cannot be changed here"
        )


def check_token_format(token: str) -> None:
    if not _TOKEN_RE.match(token):
        raise InvalidError("That does not look like a bot token (expected 123456:ABC-DEF...)")


def _flag(session: Session, key: str) -> bool:
    row = session.get(Setting, key)
    return row is not None and row.value is True


def get_telegram_config(session: Session, env: Settings) -> TelegramConfig:
    token = _env_token(env)
    if token is None:
        stored = _get(session, K_BOT_TOKEN)
        if stored:
            try:
                key = secrets_store.read_key(env.data_dir)
                if key is None:
                    raise secrets_store.DecryptError("no key file")
                token = secrets_store.decrypt(stored, key)
            except (secrets_store.DecryptError, secrets_store.SecretKeyError, OSError):
                logger.warning(
                    "the stored Telegram bot token cannot be decrypted ({} lost or replaced?); "
                    "enter it again",
                    secrets_store.KEY_FILE,
                )
    return TelegramConfig(
        bot_token=token,
        api_url=env.telegram_api_url,
        rate_per_min=env.telegram_rate_per_min,
        send_as_file=_flag(session, K_SEND_AS_FILE),
        delete_on_remove=_flag(session, K_DELETE_ON_REMOVE),
        bot_username=_get(session, K_BOT_USERNAME),
    )


def load_telegram_config(env: Settings) -> TelegramConfig:
    """:func:`get_telegram_config` in its own DB session (workers, CLI)."""
    with get_session() as s:
        return get_telegram_config(s, env)


def save_telegram_token(
    session: Session, env: Settings, token: str | None, bot_username: str | None = None
) -> None:
    """Store (encrypted) or, for None/empty, clear the token and the cached bot username."""
    ensure_unlocked(env)
    token = (token or "").strip()
    if not token:
        _delete(session, K_BOT_TOKEN)
        _delete(session, K_BOT_USERNAME)
        session.flush()
        return
    check_token_format(token)
    _put(session, K_BOT_TOKEN, secrets_store.encrypt(token, _load_key(env)))
    if bot_username:
        _put(session, K_BOT_USERNAME, bot_username.strip())
    else:
        _delete(session, K_BOT_USERNAME)
    session.flush()


def _put_flag(session: Session, key: str, value: bool) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value


def save_telegram_options(
    session: Session, env: Settings, send_as_file: bool, delete_on_remove: bool
) -> None:
    _put_flag(session, K_SEND_AS_FILE, bool(send_as_file))
    _put_flag(session, K_DELETE_ON_REMOVE, bool(delete_on_remove))
    session.flush()
