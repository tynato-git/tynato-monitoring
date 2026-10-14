from __future__ import annotations
import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import httpx
import psycopg

from . import db
from .cloud_sync import sync_once
from .config import env_int

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("tynato-monitoring.worker")


def _send_resend_email(alert: dict) -> tuple[bool, str | None, str | None]:
    provider = os.getenv("ALERT_EMAIL_PROVIDER", "resend").lower()
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    sender = os.getenv("ALERT_FROM_EMAIL", "").strip()
    if provider != "resend":
        return False, None, f"Unsupported ALERT_EMAIL_PROVIDER: {provider}"
    if not api_key or not sender:
        return False, None, "Email provider is not configured"
    kind = alert["kind"]
    state = "RECOVERED" if kind == "recovery" else alert["severity"].upper()
    subject = f"[{state}] Tynato monitoring: {alert['service']} / {alert['category']}"
    text = (f"Tynato monitoring incident {kind}.\n\n"
            f"Service: {alert['service']}\nCategory: {alert['category']}\n"
            f"Severity: {alert['severity']}\nStatus: {alert['incident_status']}\n"
            f"Summary: {alert['summary']}\nIncident ID: {alert['incident_id']}\n"
            f"First detected (UTC): {alert['first_detected_at']}\n"
            f"Resolved at (UTC): {alert.get('resolved_at') or 'Not resolved'}\n")
    try:
        response = httpx.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                                      "Idempotency-Key": f"tynato-monitoring-alert-{alert['id']}"},
                              json={"from": sender, "to": [alert["recipient"]], "subject": subject, "text": text}, timeout=10.0)
        if response.status_code >= 200 and response.status_code < 300:
            try:
                data = response.json()
            except Exception:
                data = {}
            return True, data.get("id"), None
        return False, None, f"Email provider returned HTTP {response.status_code}: {response.text[:250]}"
    except Exception as exc:
        return False, None, f"Email provider request failed ({type(exc).__name__})"


def process_alerts() -> None:
    for alert in db.due_alerts(limit=10):
        success, message_id, error = _send_resend_email(alert)
        db.finish_alert(alert["id"], success=success, error=error, provider_message_id=message_id)
        if success:
            logger.info("Alert delivered: incident=%s kind=%s recipient=%s", alert["incident_id"], alert["kind"], alert["recipient"])
        else:
            logger.error("Alert delivery failed: incident=%s recipient=%s reason=%s", alert["incident_id"], alert["recipient"], error)


def check_http(name: str, url: str, service: str, category: str) -> None:
    if not url:
        return
    started = time.monotonic()
    try:
        response = httpx.get(url, timeout=env_int("PROBE_TIMEOUT_SECONDS", 5))
        latency = (time.monotonic() - started) * 1000
        if 200 <= response.status_code < 400:
            db.record_probe_result(name, success=True, latency_ms=latency, error=None, service=service, category=category,
                failure_threshold=env_int("FAILURES_BEFORE_INCIDENT", 3), recovery_threshold=env_int("SUCCESSES_TO_RECOVER", 2))
        else:
            db.record_probe_result(name, success=False, latency_ms=latency, error=f"HTTP {response.status_code}", service=service, category=category,
                failure_threshold=env_int("FAILURES_BEFORE_INCIDENT", 3), recovery_threshold=env_int("SUCCESSES_TO_RECOVER", 2))
    except Exception as exc:
        latency = (time.monotonic() - started) * 1000
        db.record_probe_result(name, success=False, latency_ms=latency, error=type(exc).__name__, service=service, category=category,
            failure_threshold=env_int("FAILURES_BEFORE_INCIDENT", 3), recovery_threshold=env_int("SUCCESSES_TO_RECOVER", 2))


def check_crm_database(url: str) -> None:
    if not url:
        return
    started = time.monotonic()
    try:
        with psycopg.connect(url, connect_timeout=env_int("PROBE_TIMEOUT_SECONDS", 5), prepare_threshold=None) as conn:
            conn.execute("SELECT 1").fetchone()
        db.record_probe_result("crm_database", success=True, latency_ms=(time.monotonic()-started)*1000, error=None,
            service="crm_database", category="CRM_DATABASE_UNAVAILABLE", failure_threshold=env_int("FAILURES_BEFORE_INCIDENT",3), recovery_threshold=env_int("SUCCESSES_TO_RECOVER",2))
    except Exception as exc:
        db.record_probe_result("crm_database", success=False, latency_ms=(time.monotonic()-started)*1000, error=type(exc).__name__,
            service="crm_database", category="CRM_DATABASE_UNAVAILABLE", failure_threshold=env_int("FAILURES_BEFORE_INCIDENT",3), recovery_threshold=env_int("SUCCESSES_TO_RECOVER",2))


def run_cycle() -> None:
    url = os.getenv("CRM_API_HEALTH_URL", "").strip()
    if url:
        check_http("crm_api", url, "crm_api", "CRM_API_DOWN")
    else:
        logger.warning("CRM_API_HEALTH_URL is empty; CRM API health probe is disabled")
    check_crm_database(os.getenv("CRM_DATABASE_URL", "").strip())
    db.heartbeat("monitor-worker", "healthy", {"last_cycle_at": db.utc_now()})
    process_alerts()
    sync_once()
    db.prune_local_history()


def main() -> None:
    db.init_db()
    if not os.getenv("ALERT_EMAIL_RECIPIENTS", "").strip():
        logger.error("ALERT_EMAIL_RECIPIENTS is empty; critical email delivery has no recipients")
    if not os.getenv("MONITORING_DATABASE_URL", "").strip():
        logger.warning("MONITORING_DATABASE_URL is empty; Supabase mirroring is disabled")
    if not os.getenv("RESEND_API_KEY", "").strip():
        logger.warning("RESEND_API_KEY is empty; alert rows will be retried but cannot be delivered")
    interval = env_int("PROBE_INTERVAL_SECONDS", 30)
    while True:
        started = time.monotonic()
        try:
            run_cycle()
        except Exception:
            logger.exception("Unexpected worker cycle failure; worker will retry next cycle")
        sleep_for = max(1, interval - (time.monotonic() - started))
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
