import pathlib
import sys
import uuid

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("MONITORING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALERT_EMAIL_RECIPIENTS", "ops@example.com")
    monkeypatch.setenv("MONITORING_MAX_UNSYNCED_EVENTS", "1000")
    from app import db as dbmod
    dbmod.init_db()
    return dbmod


def ev(**overrides):
    base = {
        "event_id": str(uuid.uuid4()),
        "service": "crm_log_app_meta_worker",
        "category": "CRM_BACKGROUND_ERROR",
        "status": "failure",
        "severity": "high",
        "summary": "error",
        "details": {},
    }
    base.update(overrides)
    return base


def outbox_count(db):
    with db.connect() as conn:
        return conn.execute("SELECT COUNT(*) n FROM alert_outbox").fetchone()["n"]


def incidents(db, status=None):
    with db.connect() as conn:
        if status:
            return conn.execute("SELECT * FROM incidents WHERE status=?", (status,)).fetchall()
        return conn.execute("SELECT * FROM incidents").fetchall()


def test_meta_config_stored_not_emailed(db):
    db.ingest_business_event(ev(category="CRM_META_CONFIG_ISSUE", severity="medium"))
    assert len(incidents(db, "open")) == 1
    assert outbox_count(db) == 0


def test_repeated_meta_dedupes_without_email(db):
    for _ in range(3):
        db.ingest_business_event(ev(category="CRM_META_CONFIG_ISSUE", severity="medium"))
    rows = incidents(db, "open")
    assert len(rows) == 1
    assert rows[0]["occurrence_count"] == 3
    assert outbox_count(db) == 0


def test_generic_error_still_emails(db):
    db.ingest_business_event(ev())
    assert outbox_count(db) == 1


def test_http_500_still_emails(db):
    db.ingest_business_event(ev(
        service="crm_api_get_v1_x",
        category="CRM_ENDPOINT_FAILURE",
        severity="high",
    ))
    assert outbox_count(db) == 1


def test_duplicate_event_id_ignored(db):
    event = ev()
    db.ingest_business_event(event)
    duplicate, _ = db.ingest_business_event(event)
    assert duplicate is True
    assert len(incidents(db)) == 1


def test_unrelated_success_does_not_resolve(db):
    db.ingest_business_event(ev())
    db.ingest_business_event(ev(service="crm_log_other", status="success", severity="low"))
    assert len(incidents(db, "open")) == 1


def test_matching_success_resolves(db):
    db.ingest_business_event(ev())
    db.ingest_business_event(ev(status="success", severity="low"))
    assert len(incidents(db, "resolved")) == 1


def test_host_metrics_emits_no_routine_sample():
    path = ROOT / "deployment" / "host_metrics.py"
    if not path.exists():
        pytest.skip("host script is not in the container image; verified on host")
    assert "SYSTEM_METRICS_SAMPLE" not in path.read_text(encoding="utf-8")