import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.components import components_for_incident, component_for_check
from app.component_status import build_component_status, overall_status


def inc(service, category, status="open", summary="x"):
    return {"id": service + status, "service": service, "category": category,
            "status": status, "summary": summary, "occurrence_count": 1,
            "last_observed_at": "2026-10-15T10:00:00Z"}


def by(items, comp):
    return next(i for i in items if i["component"] == comp)


def test_route_names_match_middleware_format():
    assert components_for_incident("crm_api_post__audio_upload") == ["audio_evaluation", "crm_total_api"]
    assert components_for_incident("crm_api_post__invoices") == ["invoice_generation", "crm_total_api"]
    assert components_for_incident("crm_api_get__meta_connections") == ["meta_crm", "crm_total_api"]
    assert components_for_incident("crm_api_post__webhooks_meta") == ["meta_crm", "crm_total_api"]
    assert components_for_incident("crm_api_get__audit") == ["crm_total_api"]


def test_other_services_map_correctly():
    assert components_for_incident("container_invoice_api") == ["invoice_generation"]
    assert components_for_incident("crm_database") == ["crm_database"]
    assert components_for_incident("crm_log_app.routes.audio") == ["audio_evaluation"]
    assert components_for_incident("crm_api") == ["crm_total_api"]
    assert components_for_incident("crm_log_app.meta", "CRM_META_CONFIG_ISSUE") == ["meta_crm"]
    assert component_for_check("invoice_api") == "invoice_generation"


def test_open_invoice_route_is_red_and_total_red():
    items = build_component_status([inc("crm_api_post__invoices", "CRM_ENDPOINT_FAILURE",
                                        summary="CRM endpoint returned HTTP 502: POST /invoices")], [])
    assert by(items, "invoice_generation")["status"] == "red"
    assert by(items, "crm_total_api")["status"] == "red"
    assert by(items, "audio_evaluation")["status"] == "grey"


def test_resolved_route_is_green():
    items = build_component_status([inc("crm_api_post__audio_upload", "CRM_ENDPOINT_FAILURE", status="resolved")], [])
    assert by(items, "audio_evaluation")["status"] == "green"


def test_meta_config_issue_is_amber_not_red():
    items = build_component_status([inc("crm_log_app.meta", "CRM_META_CONFIG_ISSUE")], [])
    assert by(items, "meta_crm")["status"] == "amber"


def test_failed_health_check_is_red_for_database_only():
    items = build_component_status([], [{"service": "crm_database", "status": "failed",
                                         "checked_at": "2026-10-15T10:00:00Z", "error": "OperationalError"}])
    assert by(items, "crm_database")["status"] == "red"
    assert by(items, "crm_total_api")["status"] == "grey"


def test_overall_is_worst_status():
    items = build_component_status([inc("container_invoice_api", "CONTAINER_UNHEALTHY")], [])
    assert overall_status(items) == "red"