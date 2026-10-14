import os
from pathlib import Path
import uuid

from app import db


def setup_tmp(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MONITORING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALERT_EMAIL_RECIPIENTS", "ops@example.test,admin@example.test")
    db.init_db()


def test_incident_dedup_and_recovery_alerts(monkeypatch, tmp_path):
    setup_tmp(monkeypatch, tmp_path)
    from app.db import connect, open_or_update_incident, resolve_incident
    with connect() as conn:
        first_id, created1 = open_or_update_incident(conn, incident_key="health:crm_api", service="crm_api",
            category="CRM_API_DOWN", severity="critical", summary="CRM unavailable", details={"token":"unsafe"})
        second_id, created2 = open_or_update_incident(conn, incident_key="health:crm_api", service="crm_api",
            category="CRM_API_DOWN", severity="critical", summary="Still unavailable", details={})
        assert created1 is True
        assert created2 is False
        assert first_id == second_id
        incident = conn.execute("SELECT * FROM incidents WHERE id=?", (first_id,)).fetchone()
        assert incident["occurrence_count"] == 2
        assert "REDACTED" in incident["details"]
        assert resolve_incident(conn, first_id)
        second_incident, created3 = open_or_update_incident(conn, incident_key="health:crm_api", service="crm_api",
            category="CRM_API_DOWN", severity="critical", summary="CRM failed again")
        assert created3 is True
        assert second_incident != first_id
        rows = conn.execute("SELECT kind FROM alert_outbox ORDER BY created_at,id").fetchall()
        assert {r["kind"] for r in rows} == {"critical", "recovery"}


def test_event_idempotency(monkeypatch, tmp_path):
    setup_tmp(monkeypatch, tmp_path)
    event = {"event_id": str(uuid.uuid4()), "service":"invoice", "category":"INVOICE_GENERATION_FAILED",
             "status":"failure", "severity":"critical", "occurred_at":None, "customer_id":"c-1",
             "team_id":None, "summary":"Invoice generation failed", "details":{"api_key":"secret","code":"write_failed"}}
    duplicate1, incident1 = db.ingest_business_event(event)
    duplicate2, incident2 = db.ingest_business_event(event)
    assert duplicate1 is False
    assert duplicate2 is True
    assert incident1 == incident2
    with db.connect() as conn:
        row = conn.execute("SELECT details FROM monitoring_events WHERE event_id=?", (event["event_id"],)).fetchone()
        assert "REDACTED" in row["details"]
