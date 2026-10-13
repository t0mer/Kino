"""OpenWA connection settings: environment first, then the database (setup wizard).

Each field resolves on its own: a set ``KINO_*`` variable wins, otherwise the value the wizard
saved. The API key and webhook secret are stored AES-256-GCM encrypted
(:mod:`kino.secrets_store`); a token that cannot be decrypted (``secret.key`` lost or replaced)
reads as "not set", so the wizard asks again instead of crashing.

Everything is read per call, so workers pick up a reconnect without a restart.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from loguru import logger
from pydantic import SecretStr
from sqlalchemy.orm import Session

from kino import secrets_store
from kino.config import Settings
from kino.db.models import Setting
from kino.db.session import get_session
from kino.services import ConflictError, StorageError

Field = Literal["url", "api_key", "session_id", "webhook_secret"]
FIELDS: tuple[Field, ...] = ("url", "api_key", "session_id", "webhook_secret")
ENV_NAMES: dict[Field, str] = {
    "url": "KINO_OPENWA_URL",
    "api_key": "KINO_OPENWA_API_KEY",
    "session_id": "KINO_OPENWA_SESSION_ID",
    "webhook_secret": "KINO_WEBHOOK_SECRET",
}
_LABELS: dict[Field, str] = {
    "url": "The OpenWA URL",
    "api_key": "The API key",
    "session_id": "The session",
    "webhook_secret": "The webhook secret",
}
K_URL = "openwa.url"
K_SESSION_ID = "openwa.session_id"
K_SESSION_NAME = "openwa.session_name"
# Optional separate session for outgoing sends (forwards, test photo, digest, chat pickers).
# Plain, database only; absent = send with the incoming session.
K_OUTGOING_SESSION_ID = "openwa.outgoing_session_id"
K_OUTGOING_SESSION_NAME = "openwa.outgoing_session_name"
K_API_KEY = "openwa.api_key_enc"
K_WEBHOOK_SECRET = "openwa.webhook_secret_enc"
# A replacement secret the wizard accepted but OpenWA has not been told yet. The webhook check
# keeps using the active secret; registering the webhook promotes this one.
K_WEBHOOK_SECRET_PENDING = "openwa.webhook_secret_pending_enc"
_KEYS: dict[Field, str] = {
    "url": K_URL,
    "api_key": K_API_KEY,
    "session_id": K_SESSION_ID,
    "webhook_secret": K_WEBHOOK_SECRET,
}
_ENCRYPTED: frozenset[str] = frozenset({"api_key", "webhook_secret"})

NOT_CONFIGURED_HINT = (
    "connect it in the web UI (setup wizard, later Settings > OpenWA) or set "
    "KINO_OPENWA_URL, KINO_OPENWA_API_KEY and KINO_OPENWA_SESSION_ID"
)


class OpenWANotConfiguredError(RuntimeError):
    """URL, API key, session id or webhook secret is missing in both env and database."""


class EnvLockedError(ConflictError):
    """The field is set in the environment or config.yaml, which always wins over the wizard."""


@dataclass(frozen=True, repr=False)
class OpenWAConfig:
    url: str | None
    api_key: str | None
    session_id: str | None
    webhook_secret: str | None
    session_name: str | None = None
    pending_webhook_secret: str | None = None
    # resolved: the stored outgoing session, else the incoming one
    outgoing_session_id: str | None = None
    outgoing_session_name: str | None = None

    def __post_init__(self) -> None:
        if self.outgoing_session_id is None:
            object.__setattr__(self, "outgoing_session_id", self.session_id)

    @property
    def same_as_incoming(self) -> bool:
        return self.outgoing_session_id == self.session_id

    @property
    def configured(self) -> bool:
        return bool(self.url and self.api_key and self.session_id and self.webhook_secret)

    @property
    def client_ready(self) -> bool:
        return bool(self.url and self.api_key and self.session_id)

    def client_args(self) -> tuple[str, str, str]:
        """(url, api_key, session_id) for ``OpenWAClient``; raises when one is missing."""
        if not (self.url and self.api_key and self.session_id):
            raise OpenWANotConfiguredError(f"OpenWA is not configured: {NOT_CONFIGURED_HINT}")
        return self.url, self.api_key, self.session_id

    def outgoing_client_args(self) -> tuple[str, str, str]:
        """(url, api_key, session_id) for sending; the outgoing session, else the incoming one."""
        if not (self.url and self.api_key and self.outgoing_session_id):
            raise OpenWANotConfiguredError(f"OpenWA is not configured: {NOT_CONFIGURED_HINT}")
        return self.url, self.api_key, self.outgoing_session_id

    def require_webhook_secret(self) -> str:
        if not self.webhook_secret:
            raise OpenWANotConfiguredError(
                "the webhook secret is not configured: set KINO_WEBHOOK_SECRET or use the "
                "setup wizard (Settings > OpenWA)"
            )
        return self.webhook_secret

    def registration_secret(self) -> str:
        """The secret to register with OpenWA: the pending one if any, else the active one."""
        return self.pending_webhook_secret or self.require_webhook_secret()

    def __repr__(self) -> str:
        return (
            f"OpenWAConfig(url={self.url!r}, session_id={self.session_id!r}, "
            f"api_key={'set' if self.api_key else None}, "
            f"webhook_secret={'set' if self.webhook_secret else None}, "
            f"pending_webhook_secret={'set' if self.pending_webhook_secret else None})"
        )


def _secret(value: SecretStr | None) -> str | None:
    return (value.get_secret_value() if value is not None else "") or None


def env_values(env: Settings) -> dict[Field, str | None]:
    """The four fields as given by the environment; empty strings count as unset."""
    return {
        "url": env.openwa_url or None,
        "api_key": _secret(env.openwa_api_key),
        "session_id": env.openwa_session_id or None,
        "webhook_secret": _secret(env.webhook_secret),
    }


def env_overrides(env: Settings) -> list[Field]:
    """Fields set by environment variables or config.yaml (read-only in the wizard)."""
    return [f for f, v in env_values(env).items() if v]


def ensure_unlocked(env: Settings, fields: Iterable[str]) -> None:
    wanted = set(fields)
    locked = [f for f in env_overrides(env) if f in wanted]
    if locked:
        raise EnvLockedError(
            "; ".join(
                f"{_LABELS[f]} is set in the environment or config.yaml ({ENV_NAMES[f]}) "
                "and cannot be changed here"
                for f in locked
            )
        )


def _get(session: Session, key: str) -> str | None:
    row = session.get(Setting, key)
    value = None if row is None else row.value
    return value if isinstance(value, str) and value else None


def _put(session: Session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value


def _delete(session: Session, key: str) -> None:
    row = session.get(Setting, key)
    if row is not None:
        session.delete(row)


_warned_undecryptable = False


def _decrypt(token: str, env: Settings) -> str | None:
    global _warned_undecryptable
    try:
        key = secrets_store.read_key(env.data_dir)
        if key is None:
            raise secrets_store.DecryptError("no key file")
        return secrets_store.decrypt(token, key)
    except (secrets_store.DecryptError, secrets_store.SecretKeyError, OSError):
        if not _warned_undecryptable:
            _warned_undecryptable = True
            logger.warning(
                "a stored OpenWA secret cannot be decrypted ({} lost or replaced?); "
                "run the OpenWA setup again",
                secrets_store.KEY_FILE,
            )
        return None


def get_openwa_config(session: Session, env: Settings) -> OpenWAConfig:
    from_env = env_values(env)
    values: dict[Field, str | None] = {}
    for f in FIELDS:
        if from_env[f]:
            values[f] = from_env[f]
            continue
        stored = _get(session, _KEYS[f])
        values[f] = _decrypt(stored, env) if stored and f in _ENCRYPTED else stored
    pending_token = None if from_env["webhook_secret"] else _get(session, K_WEBHOOK_SECRET_PENDING)
    out_id = _get(session, K_OUTGOING_SESSION_ID)
    return OpenWAConfig(
        url=values["url"],
        api_key=values["api_key"],
        session_id=values["session_id"],
        webhook_secret=values["webhook_secret"],
        session_name=None if from_env["session_id"] else _get(session, K_SESSION_NAME),
        pending_webhook_secret=_decrypt(pending_token, env) if pending_token else None,
        outgoing_session_id=out_id or values["session_id"],
        outgoing_session_name=(
            _get(session, K_OUTGOING_SESSION_NAME)
            if out_id
            else (None if from_env["session_id"] else _get(session, K_SESSION_NAME))
        ),
    )


def load_openwa_config(env: Settings) -> OpenWAConfig:
    """:func:`get_openwa_config` in its own DB session (workers, webhook, CLI)."""
    with get_session() as s:
        return get_openwa_config(s, env)


def save_openwa_config(
    session: Session,
    env: Settings,
    *,
    url: str | None = None,
    api_key: str | None = None,
    session_id: str | None = None,
    webhook_secret: str | None = None,
    session_name: str | None = None,
) -> None:
    """Store the given (non-None) fields; secrets encrypted. Env-set fields are refused."""
    pairs: tuple[tuple[Field, str | None], ...] = (
        ("url", url),
        ("api_key", api_key),
        ("session_id", session_id),
        ("webhook_secret", webhook_secret),
    )
    given: dict[Field, str] = {f: v for f, v in pairs if v is not None}
    ensure_unlocked(env, given)
    key: bytes | None = None
    for f, value in given.items():
        if f in _ENCRYPTED:
            if key is None:
                key = _load_key(env)
            value = secrets_store.encrypt(value, key)
        _put(session, _KEYS[f], value)
    if "session_id" in given:
        _put(session, K_SESSION_NAME, session_name or "")
    session.flush()


def _load_key(env: Settings) -> bytes:
    try:
        return secrets_store.load_key(env.data_dir)
    except (OSError, secrets_store.SecretKeyError) as e:
        raise StorageError(
            f"cannot read or create {secrets_store.KEY_FILE} in the data directory"
        ) from e


def save_webhook_secret(session: Session, env: Settings, secret: str) -> None:
    """Store a secret from the wizard without breaking a webhook OpenWA already signs.

    No active secret yet (fresh install): it becomes the active one. Otherwise a different
    secret is kept as pending until :func:`promote_webhook_secret`; re-entering the active
    secret drops any pending one."""
    active = get_openwa_config(session, env).webhook_secret
    if active is None:
        save_openwa_config(session, env, webhook_secret=secret)
        _delete(session, K_WEBHOOK_SECRET_PENDING)
    elif secret == active:
        _delete(session, K_WEBHOOK_SECRET_PENDING)
    else:
        ensure_unlocked(env, ["webhook_secret"])
        _put(session, K_WEBHOOK_SECRET_PENDING, secrets_store.encrypt(secret, _load_key(env)))
    session.flush()


def normalize_url(url: str) -> str:
    """Scheme, host, port and path (scheme and host lowercase, no trailing slash): two URLs
    with the same form point at the same OpenWA. Credentials and query are ignored."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = f"{host}:{port}" if port is not None else host
    return f"{parts.scheme.lower()}://{netloc}{parts.path.rstrip('/')}"


