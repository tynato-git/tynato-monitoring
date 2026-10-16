from __future__ import annotations
from .components import COMPONENTS, LABELS, components_for_incident, component_for_check

META_CONFIG = "CRM_META_CONFIG_ISSUE"
OPEN_STATES = {"open", "acknowledged"}


def _later(a: str | None, b: str | None) -> str | None:
    # Timestamps are UTC ISO-8601 strings, so string comparison is chronological.
    if not a:
        return b
    if not b:
        return a
    return a if a >= b else b


def build_component_status(incidents: list[dict], services: list[dict]) -> list[dict]:
    state = {c: {"red": [], "amber": [], "seen": False, "last": None, "ids": []} for c in COMPONENTS}

    for inc in incidents:
        for comp in components_for_incident(inc.get("service", ""), inc.get("category", "")):
            s = state[comp]
            s["seen"] = True
            s["last"] = _later(s["last"], inc.get("last_observed_at"))
            if inc.get("status") not in OPEN_STATES:
                continue
            s["ids"].append(inc.get("id"))
            text = f"{inc.get('summary', '')} (x{inc.get('occurrence_count', 1)})"
            (s["amber"] if inc.get("category") == META_CONFIG else s["red"]).append(text)

    for svc in services:
        comp = component_for_check(svc.get("service", ""))
        if not comp:
            continue
        s = state[comp]
        s["seen"] = True
        s["last"] = _later(s["last"], svc.get("checked_at"))
        if svc.get("status") != "ok" and not s["red"]:
            s["red"].append(f"Health check failed: {svc.get('error') or 'unknown error'}")

    out = []
    for comp in COMPONENTS:
        s = state[comp]
        if s["red"]:
            status, message = "red", "; ".join(s["red"][:3])
        elif s["amber"]:
            status, message = "amber", "; ".join(s["amber"][:3])
        elif s["seen"]:
            status, message = "green", "No open incident"
        else:
            status, message = "grey", "No failure recorded yet (no direct success signal)"
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