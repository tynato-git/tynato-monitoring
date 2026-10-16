from __future__ import annotations
import re
from typing import Optional

COMPONENTS = ("crm_total_api", "crm_database", "audio_evaluation", "invoice_generation", "meta_crm")

LABELS = {
    "crm_total_api": "CRM API (all routes)",
    "crm_database": "CRM database",
    "audio_evaluation": "Audio AI evaluation",
    "invoice_generation": "Invoice generation",
    "meta_crm": "Meta CRM",
}

# Route services are named crm_api_<method>__<path> (double underscore after method).
_ROUTE_RULES = (
    (re.compile(r"^crm_api_[a-z]+_+audio(_|$)"), "audio_evaluation"),
    (re.compile(r"^crm_api_[a-z]+_+invoices?(_|$)"), "invoice_generation"),
    (re.compile(r"^crm_api_[a-z]+_+meta(_|$)"), "meta_crm"),
    (re.compile(r"^crm_api_[a-z]+_+webhooks_+meta(_|$)"), "meta_crm"),
)
_LOG_RULES = (
    (re.compile(r"audio"), "audio_evaluation"),
    (re.compile(r"invoice"), "invoice_generation"),
    (re.compile(r"meta"), "meta_crm"),
)
_CHECK_MAP = {
    "crm_api": "crm_total_api",
    "crm_database": "crm_database",
    "invoice_api": "invoice_generation",
}


def component_for_service(service: str, category: str = "") -> Optional[str]:
    if category == "CRM_META_CONFIG_ISSUE":
        return "meta_crm"
    if service in ("container_invoice_api", "invoice_api"):
        return "invoice_generation"
    if service == "crm_database":
        return "crm_database"
    if service.startswith("crm_api_"):
        for pattern, comp in _ROUTE_RULES:
            if pattern.search(service):
                return comp
        return None
    if service.startswith("crm_log_"):
        for pattern, comp in _LOG_RULES:
            if pattern.search(service):
                return comp
    return None


def components_for_incident(service: str, category: str = "") -> list[str]:
    """An incident can count for its specific component and for the total CRM API."""
    if service == "crm_api":
        return ["crm_total_api"]
    primary = component_for_service(service, category)
    result = [primary] if primary else []
    if service.startswith("crm_api_") and primary != "crm_total_api":
        result.append("crm_total_api")
    return result


def component_for_check(check_name: str) -> Optional[str]:
    return _CHECK_MAP.get(check_name)