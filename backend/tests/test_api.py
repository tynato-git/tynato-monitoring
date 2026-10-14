import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient
from app.main import app


def test_health_and_signed_event(monkeypatch, tmp_path):
    monkeypatch.setenv("MONITORING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EVENT_INGEST_HMAC_SECRET", "monitoring-test-secret-" + "x" * 24)
    monkeypatch.setenv("ALERT_EMAIL_RECIPIENTS", "operator@example.test")
    event = {
        "event_id": "test-event-0001",
        "service": "invoice",
        "category": "INVOICE_GENERATION_FAILED",
        "status": "failure",
        "severity": "critical",
        "occurred_at": "2026-10-14T07:00:00Z",
        "customer_id": "cust-test-1",
        "summary": "Test invoice failure",
        "details": {"api_key": "must-not-be-stored", "error_code": "WRITE_FAILED"},
    }
    body = json.dumps(event, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    secret = __import__("os").environ["EVENT_INGEST_HMAC_SECRET"]
    signature = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        response = client.post("/v1/events", content=body, headers={
            "Content-Type": "application/json",
            "X-Monitor-Timestamp": timestamp,
            "X-Monitor-Signature": "sha256=" + signature,
        })
        assert response.status_code == 202
        assert response.json()["duplicate"] is False
        duplicate = client.post("/v1/events", content=body, headers={
            "Content-Type": "application/json",
            "X-Monitor-Timestamp": timestamp,
            "X-Monitor-Signature": "sha256=" + signature,
        })
        assert duplicate.status_code == 202
        assert duplicate.json()["duplicate"] is True
        assert client.get("/v1/overview").status_code == 401
