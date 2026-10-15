from __future__ import annotations
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from .config import data_dir, local_db_path, csv_env, env_int


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def connect() -> sqlite3.Connection:
    path = local_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY,
            incident_key TEXT NOT NULL,
            service TEXT NOT NULL,
            category TEXT NOT NULL,
            severity TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('open','acknowledged','resolved')),
            summary TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '{}',
            customer_id TEXT,
            team_id TEXT,
            first_detected_at TEXT NOT NULL,
            last_observed_at TEXT NOT NULL,
            last_success_at TEXT,
            acknowledged_by TEXT,
            acknowledged_at TEXT,
            resolved_at TEXT,
            occurrence_count INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL,
            cloud_synced_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_incidents_active_key
            ON incidents(incident_key) WHERE status IN ('open','acknowledged');
        CREATE INDEX IF NOT EXISTS ix_incidents_updated ON incidents(updated_at DESC);
        CREATE TABLE IF NOT EXISTS health_checks (
            id TEXT PRIMARY KEY,
            check_name TEXT NOT NULL,
            status TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            latency_ms REAL,
            error_details TEXT,
            cloud_synced_at TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_health_checked ON health_checks(checked_at DESC);
        CREATE TABLE IF NOT EXISTS monitoring_events (
            event_id TEXT PRIMARY KEY,
            service TEXT NOT NULL,
            category TEXT NOT NULL,
            status TEXT NOT NULL,
            severity TEXT NOT NULL,
            customer_id TEXT,
            team_id TEXT,
            occurred_at TEXT NOT NULL,
            summary TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '{}',
            received_at TEXT NOT NULL,
            incident_id TEXT,
            cloud_synced_at TEXT
        );
        CREATE TABLE IF NOT EXISTS alert_outbox (
            id TEXT PRIMARY KEY,
            incident_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('critical','recovery')),
            recipient TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent')),
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT NOT NULL,
            provider_message_id TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            sent_at TEXT,
            updated_at TEXT NOT NULL,
            cloud_synced_at TEXT,
            UNIQUE(incident_id,kind,recipient),
            FOREIGN KEY(incident_id) REFERENCES incidents(id)
        );
        CREATE INDEX IF NOT EXISTS ix_outbox_due ON alert_outbox(status,next_attempt_at);
        CREATE TABLE IF NOT EXISTS probe_state (
            check_name TEXT PRIMARY KEY,
            failure_streak INTEGER NOT NULL DEFAULT 0,
            success_streak INTEGER NOT NULL DEFAULT 0,
            current_incident_id TEXT,
            last_status TEXT NOT NULL DEFAULT 'unknown',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS worker_heartbeats (
            worker_name TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL,
            cloud_synced_at TEXT
        );
        """)


def sanitize_data(value: Any, depth: int = 0) -> Any:
    """Bound untrusted telemetry and redact common credential-like keys."""
    if depth > 5:
        return "[truncated]"
    secret_keys = {"password", "passwd", "secret", "token", "access_token", "refresh_token",
                   "authorization", "api_key", "apikey", "private_key", "database_url", "connection_string"}
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:50]:
            k = str(key)[:100]
            result[k] = "[REDACTED]" if k.lower() in secret_keys or any(k.lower().endswith("_" + s) for s in secret_keys) else sanitize_data(item, depth + 1)
        return result
    if isinstance(value, list):
        return [sanitize_data(item, depth + 1) for item in value[:50]]
    if isinstance(value, str):
        return value[:1000]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)[:1000]

NO_EMAIL_CATEGORIES = {"CRM_META_CONFIG_ISSUE"}

def _enqueue_alert(conn: sqlite3.Connection, incident: sqlite3.Row | dict, kind: str) -> None:
   
    if incident["category"] in NO_EMAIL_CATEGORIES:
        return
    recipients = csv_env("ALERT_EMAIL_RECIPIENTS")
    now = utc_now()
    for recipient in recipients:
        conn.execute("""INSERT OR IGNORE INTO alert_outbox
            (id,incident_id,kind,recipient,status,attempts,next_attempt_at,created_at,updated_at)
            VALUES (?,?,?,?,'pending',0,?,?,?)""",
            (str(uuid.uuid4()), incident["id"], kind, recipient, now, now, now))

def _active_incident(conn: sqlite3.Connection, key: str):
    return conn.execute("SELECT * FROM incidents WHERE incident_key=? AND status IN ('open','acknowledged') ORDER BY first_detected_at DESC LIMIT 1", (key,)).fetchone()


def open_or_update_incident(conn: sqlite3.Connection, *, incident_key: str, service: str,
                            category: str, severity: str, summary: str, details: dict | None = None,
                            customer_id: str | None = None, team_id: str | None = None,
                            occurred_at: str | None = None) -> tuple[str, bool]:
    now = occurred_at or utc_now()
    safe_details = json.dumps(sanitize_data(details or {}), separators=(",", ":"))
    existing = _active_incident(conn, incident_key)
    if existing:
        try:
            prior_details = json.loads(existing["details"] or "{}")
        except json.JSONDecodeError:
            prior_details = {}
        incoming_details = sanitize_data(details or {})
        if isinstance(prior_details, dict) and isinstance(incoming_details, dict):
            prior_details.update(incoming_details)
            safe_details = json.dumps(prior_details, separators=(",", ":"))
        conn.execute("""UPDATE incidents SET last_observed_at=?, occurrence_count=occurrence_count+1,
            summary=?, details=?, severity=CASE WHEN severity='critical' OR ?='critical' THEN 'critical' ELSE severity END,
            updated_at=?, cloud_synced_at=NULL WHERE id=?""",
            (now, summary[:500], safe_details, severity, utc_now(), existing["id"]))
        return existing["id"], False
    last_success = conn.execute("SELECT checked_at FROM health_checks WHERE check_name=? AND status='ok' ORDER BY checked_at DESC LIMIT 1", (incident_key.removeprefix("health:"),)).fetchone() if incident_key.startswith("health:") else None
    incident_id = str(uuid.uuid4())
    conn.execute("""INSERT INTO incidents
        (id,incident_key,service,category,severity,status,summary,details,customer_id,team_id,
         first_detected_at,last_observed_at,last_success_at,occurrence_count,updated_at)
        VALUES (?,?,?,?,?,'open',?,?,?,?,?,?,?,?,?)""",
        (incident_id, incident_key, service, category, severity, summary[:500], safe_details,
         customer_id, team_id, now, now, last_success["checked_at"] if last_success else None,
         1, utc_now()))
    row = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
    _enqueue_alert(conn, row, "critical")
    return incident_id, True


def resolve_incident(conn: sqlite3.Connection, incident_id: str, recovered_at: str | None = None) -> bool:
    row = conn.execute("SELECT * FROM incidents WHERE id=? AND status IN ('open','acknowledged')", (incident_id,)).fetchone()
    if not row:
        return False
    now = recovered_at or utc_now()
    conn.execute("UPDATE incidents SET status='resolved',last_success_at=?,resolved_at=?,updated_at=?,cloud_synced_at=NULL WHERE id=?",
                 (now, now, utc_now(), incident_id))
    resolved = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
    _enqueue_alert(conn, resolved, "recovery")
    return True


def record_probe_result(check_name: str, *, success: bool, latency_ms: float | None,
                        error: str | None, service: str, category: str,
                        failure_threshold: int, recovery_threshold: int) -> None:
    now = utc_now()
    check_id = str(uuid.uuid4())
    status = "ok" if success else "failed"
    safe_error = (str(error)[:500] if error else None)
    incident_key = f"health:{check_name}"
    with connect() as conn:
        conn.execute("INSERT INTO health_checks(id,check_name,status,checked_at,latency_ms,error_details) VALUES(?,?,?,?,?,?)",
                     (check_id, check_name, status, now, latency_ms, json.dumps({"message": safe_error}) if safe_error else None))
        state = conn.execute("SELECT * FROM probe_state WHERE check_name=?", (check_name,)).fetchone()
        failures = (state["failure_streak"] if state else 0) + (0 if success else 1)
        successes = (state["success_streak"] if state else 0) + (1 if success else 0)
        if not success:
            successes = 0
        else:
            failures = 0
        current_id = state["current_incident_id"] if state else None
        last_status = status
        if not success and failures >= failure_threshold:
            incident_id, _ = open_or_update_incident(conn, incident_key=incident_key, service=service,
                category=category, severity="critical", summary=f"{service} health check failed",
                details={"check_name": check_name, "error": safe_error}, occurred_at=now)
            current_id = incident_id
        elif success and current_id:
            if successes >= recovery_threshold:
                resolve_incident(conn, current_id, now)
                current_id = None
        conn.execute("""INSERT INTO probe_state(check_name,failure_streak,success_streak,current_incident_id,last_status,updated_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(check_name) DO UPDATE SET failure_streak=excluded.failure_streak,
            success_streak=excluded.success_streak,current_incident_id=excluded.current_incident_id,
            last_status=excluded.last_status,updated_at=excluded.updated_at""",
            (check_name, failures, successes, current_id, last_status, now))


class LocalQueueFullError(RuntimeError):
    """The bounded local event spool is full; callers should retry later."""


def ingest_business_event(event: dict) -> tuple[bool, str | None]:
    now = utc_now()
    details = sanitize_data(event.get("details") or {})
    event_id = event["event_id"]
    service = event["service"]
    category = event["category"]
    customer_id = event.get("customer_id")
    team_id = event.get("team_id")
    incident_key = f"business:{service}:{category}:{customer_id or 'global'}"
    with connect() as conn:
        existing = conn.execute("SELECT event_id,incident_id FROM monitoring_events WHERE event_id=?", (event_id,)).fetchone()
        if existing:
            return True, existing["incident_id"]
        max_pending = env_int("MONITORING_MAX_UNSYNCED_EVENTS", 10000)
        pending_count = conn.execute("SELECT COUNT(*) n FROM monitoring_events WHERE cloud_synced_at IS NULL").fetchone()["n"]
        if pending_count >= max_pending:
            raise LocalQueueFullError("local monitoring event spool is full")
        conn.execute("""INSERT INTO monitoring_events
            (event_id,service,category,status,severity,customer_id,team_id,occurred_at,summary,details,received_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (event_id, service, category, event["status"], event["severity"], customer_id, team_id,
             event.get("occurred_at") or now, event["summary"][:500], json.dumps(details, separators=(",", ":")), now))
        incident_id = None
        if event["status"] == "failure":
            incident_id, _ = open_or_update_incident(conn, incident_key=incident_key, service=service,
                category=category, severity=event["severity"], summary=event["summary"], details=details,
                customer_id=customer_id, team_id=team_id, occurred_at=event.get("occurred_at") or now)
        else:
            active = _active_incident(conn, incident_key)
            if active:
                incident_id = active["id"]
                resolve_incident(conn, incident_id, event.get("occurred_at") or now)
        conn.execute("UPDATE monitoring_events SET incident_id=? WHERE event_id=?", (incident_id, event_id))
        return False, incident_id


def list_incidents(limit: int = 100, status: str | None = None) -> list[dict]:
    limit = min(max(limit, 1), 500)
    with connect() as conn:
        if status in {"open", "acknowledged", "resolved"}:
            rows = conn.execute("SELECT * FROM incidents WHERE status=? ORDER BY updated_at DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM incidents ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [_incident_dict(row) for row in rows]


def _incident_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    try:
        item["details"] = json.loads(item.get("details") or "{}")
    except json.JSONDecodeError:
        item["details"] = {}
    return item


def get_incident(incident_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
    return _incident_dict(row) if row else None


def update_incident_status(incident_id: str, status: str, actor: str) -> dict | None:
    now = utc_now()
    with connect() as conn:
        row = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if not row:
            return None
        if status == "acknowledged" and row["status"] in ("open", "acknowledged"):
            conn.execute("UPDATE incidents SET status='acknowledged',acknowledged_by=?,acknowledged_at=?,updated_at=?,cloud_synced_at=NULL WHERE id=?",
                         (actor, now, now, incident_id))
        elif status == "resolved":
            resolve_incident(conn, incident_id, now)
        updated = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        return _incident_dict(updated) if updated else None


def overview() -> dict:
    with connect() as conn:
        active = conn.execute("SELECT COUNT(*) n FROM incidents WHERE status IN ('open','acknowledged')").fetchone()["n"]
        open_count = conn.execute("SELECT COUNT(*) n FROM incidents WHERE status='open'").fetchone()["n"]
        failed_24h = conn.execute("SELECT COUNT(*) n FROM health_checks WHERE status='failed' AND checked_at >= ?", ((datetime.now(timezone.utc)-timedelta(hours=24)).isoformat().replace('+00:00','Z'),)).fetchone()["n"]
        checks = conn.execute("""SELECT h.* FROM health_checks h INNER JOIN
            (SELECT check_name,MAX(checked_at) latest FROM health_checks GROUP BY check_name) x
            ON h.check_name=x.check_name AND h.checked_at=x.latest ORDER BY h.check_name""").fetchall()
        pending = conn.execute("SELECT COUNT(*) n FROM alert_outbox WHERE status='pending'").fetchone()["n"]
        heartbeat = conn.execute("SELECT worker_name,status,last_seen_at,details FROM worker_heartbeats").fetchall()
    services = []
    for row in checks:
        services.append({"service": row["check_name"], "status": row["status"], "checked_at": row["checked_at"],
                         "latency_ms": row["latency_ms"], "error": json.loads(row["error_details"] or "null")})
    return {"generated_at": utc_now(), "active_incidents": active, "open_incidents": open_count,
            "failed_checks_24h": failed_24h, "pending_alerts": pending, "services": services,
            "workers": [dict(r) for r in heartbeat]}


def heartbeat(worker_name: str, status: str = "healthy", details: dict | None = None) -> None:
    now = utc_now()
    payload = json.dumps(sanitize_data(details or {}), separators=(",", ":"))
    with connect() as conn:
        conn.execute("""INSERT INTO worker_heartbeats(worker_name,status,last_seen_at,details,updated_at)
        VALUES(?,?,?,?,?) ON CONFLICT(worker_name) DO UPDATE SET status=excluded.status,last_seen_at=excluded.last_seen_at,
        details=excluded.details,updated_at=excluded.updated_at,cloud_synced_at=NULL""",
        (worker_name, status, now, payload, now))


def due_alerts(limit: int = 10) -> list[dict]:
    now = utc_now()
    with connect() as conn:
        rows = conn.execute("""SELECT a.*, i.service,i.category,i.severity,i.summary,i.first_detected_at,i.resolved_at,i.status incident_status
          FROM alert_outbox a JOIN incidents i ON i.id=a.incident_id
          WHERE a.status='pending' AND a.next_attempt_at<=? ORDER BY a.created_at LIMIT ?""", (now, limit)).fetchall()
    return [dict(r) for r in rows]


def finish_alert(alert_id: str, *, success: bool, error: str | None = None, provider_message_id: str | None = None) -> None:
    now = utc_now()
    with connect() as conn:
        row = conn.execute("SELECT attempts FROM alert_outbox WHERE id=?", (alert_id,)).fetchone()
        if not row:
            return
        attempts = row["attempts"] + 1
        if success:
            conn.execute("UPDATE alert_outbox SET status='sent',attempts=?,provider_message_id=?,last_error=NULL,sent_at=?,updated_at=?,cloud_synced_at=NULL WHERE id=?",
                         (attempts, provider_message_id, now, now, alert_id))
        else:
            delay = min(3600, 15 * (2 ** min(attempts - 1, 8)))
            retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            conn.execute("UPDATE alert_outbox SET attempts=?,last_error=?,next_attempt_at=?,updated_at=?,cloud_synced_at=NULL WHERE id=?",
                         (attempts, (error or "delivery failed")[:500], retry_at, now, alert_id))


def cloud_pending(table: str, limit: int = 100) -> list[dict]:
    allowed = {"incidents", "health_checks", "monitoring_events", "alert_outbox", "worker_heartbeats"}
    if table not in allowed:
        raise ValueError("Unsupported table")
    with connect() as conn:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE cloud_synced_at IS NULL LIMIT ?", (limit,)).fetchall()]


def mark_cloud_synced(table: str, key_col: str, key_value: str) -> None:
    allowed = {"incidents", "health_checks", "monitoring_events", "alert_outbox", "worker_heartbeats"}
    allowed_keys = {"incidents": "id", "health_checks": "id", "monitoring_events": "event_id", "alert_outbox": "id", "worker_heartbeats": "worker_name"}
    if table not in allowed or allowed_keys[table] != key_col:
        raise ValueError("Unsupported sync target")
    with connect() as conn:
        conn.execute(f"UPDATE {table} SET cloud_synced_at=? WHERE {key_col}=?", (utc_now(), key_value))


def prune_local_history() -> None:
    """Bound routine health-history growth; incidents and unsent alert evidence are retained."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    with connect() as conn:
        conn.execute("DELETE FROM health_checks WHERE checked_at < ? AND cloud_synced_at IS NOT NULL", (cutoff,))
        conn.execute("DELETE FROM monitoring_events WHERE occurred_at < ? AND cloud_synced_at IS NOT NULL", (cutoff,))
        # Keep the local health-check history bounded even during a long Supabase outage.
        conn.execute("DELETE FROM health_checks WHERE id NOT IN (SELECT id FROM health_checks ORDER BY checked_at DESC, rowid DESC LIMIT 50000)")
