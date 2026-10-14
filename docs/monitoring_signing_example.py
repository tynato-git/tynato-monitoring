"""Small signing helper for the CRM backend. Do not call network APIs inline in business requests.
Queue the payload in the CRM's durable outbox first, then send/retry it asynchronously.
"""
import hashlib
import hmac
import json
import time
from typing import Any


def signed_monitoring_request_body(event: dict[str, Any], secret: str) -> tuple[bytes, dict[str, str]]:
    if len(secret) < 32:
        raise ValueError("Monitoring HMAC secret must be at least 32 characters")
    body = json.dumps(event, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    timestamp = str(int(time.time()))
    digest = hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    return body, {"Content-Type": "application/json", "X-Monitor-Timestamp": timestamp,
                  "X-Monitor-Signature": "sha256=" + digest}
