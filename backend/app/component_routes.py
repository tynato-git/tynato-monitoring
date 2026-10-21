from __future__ import annotations
from fastapi import APIRouter, Depends

from . import db
from .components import PROBE_TO_COMPONENT
from .component_status import build_component_status, overall_status
from .probe_store import latest_probes
from .security import current_monitoring_user

router = APIRouter()


@router.get("/v1/components")
def get_components(user=Depends(current_monitoring_user)) -> dict:
    incidents = db.list_incidents(500, "open") + db.list_incidents(500, "acknowledged")
    probes = latest_probes(PROBE_TO_COMPONENT.keys())
    items = build_component_status(incidents, probes)
    return {"overall": overall_status(items), "components": items}