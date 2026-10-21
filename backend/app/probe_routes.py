from __future__ import annotations
import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from . import db
from .probe_store import FAILURE_THRESHOLD, PROBE_SPECS, RECOVERY_THRESHOLD
from .security import verify_event_signature

logger = logging.getLogger("tynato-monitoring.probes")
router = APIRouter()


class ProbeReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_name: str = Field(min_length=3, max_length=40)
    ok: bool
    latency_ms: Optional[float] = Field(default=None, ge=0, le=60000)
    http_status: Optional[int] = Field(default=None, ge=100, le=599)
    error_type: Optional[str] = Field(default=None, max_length=80,
                                      pattern=r"^[A-Za-z0-9_.:-]+$")


@router.post("/v1/probes", status_code=202)
async def ingest_probe(
    request: Request,
    x_monitor_timestamp: Optional[str] = Header(default=None),
    x_monitor_signature: Optional[str] = Header(default=None),
) -> dict:
    raw = await request.body()
    if len(raw) > 4096:
        raise HTTPException(status_code=413, detail="Probe payload too large")
    verify_event_signature(x_monitor_timestamp, x_monitor_signature, raw)
    try:
        report = ProbeReport.model_validate_json(raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid probe payload") from exc

    spec = PROBE_SPECS.get(report.check_name)
    if spec is None:
        raise HTTPException(status_code=422, detail="Unknown probe")

    if report.ok:
        error = None
    elif report.http_status is not None:
        error = f"HTTP {report.http_status}"
    else:
        error = report.error_type or "unreachable"

    try:
        db.record_probe_result(
            report.check_name,
            success=report.ok,
            latency_ms=report.latency_ms,
            error=error,
            service=spec["service"],
            category=spec["category"],
            failure_threshold=FAILURE_THRESHOLD,
            recovery_threshold=RECOVERY_THRESHOLD,
        )
    except Exception:
        logger.exception("Probe result could not be stored for %s", report.check_name)
        raise HTTPException(status_code=503, detail="Probe store unavailable")
    return {"accepted": True}