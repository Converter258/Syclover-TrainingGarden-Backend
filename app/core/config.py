from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

HOST_PATTERN = re.compile(r"^[A-Za-z0-9._:\[\]-]{1,64}$")


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _as_host(value: str | None, default: str) -> str:
    candidate = (value or default).strip()
    return candidate if HOST_PATTERN.fullmatch(candidate) else default


def _as_optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if 1 <= parsed <= 65535 else None


@dataclass(frozen=True)
class Settings:
    app_name: str
    environment: str
    secret_key: str
    database_path: Path
    storage_path: Path
    cors_origins: tuple[str, ...]
    token_ttl_minutes: int
    instance_ttl_minutes: int
    instance_public_host: str
    instance_bind_address: str
    instance_port_range_start: int | None
    instance_port_range_end: int | None
    instance_reaper_enabled: bool
    instance_reaper_interval_seconds: int
    instance_start_async: bool
    flag_prefix: str
    public_base_url: str
    docker_mode: str
    seed_demo: bool
    admin_username: str
    admin_password: str | None
    max_upload_bytes: int
    max_build_upload_bytes: int

    @property
    def instance_port_range(self) -> range | None:
        """Published host ports allowed for challenge containers, when constrained."""
        if self.instance_port_range_start is None or self.instance_port_range_end is None:
            return None
        if self.instance_port_range_start > self.instance_port_range_end:
            return None
        return range(self.instance_port_range_start, self.instance_port_range_end + 1)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        app_name="Syclover Training Garden",
        environment=os.getenv("SYCL_ENV", "development"),
        secret_key=os.getenv("SYCL_SECRET_KEY", "development-only-change-me"),
        database_path=Path(os.getenv("SYCL_DATABASE_PATH", "./data/training_garden.db")),
        storage_path=Path(os.getenv("SYCL_STORAGE_PATH", "./storage")),
        cors_origins=tuple(
            origin.strip()
            for origin in os.getenv("SYCL_CORS_ORIGINS", "http://localhost:5173").split(",")
            if origin.strip()
        ),
        token_ttl_minutes=int(os.getenv("SYCL_TOKEN_TTL_MINUTES", "720")),
        instance_ttl_minutes=int(os.getenv("SYCL_INSTANCE_TTL_MINUTES", "60")),
        instance_public_host=os.getenv("SYCL_INSTANCE_PUBLIC_HOST", "localhost"),
        instance_bind_address=_as_host(os.getenv("SYCL_INSTANCE_BIND_ADDRESS"), "127.0.0.1"),
        instance_port_range_start=_as_optional_int(os.getenv("SYCL_INSTANCE_PORT_RANGE_START")),
        instance_port_range_end=_as_optional_int(os.getenv("SYCL_INSTANCE_PORT_RANGE_END")),
        instance_reaper_enabled=_as_bool(os.getenv("SYCL_INSTANCE_REAPER_ENABLED"), True),
        instance_reaper_interval_seconds=max(
            15, int(os.getenv("SYCL_INSTANCE_REAPER_INTERVAL_SECONDS", "60"))
        ),
        instance_start_async=_as_bool(os.getenv("SYCL_INSTANCE_START_ASYNC"), True),
        flag_prefix=os.getenv("SYCL_FLAG_PREFIX", "SYC").strip() or "SYC",
        public_base_url=os.getenv("SYCL_PUBLIC_BASE_URL", "").strip().rstrip("/"),
        docker_mode=os.getenv("SYCL_DOCKER_MODE", "cli"),
        seed_demo=_as_bool(os.getenv("SYCL_SEED_DEMO"), False),
        # Kept under the legacy field name for deployment compatibility. The root
        # administrator username is fixed by the platform contract.
        admin_username="Syclover",
        admin_password=os.getenv("SYCL_ADMIN_PASSWORD", "Syclover@2026") or None,
        max_upload_bytes=int(os.getenv("SYCL_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))),
        max_build_upload_bytes=int(
            os.getenv("SYCL_MAX_BUILD_UPLOAD_BYTES", str(128 * 1024 * 1024))
        ),
    )