def url_changed(cfg: OpenWAConfig, new_url: str | None) -> bool:
    """True when ``new_url`` points at a different OpenWA than the effective URL.

    No effective URL yet (fresh install, or only some fields set by env) is not a change."""
    return bool(new_url and cfg.url and normalize_url(new_url) != normalize_url(cfg.url))


def clear_openwa_session(session: Session, env: Settings) -> None:
    """Forget the stored sessions (they belong to another OpenWA); an env-set one stays."""
    clear_outgoing_session(session)  # also belongs to the old OpenWA, even if the env pins the rest
    if "session_id" in env_overrides(env):
        return
    _delete(session, K_SESSION_ID)
    _delete(session, K_SESSION_NAME)
    session.flush()


def save_outgoing_session(session: Session, session_id: str, name: str | None) -> None:
    """Send with this session instead of the incoming one."""
    _put(session, K_OUTGOING_SESSION_ID, session_id)
    _put(session, K_OUTGOING_SESSION_NAME, name or "")
    session.flush()


def clear_outgoing_session(session: Session) -> None:
    """Back to sending with the incoming session."""
    _delete(session, K_OUTGOING_SESSION_ID)
    _delete(session, K_OUTGOING_SESSION_NAME)
    session.flush()


def promote_webhook_secret(session: Session, env: Settings, registered: str) -> None:
    """OpenWA now signs with ``registered``: make it active if it is the pending secret."""
    cfg = get_openwa_config(session, env)
    if cfg.pending_webhook_secret is None or cfg.pending_webhook_secret != registered:
        return
    save_openwa_config(session, env, webhook_secret=registered)
    _delete(session, K_WEBHOOK_SECRET_PENDING)
    session.flush()
