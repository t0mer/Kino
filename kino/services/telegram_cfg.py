"""Telegram bot settings, managed in the database (Settings page).

Nothing here is read from the environment or config.yaml. The bot token is stored AES-256-GCM
encrypted (a token that cannot be decrypted reads as "not set"); the API URL, rate and options
are plain settings. ``api_url`` is an admin-only setting: the bot token goes into the request
path, so it is validated strictly (http(s), host, no userinfo/query/fragment) but deliberately
not allow-listed, since a self-hosted Bot API server on a private address is a supported setup.

Everything is read per call, so workers pick up a change without a restart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from loguru import logger
from sqlalchemy.orm import Session

from kino import secrets_store
from kino.config import Settings
from kino.db.models import Setting
from kino.db.session import get_session
from kino.services import InvalidError
from kino.services.openwa_cfg import _delete, _get, _load_key, _put

K_BOT_TOKEN = "telegram.bot_token_enc"
K_BOT_USERNAME = "telegram.bot_username"
K_SEND_AS_FILE = "telegram.send_as_file"
K_DELETE_ON_REMOVE = "telegram.delete_on_remove"
K_API_URL = "telegram.api_url"
K_RATE = "telegram.rate_per_min"
DEFAULT_API_URL = "https://api.telegram.org"
DEFAULT_RATE_PER_MIN = 20
MAX_RATE_PER_MIN = 600

_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
_BOT_URL_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")

__all__ = [
    "TelegramConfig",
    "check_token_format",
    "get_telegram_config",
    "load_telegram_config",
    "redact_token",
    "normalize_api_url",
    "save_telegram_connection",
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


def normalize_api_url(url: str) -> str:
    """Validate a Bot API base URL and strip its trailing slash; raises InvalidError."""
    url = (url or "").strip().rstrip("/")
    try:
        parts = urlsplit(url)
        host, _ = parts.hostname, parts.port
    except ValueError:
        raise InvalidError("api_url is not a valid URL") from None
    if parts.scheme not in ("http", "https") or not host:
        raise InvalidError("api_url must be an http(s) URL with a host")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise InvalidError("api_url must not contain credentials")
    if parts.query or parts.fragment or "?" in url or "#" in url:
        raise InvalidError("api_url must not contain a query or fragment")
    return url


def check_token_format(token: str) -> None:
    if not _TOKEN_RE.match(token):
        raise InvalidError("That does not look like a bot token (expected 123456:ABC-DEF...)")


def _flag(session: Session, key: str) -> bool:
    row = session.get(Setting, key)
    return row is not None and row.value is True


def _int_setting(session: Session, key: str, default: int) -> int:
    row = session.get(Setting, key)
    v = None if row is None else row.value
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 1 else default


def get_telegram_config(session: Session, env: Settings) -> TelegramConfig:
    token: str | None = None
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
        api_url=_get(session, K_API_URL) or DEFAULT_API_URL,
        rate_per_min=_int_setting(session, K_RATE, DEFAULT_RATE_PER_MIN),
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


def save_telegram_connection(
    session: Session, *, api_url: str | None = None, rate_per_min: int | None = None
) -> None:
    """Save the Bot API URL and/or send rate; an omitted (None) value stays unchanged."""
    if api_url is not None:
        url = normalize_api_url(api_url)
    if rate_per_min is not None and (
        isinstance(rate_per_min, bool) or not 1 <= rate_per_min <= MAX_RATE_PER_MIN
    ):
        raise InvalidError(f"rate_per_min must be between 1 and {MAX_RATE_PER_MIN}")
    if api_url is not None:
        _put(session, K_API_URL, url)
    if rate_per_min is not None:
        _put_int(session, K_RATE, rate_per_min)
    session.flush()


def _put_int(session: Session, key: str, value: int) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
