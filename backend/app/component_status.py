from __future__ import annotations
from datetime import datetime, timezone

from .components import COMPONENTS, LABELS, PROBE_TO_COMPONENT, components_for_incident
from .probe_store import failure_message

META_CONFIG = "CRM_META_CONFIG_ISSUE"
OPEN_STATES = {"open", "acknowledged"}
FRESH_SECONDS = 90
RED_AFTER_FAILURES = 2

_HTTP_LABELS = {
    "429": "rate limited",
    "500": "internal server error",
    "502": "bad gateway",
    "503": "service overloaded or unavailable",
    "504": "gateway timeout",
}


def _parse(ts):
    if not ts:
        return None
    try:
        value = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _age_seconds(ts, now):
    parsed = _parse(ts)
    return None if parsed is None else (now - parsed).total_seconds()


def _describe(error_details) -> str:
    message = failure_message(error_details)
    if message.startswith("HTTP "):
        code = message.split()[1]
        label = _HTTP_LABELS.get(code, "HTTP error")
        return f"{label} ({message})"
    return f"request failed: {message or 'unknown error'}"


def build_component_status(incidents: list[dict], probes: dict[str, dict],
                           now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    state = {c: {"red": [], "amber": [], "ok": False, "last": None, "ids": []}
             for c in COMPONENTS}

    # 1. Open incidents only. Resolved incidents never color a card.
    for inc in incidents:
        if inc.get("status") not in OPEN_STATES:
            continue
        comps = components_for_incident(inc.get("service", ""), inc.get("category", ""))
        if not comps:
            continue
        count = inc.get("occurrence_count") or 1
        text = f"{inc.get('summary') or inc.get('category') or 'incident'} (x{count})"
        for comp in comps:
            s = state[comp]
            s["ids"].append(inc.get("id"))
            if inc.get("category") == META_CONFIG:
                s["amber"].append(text)
            else:
                s["red"].append(text)

    # 2. Active probes. Green needs a fresh passing probe.
    for check, comp in PROBE_TO_COMPONENT.items():
        probe = probes.get(check)
        if not probe:
            continue
        s = state[comp]
        if not s["last"] or (_parse(probe["checked_at"]) and _parse(probe["checked_at"]) > _parse(s["last"])):
            s["last"] = probe["checked_at"]
        age = _age_seconds(probe.get("checked_at"), now)
        if probe["status"] == "ok":
            if age is not None and age <= FRESH_SECONDS:
                s["ok"] = True
            else:
                s["amber"].append("No fresh probe result (last check is older than 90 seconds)")
        else:
            streak = int(probe.get("failure_streak") or 0)
            reason = _describe(probe.get("error_details"))
            if streak >= RED_AFTER_FAILURES:
                s["red"].append(f"{check}: {reason}; {streak} failed checks in a row")
            else:
                s["amber"].append(f"{check}: {reason}; 1 failed check, waiting for confirmation")

    out = []
    for comp in COMPONENTS:
        s = state[comp]
        if s["red"]:
            status, message = "red", "; ".join(s["red"][:3])
        elif s["amber"]:
            status, message = "amber", "; ".join(s["amber"][:3])
        elif s["ok"]:
            status, message = "green", "Latest probe passed"
        else:
            status, message = "grey", "No live probe or open incident for this component"
        out.append({
            "component": comp,
            "label": LABELS[comp],
            "status": status,
            "message": message,
            "last_signal_at": s["last"],
            "incident_ids": s["ids"],
        })
    return out


def overall_status(items: list[dict]) -> str:
    statuses = {i["status"] for i in items}
    if "red" in statuses:
        return "red"
    if "amber" in statuses:
        return "amber"
    if statuses == {"green"}:
        return "green"
    return "grey"