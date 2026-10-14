from __future__ import annotations
import hashlib
import hmac
import os
import time
from typing import Any

import jwt
from jwt import PyJWKClient
from fastapi import HTTPException, Request, status

from .config import issuer_url, jwks_url, jwt_audience

_jwks_client: PyJWKClient | None = None


def verify_event_signature(timestamp_header: str | None, signature_header: str | None, body: bytes) -> None:
    secret = os.getenv("EVENT_INGEST_HMAC_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(status_code=503, detail="Event ingestion is not configured")
    if not timestamp_header or not signature_header:
        raise HTTPException(status_code=401, detail="Missing event signature")
    try:
        timestamp = int(timestamp_header)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid event timestamp")
    skew = int(os.getenv("EVENT_MAX_CLOCK_SKEW_SECONDS", "300"))
    if abs(int(time.time()) - timestamp) > max(30, min(skew, 900)):
        raise HTTPException(status_code=401, detail="Event timestamp outside permitted window")
    supplied = signature_header.removeprefix("sha256=").strip().lower()
    signed = str(timestamp).encode("ascii") + b"." + body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="Invalid event signature")


def current_monitoring_user(request: Request) -> dict[str, Any]:
    global _jwks_client
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
    url = jwks_url()
    issuer = issuer_url()
    if not url or not issuer:
        raise HTTPException(status_code=503, detail="Monitoring authentication is not configured")
    token = auth.split(" ", 1)[1].strip()
    try:
        if _jwks_client is None:
            _jwks_client = PyJWKClient(url, cache_keys=True, lifespan=3600, timeout=3)
        signing_key = _jwks_client.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, signing_key, algorithms=["ES256", "RS256", "EdDSA"],
                            audience=os.getenv("JWT_AUDIENCE", jwt_audience()), issuer=issuer,
                            options={"require": ["exp", "iss", "sub", "aud"]})
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session")
    app_meta = claims.get("app_metadata") or {}
    role = app_meta.get("monitoring_role")
    if role not in {"super_admin", "technical_operator"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Monitoring role is not authorized")
    return {"user_id": claims.get("sub"), "role": role, "email": claims.get("email")}
