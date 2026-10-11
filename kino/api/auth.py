"""Authentication core (CLAUDE.md section 13.1).

Two methods, both active:

* **Cloudflare Access**: a request carrying ``Cf-Access-Jwt-Assertion`` is authenticated by
  that signed JWT alone (JWKS signature, ``aud``, issuer, ``exp``, optional email allow-list).
  An invalid token is a hard 401; it never falls back to the local session.
  ``Cf-Access-Authenticated-User-Email`` is never read.
* **Local login**: username + argon2 hash (env ``KINO_ADMIN_PASSWORD_HASH`` wins over the
  first-run hash stored in ``settings['auth.password_hash']``), rate-limited per client IP,
  then a signed session cookie (itsdangerous, 7 days).

CSRF uses a double-submit token: cookie ``kino_csrf`` echoed in header ``X-CSRF-Token``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from itsdangerous import BadSignature, URLSafeTimedSerializer
from jwt.algorithms import RSAAlgorithm
from loguru import logger
from sqlalchemy.exc import IntegrityError

from kino import metrics
from kino.config import Settings
from kino.db.models import Setting
from kino.db.session import get_session

SESSION_COOKIE = "kino_session"
CSRF_COOKIE = "kino_csrf"
CSRF_HEADER = "X-CSRF-Token"
CF_JWT_HEADER = "Cf-Access-Jwt-Assertion"
SESSION_MAX_AGE = 7 * 24 * 3600
DEFAULT_ADMIN_USER = "admin"
MIN_PASSWORD_LENGTH = 8

K_PASSWORD_HASH = "auth.password_hash"
K_SECRET_KEY = "auth.secret_key"

LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW = 15 * 60.0
JWKS_TTL = 3600.0
JWKS_REFRESH_COOLDOWN = 30.0  # an unknown kid may force a refetch at most this often
JWKS_TIMEOUT = 5.0

# Requests that came through a proxy/tunnel carry one of these; first-run setup is LAN-only.
PROXY_HEADERS = ("cf-ray", "cf-connecting-ip", "x-forwarded-for", CF_JWT_HEADER.lower())

_hasher = PasswordHasher()


def _now() -> float:
    return time.time()


@dataclass(frozen=True)
class AuthInfo:
    method: Literal["cloudflare", "local"]
    user: str


class AuthError(Exception):
    """Authentication failed; maps to HTTP 401."""


# ---- settings rows ---------------------------------------------------------------------------


def _get_setting(key: str) -> Any:
    with get_session() as s:
        row = s.get(Setting, key)
        return None if row is None else row.value


def _insert_setting(key: str, value: Any) -> bool:
    """Insert ``key`` only if absent. Returns False when another request/process won."""
    try:
        with get_session() as s:
            if s.get(Setting, key) is not None:
                return False
            s.add(Setting(key=key, value=value))
    except IntegrityError:
        return False
    return True


def load_secret_key(settings: Settings) -> str:
    """Cookie signing key: ``KINO_SECRET_KEY``, else one generated once and kept in the DB."""
    if settings.secret_key and settings.secret_key.get_secret_value():
        return settings.secret_key.get_secret_value()
    stored = _get_setting(K_SECRET_KEY)
    if isinstance(stored, str) and stored:
        return stored
    if _insert_setting(K_SECRET_KEY, secrets.token_urlsafe(48)):
        logger.info("KINO_SECRET_KEY not set; generated one and stored it in the database")
    stored = _get_setting(K_SECRET_KEY)
    if not isinstance(stored, str) or not stored:
        raise RuntimeError("could not load or create the session signing key")
    return stored


# ---- local password --------------------------------------------------------------------------


def admin_user(settings: Settings) -> str:
    return settings.admin_user or DEFAULT_ADMIN_USER


def password_hash(settings: Settings) -> str | None:
    """The active admin hash: env wins over the first-run hash in the DB."""
    if settings.admin_password_hash and settings.admin_password_hash.get_secret_value():
        return settings.admin_password_hash.get_secret_value()
    stored = _get_setting(K_PASSWORD_HASH)
    return stored if isinstance(stored, str) and stored else None


def setup_required(settings: Settings) -> bool:
    return password_hash(settings) is None


def store_first_password(password: str) -> bool:
    """Store the first-run hash. False if one already exists (setup is one-time)."""
    return _insert_setting(K_PASSWORD_HASH, _hasher.hash(password))


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except VerificationError:
        return False
    except InvalidHashError:
        logger.error("the configured admin password hash is not a valid argon2 hash")
        return False


def _hash_fingerprint(stored_hash: str) -> str:
    # Sessions carry this, so changing the password invalidates every existing session.
    return hashlib.sha256(stored_hash.encode()).hexdigest()[:16]


# ---- session cookie --------------------------------------------------------------------------


def _serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt="kino-session")


def make_session(secret: str, user: str, stored_hash: str) -> str:
    return _serializer(secret).dumps({"u": user, "h": _hash_fingerprint(stored_hash)})


def read_session(secret: str, cookie: str, settings: Settings) -> AuthInfo | None:
    try:
        data = _serializer(secret).loads(cookie, max_age=SESSION_MAX_AGE)
    except BadSignature:  # includes SignatureExpired
        return None
    if not isinstance(data, dict):
        return None
    stored = password_hash(settings)
    user = admin_user(settings)
    if stored is None or data.get("u") != user:
        return None
    if not hmac.compare_digest(str(data.get("h", "")), _hash_fingerprint(stored)):
        return None
    return AuthInfo(method="local", user=user)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(cookie: str | None, header: str | None) -> bool:
    if not cookie or not header:
        return False
    return hmac.compare_digest(cookie.encode(), header.encode())


# ---- login rate limit ------------------------------------------------------------------------


class LoginLimiter:
    """At most ``LOGIN_MAX_FAILURES`` failed logins per IP in ``LOGIN_WINDOW``; then locked
    until the oldest failure leaves the window. In-memory: the API runs as one process.

    Keyed on the direct peer IP: behind a reverse proxy every client shares one bucket.

    Every attempt is recorded *before* the password is checked (:meth:`try_acquire`), so
    concurrent guesses cannot all slip past the limit while argon2 runs; a success clears it.
    """

    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, ip: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(ip, []) if now - t < LOGIN_WINDOW]
        if recent:
            self._failures[ip] = recent
        else:
            self._failures.pop(ip, None)
        return recent

    def try_acquire(self, ip: str) -> int:
        """Record an attempt for ``ip`` and return 0, or return the seconds until it may try
        again (nothing recorded) when it is locked out."""
        now = _now()
        with self._lock:
            recent = self._recent(ip, now)
            if len(recent) >= LOGIN_MAX_FAILURES:
                return max(1, int(recent[-LOGIN_MAX_FAILURES] + LOGIN_WINDOW - now) + 1)
            self._failures.setdefault(ip, []).append(now)
            if len(self._failures) > 10_000:  # bound memory under a spray of source IPs
                for k in list(self._failures):
                    self._recent(k, now)  # drops IPs with no failure left in the window
            return 0

    def success(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)


limiter = LoginLimiter()


# ---- Cloudflare Access -----------------------------------------------------------------------


def _fetch_jwks(url: str) -> dict[str, Any]:
    r = httpx.get(url, timeout=JWKS_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise ValueError("JWKS response is not an object")
    return data


class JwksCache:
    def __init__(self) -> None:
        self._keys: dict[str, Any] = {}
        self._url = ""
        self._fetched = 0.0
        self._lock = threading.Lock()

    def _refresh(self, url: str) -> None:
        data = _fetch_jwks(url)
        keys: dict[str, Any] = {}
        for jwk in data.get("keys", []):
            if isinstance(jwk, dict) and jwk.get("kty") == "RSA" and jwk.get("kid"):
                keys[str(jwk["kid"])] = RSAAlgorithm.from_jwk(jwk)
        self._keys, self._url, self._fetched = keys, url, _now()

    def key(self, url: str, kid: str) -> Any:
        with self._lock:
            age = _now() - self._fetched
            if url != self._url or age >= JWKS_TTL:
                self._refresh(url)
            elif kid not in self._keys and age >= JWKS_REFRESH_COOLDOWN:
                self._refresh(url)  # key rotation
            if kid not in self._keys:
                raise AuthError("unknown signing key")
            return self._keys[kid]


jwks = JwksCache()


def verify_cf_jwt(settings: Settings, token: str) -> AuthInfo:
    """Validate a Cloudflare Access JWT or raise :class:`AuthError`."""
    team = (settings.cf_access_team_domain or "").strip().removeprefix("https://").rstrip("/")
    aud = (settings.cf_access_aud or "").strip()
    if not team or not aud:
        raise AuthError("Cloudflare Access is not configured")
    issuer = f"https://{team}"
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        if not isinstance(kid, str) or not kid:
            raise AuthError("token has no key id")
        key = jwks.key(f"{issuer}/cdn-cgi/access/certs", kid)
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=aud,
            issuer=issuer,
            options={"require": ["exp", "iss", "aud"]},
        )
    except jwt.PyJWTError as e:
        raise AuthError(f"invalid token: {type(e).__name__}") from None
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Cloudflare Access JWKS fetch failed: {}", type(e).__name__)
        raise AuthError("could not fetch signing keys") from None
    email = claims.get("email")
    allowed = {
        e.strip().lower() for e in (settings.cf_access_allowed_emails or "").split(",") if e.strip()
    }
    if allowed and (not isinstance(email, str) or email.lower() not in allowed):
        raise AuthError("email not allowed")
    user = email or claims.get("common_name") or claims.get("sub") or "cloudflare"
    return AuthInfo(method="cloudflare", user=str(user))


def record(method: str, result: str) -> None:
    metrics.auth.labels(method=method, result=result).inc()


def reset_state() -> None:
    """Forget lockouts and cached keys (tests)."""
    global limiter, jwks
    limiter = LoginLimiter()
    jwks = JwksCache()
