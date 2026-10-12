"""AES-256-GCM encryption for secrets kept in the database (OpenWA API key, webhook secret).

The 32-byte key lives in ``<data_dir>/secret.key`` (mode 0600), never in the database, so a copy
of the database alone does not expose the secrets. Token format: ``v1:<urlsafe b64(nonce||ct)>``
with a random 12-byte nonce per encryption.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import os
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_FILE = "secret.key"
KEY_BYTES = 32
NONCE_BYTES = 12
PREFIX = "v1:"


class SecretKeyError(RuntimeError):
    """The key file exists but is unusable (wrong size)."""


class DecryptError(Exception):
    """A token cannot be decrypted: malformed, tampered, or made with another key."""


def key_path(data_dir: Path) -> Path:
    return data_dir / KEY_FILE


def read_key(data_dir: Path) -> bytes | None:
    """The key, or None when the file does not exist yet."""
    path = key_path(data_dir)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    if len(data) != KEY_BYTES:
        raise SecretKeyError(f"{path} must hold exactly {KEY_BYTES} bytes")
    return data


def load_key(data_dir: Path) -> bytes:
    """The key, created on first use.

    Creation never overwrites: a fresh key is written to a private temp file (0600) and
    hard-linked into place, which fails if another process got there first. Every process
    then reads back the one key that won.
    """
    existing = read_key(data_dir)
    if existing is not None:
        return existing
    data_dir.mkdir(parents=True, exist_ok=True)
    path = key_path(data_dir)
    tmp = data_dir / f".{KEY_FILE}.{os.getpid()}.{secrets.token_hex(4)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(secrets.token_bytes(KEY_BYTES))
            f.flush()
            os.fsync(f.fileno())
        with contextlib.suppress(FileExistsError):
            os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    key = read_key(data_dir)
    if key is None:  # pragma: no cover - the link above (or a concurrent one) created it
        raise SecretKeyError(f"could not create {path}")
    return key


def encrypt(plaintext: str, key: bytes) -> str:
    nonce = secrets.token_bytes(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt(token: str, key: bytes) -> str:
    if not token.startswith(PREFIX):
        raise DecryptError("unknown token format")
    try:
        raw = base64.urlsafe_b64decode(token[len(PREFIX) :].encode("ascii"))
    except (binascii.Error, ValueError):
        raise DecryptError("malformed token") from None
    if len(raw) <= NONCE_BYTES:
        raise DecryptError("malformed token")
    try:
        plain = AESGCM(key).decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], None)
    except InvalidTag:
        raise DecryptError("token does not match the key") from None
    return plain.decode("utf-8")
