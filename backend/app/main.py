from __future__ import annotations
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal, Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict, field_validator

from . import db
from .config import csv_env
from .security import verify_event_signature, current_monitoring_user

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("tynato-monitoring.api")

@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    logger.info("Monitoring API started; local durable store: %s", os.getenv("MONITORING_DATA_DIR", ".local-data"))
    yield

_DOCS_ON = os.getenv("MONITORING_DOCS_ENABLED", "false").lower() == "true"
app = FastAPI(
    title="Tynato Monitoring API",
    version="0.1.0",
    docs_url="/docs" if _DOCS_ON else None,
    redoc_url="/redoc" if _DOCS_ON else None,
    openapi_url="/openapi.json" if _DOCS_ON else None,
    lifespan=lifespan,
)

from .component_routes import router as component_router
app.include_router(component_router)

_origins = csv_env("MONITORING_CORS_ORIGINS")
app.add_middleware(CORSMiddleware, allow_origins=_origins or ["http://localhost:3000"],
                   allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
                   allow_headers=["Authorization", "Content-Type", "X-Monitor-Timestamp", "X-Monitor-Signature"])


class MonitoringEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=8, max_length=128)
    service: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.:-]+$")
    category: str = Field(min_length=2, max_length=100, pattern=r"^[A-Z0-9_:-]+$")
    status: Literal["failure", "success"]
    severity: Literal["critical", "high", "medium", "low"] = "high"
    occurred_at: str | None = Field(default=None, max_length=40)
    customer_id: str | None = Field(default=None, max_length=128)
    team_id: str | None = Field(default=None, max_length=128)
    summary: str = Field(min_length=2, max_length=500)
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def validate_occurrence(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("occurred_at must be an ISO 8601 timestamp") from exc
        if parsed.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@app.get("/health/live")
def live() -> dict:
    return {"status": "alive"}


@app.get("/health/ready")
def ready() -> dict:
    try:
        db.init_db()
        with db.connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ready", "local_store": "ok"}
    except Exception:
        logger.exception("Readiness check failed")
        raise HTTPException(status_code=503, detail="Local monitoring store unavailable")


@app.post("/v1/events", status_code=202)
async def ingest_event(request: Request, x_monitor_timestamp: str | None = Header(default=None),
                       x_monitor_signature: str | None = Header(default=None)) -> dict:
    raw = await request.body()
    if len(raw) > 65536:
        raise HTTPException(status_code=413, detail="Event payload exceeds 64 KiB")
    verify_event_signature(x_monitor_timestamp, x_monitor_signature, raw)
    try:
        event = MonitoringEvent.model_validate_json(raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid event payload") from exc
    try:
        duplicate, incident_id = db.ingest_business_event(event.model_dump())
    except db.LocalQueueFullError:
        raise HTTPException(status_code=503, detail="Monitoring event queue is full; retry later", headers={"Retry-After": "60"})
    return {"accepted": True, "duplicate": duplicate, "incident_id": incident_id}


def _user(user=Depends(current_monitoring_user)):
    return user


@app.get("/v1/overview")
def get_overview(user=Depends(_user)) -> dict:
    return db.overview()


@app.get("/v1/incidents")
def get_incidents(status_filter: str | None = Query(default=None, alias="status"),
                  limit: int = Query(default=100, ge=1, le=500), user=Depends(_user)) -> dict:
    return {"items": db.list_incidents(limit, status_filter)}


@app.post("/v1/incidents/{incident_id}/acknowledge")
def acknowledge_incident(incident_id: str, user=Depends(_user)) -> dict:
    try:
        uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Incident not found")
    result = db.update_incident_status(incident_id, "acknowledged", user["user_id"])
    if not result:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"item": result}


@app.post("/v1/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: str, user=Depends(_user)) -> dict:
    try:
        uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Incident not found")
    result = db.update_incident_status(incident_id, "resolved", user["user_id"])
    if not result:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"item": result}
