import hashlib
import hmac
import time
import pytest
from fastapi import HTTPException
from app.security import verify_event_signature


def test_valid_signature(monkeypatch):
    monkeypatch.setenv("EVENT_INGEST_HMAC_SECRET", "a" * 40)
    body = b'{"event_id":"event-0001"}'
    timestamp = str(int(time.time()))
    digest = hmac.new(("a" * 40).encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    verify_event_signature(timestamp, "sha256=" + digest, body)


def test_invalid_signature_rejected(monkeypatch):
    monkeypatch.setenv("EVENT_INGEST_HMAC_SECRET", "a" * 40)
    with pytest.raises(HTTPException) as exc:
        verify_event_signature(str(int(time.time())), "sha256=not-a-real-signature", b"{}")
    assert exc.value.status_code == 401


def test_missing_secret_fails_closed(monkeypatch):
    monkeypatch.setenv("EVENT_INGEST_HMAC_SECRET", "")
    with pytest.raises(HTTPException) as exc:
        verify_event_signature(str(int(time.time())), "sha256=anything", b"{}")
    assert exc.value.status_code == 503
