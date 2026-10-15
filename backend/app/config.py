from __future__ import annotations

import os
from pathlib import Path


def env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, value)


def csv_env(name: str) -> list[str]:
    return [v.strip() for v in os.getenv(name, "").split(",") if v.strip()]


def supabase_url() -> str:
    return os.getenv("SUPABASE_URL", "").strip().rstrip("/")


def issuer_url() -> str:
    base = supabase_url()
    return f"{base}/auth/v1" if base else ""


def jwks_url() -> str:
    return f"{issuer_url()}/.well-known/jwks.json" if issuer_url() else ""


def data_dir() -> Path:
    return Path(os.getenv("MONITORING_DATA_DIR", ".local-data")).expanduser()


def local_db_path() -> Path:
    return data_dir() / "monitoring.sqlite3"


def jwt_audience() -> str:
    return os.getenv("JWT_AUDIENCE", "authenticated")