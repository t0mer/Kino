import base64
import os
import stat
import threading

import pytest

from kino import secrets_store as ss


def test_roundtrip(tmp_path):
    key = ss.load_key(tmp_path)
    token = ss.encrypt("api-key-ü", key)
    assert token.startswith("v1:") and "api-key" not in token
    assert ss.decrypt(token, key) == "api-key-ü"


def test_nonce_is_random(tmp_path):
    key = ss.load_key(tmp_path)
    assert ss.encrypt("same", key) != ss.encrypt("same", key)


def test_wrong_key_tamper_and_garbage_raise(tmp_path):
    key = ss.load_key(tmp_path)
    token = ss.encrypt("secret-value", key)
    with pytest.raises(ss.DecryptError):
        ss.decrypt(token, os.urandom(32))
    raw = bytearray(base64.urlsafe_b64decode(token[3:]))
    raw[-1] ^= 1
    with pytest.raises(ss.DecryptError):
        ss.decrypt("v1:" + base64.urlsafe_b64encode(bytes(raw)).decode(), key)
    for bad in ("", "v2:abc", "v1:!!!", "v1:" + "A" * 8):
        with pytest.raises(ss.DecryptError):
            ss.decrypt(bad, key)


def test_key_file_created_0600_and_reused(tmp_path):
    key = ss.load_key(tmp_path)
    path = tmp_path / "secret.key"
    assert len(key) == 32 and path.read_bytes() == key
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert ss.load_key(tmp_path) == key
    assert [p.name for p in tmp_path.iterdir()] == ["secret.key"]  # no temp file left


def test_existing_key_never_overwritten(tmp_path):
    (tmp_path / "secret.key").write_bytes(b"k" * 32)
    assert ss.load_key(tmp_path) == b"k" * 32


def test_concurrent_creation_agrees(tmp_path):
    out: list[bytes] = []
    threads = [threading.Thread(target=lambda: out.append(ss.load_key(tmp_path))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(out)) == 1 and out[0] == (tmp_path / "secret.key").read_bytes()


def test_read_key_missing_and_bad_size(tmp_path):
    assert ss.read_key(tmp_path) is None
    (tmp_path / "secret.key").write_bytes(b"short")
    with pytest.raises(ss.SecretKeyError):
        ss.read_key(tmp_path)
    with pytest.raises(ss.SecretKeyError):
        ss.load_key(tmp_path)
