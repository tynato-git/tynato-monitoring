from __future__ import annotations
import re

COMPONENTS = (
    "crm_total_api",
    "crm_database",
    "audio_evaluation",
    "invoice_generation",
    "meta_crm",
)

LABELS = {
    "crm_total_api": "CRM API (all routes)",
    "crm_database": "CRM database",
    "audio_evaluation": "Audio AI evaluation",
    "invoice_generation": "Invoice generation",
    "meta_crm": "Meta CRM",
}

# Active probe name (health_checks.check_name) -> component.
PROBE_TO_COMPONENT = {
    "crm_api": "crm_total_api",
    "crm_database": "crm_database",
    "invoice_api": "invoice_generation",
}

META_CONFIG = "CRM_META_CONFIG_ISSUE"

_DIRECT = {
    "crm_api": "crm_total_api",
    "crm_database": "crm_database",
    "container_invoice_api": "invoice_generation",
    "invoice_api": "invoice_generation",
}

# Route services are named crm_api_<method>__<path> by the CRM middleware.
_ROUTE_RULES = (
    (re.compile(r"^crm_api_[a-z]+_+audio(_|$)"), "audio_evaluation"),
    (re.compile(r"^crm_api_[a-z]+_+invoices?(_|$)"), "invoice_generation"),
    (re.compile(r"^crm_api_[a-z]+_+meta(_|$)"), "meta_crm"),
    (re.compile(r"^crm_api_[a-z]+_+webhooks_+meta(_|$)"), "meta_crm"),
)

# CRM logger names reported by the log bridge, e.g. crm_log_app.routes.audio.
_LOG_RULES = (
    (re.compile(r"audio"), "audio_evaluation"),
    (re.compile(r"invoice"), "invoice_generation"),
    (re.compile(r"meta"), "meta_crm"),
)


def components_for_incident(service: str, category: str = "") -> list[str]:
    """Return the components an incident affects. Route incidents also count
    toward the total CRM API."""
    service = service or ""
    if category == META_CONFIG:
        return ["meta_crm"]
    if service in _DIRECT:
        return [_DIRECT[service]]
    if service.startswith("crm_api_"):
        for pattern, comp in _ROUTE_RULES:
            if pattern.search(service):
                return [comp, "crm_total_api"]
        return ["crm_total_api"]
    if service.startswith("crm_log_"):
        for pattern, comp in _LOG_RULES:
            if pattern.search(service):
                return [comp]
    return []