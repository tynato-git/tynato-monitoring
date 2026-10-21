#!/usr/bin/env python3

import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ENV_FILE = Path("/opt/tynato-monitoring/deployment/api.env")
REPORT_URL = "http://127.0.0.1:18080/v1/probes"
INTERVAL_SECONDS = 30
TIMEOUT_SECONDS = 3
TARGETS = {
    "invoice_api": "http://127.0.0.1:8001/openapi.json",
}


def load_secret() -> str:
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if line.startswith("EVENT_INGEST_HMAC_SECRET="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def probe(url: str) -> dict:
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            code = response.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    except Exception as exc:
        reason = getattr(exc, "reason", exc)
        return {"ok": False, "latency_ms": None, "http_status": None,
                "error_type": type(reason).__name__}
    latency = round((time.monotonic() - started) * 1000, 2)
    ok = 200 <= code < 300
    return {"ok": ok, "latency_ms": latency, "http_status": code, "error_type": None}


def report(secret: str, check_name: str, result: dict) -> bool:
    body = json.dumps({"check_name": check_name, **result},
                      separators=(",", ":")).encode()
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        REPORT_URL, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Monitor-Timestamp": ts,
            "X-Monitor-Signature": "sha256=" + sig,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return 200 <= response.status < 300
    except Exception as exc:
        print(f"report failed for {check_name}: {type(exc).__name__}", flush=True)
        return False


def run_once(secret: str) -> None:
    for name, url in TARGETS.items():
        report(secret, name, probe(url))


def main() -> None:
    secret = load_secret()
    if len(secret) < 32:
        print("monitoring secret unavailable; probes not reported", flush=True)
        sys.exit(1)
    once = "--once" in sys.argv
    while True:
        started = time.monotonic()
        run_once(secret)
        if once:
            break
        time.sleep(max(1.0, INTERVAL_SECONDS - (time.monotonic() - started)))


if __name__ == "__main__":
    main()