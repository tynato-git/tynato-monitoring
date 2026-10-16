from __future__ import annotations
from fastapi import APIRouter, Depends
from . import db
from .component_status import build_component_status, overall_status
from .security import current_monitoring_user

router = APIRouter()


@router.get("/v1/components")
def get_components(user=Depends(current_monitoring_user)) -> dict:
    incidents = db.list_incidents(500, "open") + db.list_incidents(500, "acknowledged")
    services = db.overview()["services"]
    items = build_component_status(incidents, services)
    return {"overall": overall_status(items), "components": items}