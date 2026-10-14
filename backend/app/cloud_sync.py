from __future__ import annotations
import json
import logging
import os
from datetime import datetime
from typing import Any

from . import db

logger = logging.getLogger("tynato-monitoring.cloud-sync")


def _dt(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _json(value: str | None) -> dict:
    try:
        loaded = json.loads(value or "{}")
        return loaded if isinstance(loaded, dict) else {"value": loaded}
    except json.JSONDecodeError:
        return {"message": (value or "")[:500]}


def sync_once() -> int:
    """Best-effort cloud mirror. Local SQLite and email outbox remain usable if Supabase fails."""
    url = os.getenv("MONITORING_DATABASE_URL", "").strip()
    if not url:
        return 0
    try:
        import psycopg
        from psycopg.types.json import Jsonb
        conn = psycopg.connect(url, connect_timeout=3, autocommit=True, prepare_threshold=None)
    except Exception as exc:
        logger.warning("Monitoring Supabase connection unavailable; local data retained (%s)", type(exc).__name__)
        return 0
    count = 0
    specs = [
        ("incidents", "id", """INSERT INTO public.monitoring_incidents
        (id,incident_key,service,category,severity,status,summary,details,customer_id,team_id,first_detected_at,last_observed_at,last_success_at,acknowledged_by,acknowledged_at,resolved_at,occurrence_count,updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status,severity=EXCLUDED.severity,summary=EXCLUDED.summary,details=EXCLUDED.details,last_observed_at=EXCLUDED.last_observed_at,last_success_at=EXCLUDED.last_success_at,acknowledged_by=EXCLUDED.acknowledged_by,acknowledged_at=EXCLUDED.acknowledged_at,resolved_at=EXCLUDED.resolved_at,occurrence_count=EXCLUDED.occurrence_count,updated_at=EXCLUDED.updated_at"""),
        ("health_checks", "id", """INSERT INTO public.monitoring_health_checks
        (id,check_name,status,checked_at,latency_ms,error_details) VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (id) DO NOTHING"""),
        ("monitoring_events", "event_id", """INSERT INTO public.monitoring_events
        (event_id,service,category,status,severity,customer_id,team_id,occurred_at,summary,details,received_at,incident_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (event_id) DO NOTHING"""),
        ("alert_outbox", "id", """INSERT INTO public.monitoring_alert_deliveries
        (id,incident_id,kind,recipient,status,attempts,provider_message_id,last_error,created_at,sent_at,updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status,attempts=EXCLUDED.attempts,provider_message_id=EXCLUDED.provider_message_id,last_error=EXCLUDED.last_error,sent_at=EXCLUDED.sent_at,updated_at=EXCLUDED.updated_at"""),
        ("worker_heartbeats", "worker_name", """INSERT INTO public.monitoring_worker_heartbeats
        (worker_name,status,last_seen_at,details,updated_at) VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (worker_name) DO UPDATE SET status=EXCLUDED.status,last_seen_at=EXCLUDED.last_seen_at,details=EXCLUDED.details,updated_at=EXCLUDED.updated_at"""),
    ]
    try:
        with conn:
            for table, key, sql in specs:
                for row in db.cloud_pending(table, 100):
                    if table == "incidents":
                        args = (row["id"],row["incident_key"],row["service"],row["category"],row["severity"],row["status"],row["summary"],Jsonb(_json(row["details"])),row["customer_id"],row["team_id"],_dt(row["first_detected_at"]),_dt(row["last_observed_at"]),_dt(row["last_success_at"]),row["acknowledged_by"],_dt(row["acknowledged_at"]),_dt(row["resolved_at"]),row["occurrence_count"],_dt(row["updated_at"]))
                    elif table == "health_checks":
                        args = (row["id"],row["check_name"],row["status"],_dt(row["checked_at"]),row["latency_ms"],Jsonb(_json(row["error_details"])))
                    elif table == "monitoring_events":
                        args = (row["event_id"],row["service"],row["category"],row["status"],row["severity"],row["customer_id"],row["team_id"],_dt(row["occurred_at"]),row["summary"],Jsonb(_json(row["details"])),_dt(row["received_at"]),row["incident_id"])
                    elif table == "alert_outbox":
                        args = (row["id"],row["incident_id"],row["kind"],row["recipient"],row["status"],row["attempts"],row["provider_message_id"],row["last_error"],_dt(row["created_at"]),_dt(row["sent_at"]),_dt(row["updated_at"]))
                    else:
                        args = (row["worker_name"],row["status"],_dt(row["last_seen_at"]),Jsonb(_json(row["details"])),_dt(row["updated_at"]))
                    conn.execute(sql, args)
                    db.mark_cloud_synced(table, key, row[key])
                    count += 1
    except Exception as exc:
        logger.warning("Supabase mirror attempt failed; unsynced data will be retried (%s)", type(exc).__name__)
    finally:
        conn.close()
    return count
