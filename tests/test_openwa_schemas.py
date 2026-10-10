import hashlib
import hmac
import json
from datetime import UTC
from pathlib import Path

import pytest

from kino.openwa.schemas import WebhookEvent, parse_webhook, verify_signature

FIX = Path(__file__).parent / "fixtures" / "openwa"


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def test_image_group_parses():
    ev = parse_webhook(load("image_group.json"))
    assert isinstance(ev, WebhookEvent)
    assert ev.session_id == "kino-session"
    assert ev.message_id == "3EB0ABC123"
    assert ev.chat_id == "120363021234567890@g.us"
    assert ev.is_group is True
    assert ev.sender_id == "9725****456@c.us"
    assert ev.sender_name == "Dana K"
    assert ev.media_type == "image"
    assert ev.mimetype == "image/jpeg"
    assert ev.media_b64 and ev.from_me is False
    assert ev.timestamp.tzinfo is UTC
    assert ev.timestamp.year == 2026


def test_text_has_no_media():
    ev = parse_webhook(load("text_group.json"))
    assert ev.media_type == "text"
    assert ev.media_b64 is None and ev.mimetype is None


def test_non_group_and_omitted_media():
    ev = parse_webhook(load("image_direct.json"))
    assert ev.is_group is False
    assert ev.media_b64 is None
    assert ev.mimetype == "image/jpeg"
    assert ev.sender_id == "9725****456@c.us"


def test_extra_fields_ignored():
    d = load("text_group.json")
    d["data"]["zzz"] = 1
    d["extra"] = 2
    assert parse_webhook(d).message_id == "3EB0DEF456"


def test_missing_required_raises():
    d = load("text_group.json")
    del d["data"]["id"]
    with pytest.raises(ValueError):
        parse_webhook(d)


def _sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_verify_signature():
    body = b'{"a":1}'
    good = _sig("s3", body)
    assert verify_signature("s3", body, good)
    assert verify_signature("s3", body, good[len("sha256=") :])
    assert not verify_signature("s3", body, _sig("other", body))
    assert not verify_signature("s3", body + b" ", good)
    assert not verify_signature("s3", body, None)
    assert not verify_signature("s3", body, "")
    assert not verify_signature("s3", body, "sha256=abc")
    assert not verify_signature("", body, good)
