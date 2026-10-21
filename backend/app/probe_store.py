from __future__ import annotations
import json
from typing import Iterable

from . import db

# Verified against db.record_probe_result: opens an incident after
# FAILURE_THRESHOLD consecutive failures and resolves after RECOVERY_THRESHOLD successes.
FAILURE_THRESHOLD = 2
RECOVERY_THRESHOLD = 2

# Only probes that the host reports through /v1/probes are listed here.
# The worker's crm_api and crm_database probes write directly via db.record_probe_result.
PROBE_SPECS = {
    "invoice_api": {
        "service": "invoice_api",
        "category": "INVOICE_API_UNAVAILABLE",
    },
}


def latest_probes(names: Iterable[str]) -> dict[str, dict]:
    names = sorted(set(names))
    if not names:
        return {}
    marks = ",".join("?" for _ in names)
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT h.check_name, h.status, h.checked_at, h.latency_ms, h.error_details
            FROM health_checks h
            INNER JOIN (
                SELECT check_name, MAX(checked_at) AS latest
                FROM health_checks
                WHERE check_name IN ({marks})
                GROUP BY check_name
            ) x ON h.check_name = x.check_name AND h.checked_at = x.latest
            """,
            names,
        ).fetchall()
        streaks = {
            r["check_name"]: r["failure_streak"]
            for r in conn.execute(
                f"SELECT check_name, failure_streak FROM probe_state WHERE check_name IN ({marks})",
                names,
            ).fetchall()
        }
    result = {}
    for r in rows:
        result[r["check_name"]] = {
            "status": r["status"],
            "checked_at": r["checked_at"],
            "latency_ms": r["latency_ms"],
            "error_details": r["error_details"],
            "failure_streak": int(streaks.get(r["check_name"]) or 0),
        }
    return result


def failure_message(error_details: str | None) -> str:
    try:
        value = json.loads(error_details or "{}")
        message = str(value.get("message") or "") if isinstance(value, dict) else ""
    except (ValueError, TypeError):
        message = ""
    return message[:120]