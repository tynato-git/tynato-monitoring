import hashlib
import hmac
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ENV_FILE = Path("/opt/tynato-monitoring/deployment/api.env")
STATE_DIR = Path("/var/lib/tynato-host-metrics")
STATE_FILE = STATE_DIR / "state.json"
EVENT_URL = "http://127.0.0.1:18080/v1/events"
CPU_LIMIT = 90.0
MEMORY_AVAILABLE_MIN = 10.0
DISK_LIMIT = 85.0
CONTAINER_CPU_LIMIT = 90.0
CONTAINER_MEMORY_LIMIT = 85.0


def load_secret():
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if line.startswith("EVENT_INGEST_HMAC_SECRET="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def read_state():
    try:
        value = json.loads(STATE_FILE.read_text())
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    return {"checks": {}, "pending": []}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.chmod(tmp, 0o600)
    tmp.replace(STATE_FILE)


def post_event(event, secret):
    ts = str(int(time.time()))
    body = json.dumps(event, separators=(",", ":")).encode()
    sig = hmac.new(
        secret.encode(), ts.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    req = urllib.request.Request(
        EVENT_URL, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Monitor-Timestamp": ts,
            "X-Monitor-Signature": "sha256=" + sig,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def make_event(service, category, status, severity, summary, details):
    return {
        "event_id": str(uuid.uuid4()),
        "service": service,
        "category": category,
        "status": status,
        "severity": severity,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary[:500],
        "details": details,
    }


def read_cpu():
    parts = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
    values = [int(x) for x in parts]
    total = sum(values)
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return total, idle


def cpu_percent():
    a = read_cpu()
    time.sleep(1)
    b = read_cpu()
    total_delta, idle_delta = b[0] - a[0], b[1] - a[1]
    if total_delta <= 0:
        return 0.0
    return round(100.0 * (1.0 - idle_delta / total_delta), 2)


def memory_metrics():
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, raw = line.split(":", 1)
        values[key] = int(raw.strip().split()[0]) * 1024
    total = values["MemTotal"]
    available = values.get("MemAvailable", values.get("MemFree", 0))
    pct_available = round(100 * available / max(total, 1), 2)
    return {
        "total_mb": round(total / 1048576, 1),
        "available_mb": round(available / 1048576, 1),
        "available_pct": pct_available,
    }


def command_ok(args, timeout=4):
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0, result.stdout.strip()
    except Exception:
        return False, ""


def checks_now():
    checks = []
    cpu = cpu_percent()
    checks.append((
        "host-cpu", "host_metrics", "HOST_CPU_HIGH",
        cpu >= CPU_LIMIT, {"cpu_pct": cpu}, "high", 3,
        f"Host CPU usage is high ({cpu}%)",
    ))

    mem = memory_metrics()
    checks.append((
        "host-memory", "host_metrics", "HOST_MEMORY_LOW",
        mem["available_pct"] < MEMORY_AVAILABLE_MIN,
        mem, "critical", 3,
        f"Host available memory is low ({mem['available_pct']}% available)",
    ))

    usage = shutil.disk_usage("/")
    disk_pct = round(100 * usage.used / max(usage.total, 1), 2)
    checks.append((
        "host-disk", "host_metrics", "HOST_DISK_HIGH",
        disk_pct >= DISK_LIMIT,
        {"disk_used_pct": disk_pct, "free_gb": round(usage.free / 1e9, 2)},
        "high", 2, f"Root filesystem usage is high ({disk_pct}%)",
    ))

    checks.append((
        "crm-systemd", "crm_api", "CRM_API_SERVICE_DOWN",
        command_ok(["systemctl", "is-active", "--quiet", "tynato-api.service"])[0] is False,
        {"unit": "tynato-api.service"}, "critical", 1,
        "Main CRM API systemd service is not active",
    ))

    for name in ("monitor-api", "monitor-worker", "invoice-api"):
        ok, running = command_ok([
            "docker", "inspect", "--format={{.State.Running}}", name
        ])
        checks.append((
            "container-" + name, "container_" + name.replace("-", "_"),
            "CONTAINER_UNHEALTHY", not (ok and running == "true"),
            {"container": name, "running": running or "unknown"},
            "critical" if name in ("monitor-api", "monitor-worker") else "high",
            1, f"Container {name} is not running",
        ))

    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:18080/health/ready", timeout=2
        ) as response:
            ready = response.status == 200
    except Exception:
        ready = False
    checks.append((
        "monitor-api-ready", "monitor_api", "MONITORING_API_UNAVAILABLE",
        not ready, {"ready": ready}, "critical", 1,
        "Monitoring API readiness check failed",
    ))

    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:8001/openapi.json", timeout=3
        ) as response:
            invoice_ok = response.status == 200
    except Exception:
        invoice_ok = False
    checks.append((
        "invoice-api-ready", "invoice_api", "INVOICE_API_UNAVAILABLE",
        not invoice_ok, {"ready": invoice_ok}, "high", 3,
        "Invoice API is not responding; invoice generation will fail",
    ))

    # Sample resource use for each container. Skip if Docker stats is
    # temporarily unavailable; container-state checks remain active.
    ok, output = command_ok([
        "docker", "stats", "--no-stream",
        "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemPerc}}"
    ], timeout=6)
    if ok:
        for row in output.splitlines():
            parts = row.split("|")
            if len(parts) != 3:
                continue
            name = parts[0].strip()
            try:
                cpu_pct = float(parts[1].strip().rstrip("%"))
                mem_pct = float(parts[2].strip().rstrip("%"))
            except ValueError:
                continue
            service = "container_" + name.replace("-", "_")[:60]
            checks.append((
                "cpu-" + name, service, "CONTAINER_CPU_HIGH",
                cpu_pct >= CONTAINER_CPU_LIMIT,
                {"container": name, "cpu_pct": cpu_pct},
                "high", 3, f"Container {name} CPU usage is high ({cpu_pct}%)",
            ))
            checks.append((
                "memory-" + name, service, "CONTAINER_MEMORY_HIGH",
                mem_pct >= CONTAINER_MEMORY_LIMIT,
                {"container": name, "memory_pct": mem_pct},
                "high", 3, f"Container {name} memory usage is high ({mem_pct}%)",
            ))
    return checks


def main():
    secret = load_secret()
    if not secret:
        raise SystemExit("Monitoring HMAC secret unavailable; metrics not submitted")

    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    state = read_state()
    checks_state = state.setdefault("checks", {})
    pending = state.setdefault("pending", [])

    checks = checks_now()
    for key, service, category, failed, details, severity, threshold, summary in checks:
        item = checks_state.setdefault(
            key, {"active": False, "fail_count": 0, "success_count": 0}
        )
        if failed:
            item["fail_count"] += 1
            item["success_count"] = 0
            if not item["active"] and item["fail_count"] >= threshold:
                pending.append(make_event(
                    service, category, "failure", severity, summary, details
                ))
                item["active"] = True
        else:
            item["fail_count"] = 0
            if item["active"]:
                item["success_count"] += 1
                if item["success_count"] >= 2:
                    pending.append(make_event(
                        service, category, "success", severity,
                        "Recovered: " + summary, details,
                    ))
                    item["active"] = False
                    item["success_count"] = 0


    

    # Persist state/outbox before delivery so temporary API outages don't
    # silently discard already-detected events.
    pending[:] = pending[-100:]
    save_state(state)

    sent = 0
    while pending:
        if not post_event(pending[0], secret):
            break
        pending.pop(0)
        sent += 1
        save_state(state)

    print(
        f"Host metrics checks_evaluated={len(checks)} "
        f"events_sent={sent} events_pending={len(pending)}"
    )


if __name__ == "__main__":
    main()
