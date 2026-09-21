from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


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
    docker_mode: str
    seed_demo: bool
    admin_username: str
    admin_password: str | None
    max_upload_bytes: int


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
        docker_mode=os.getenv("SYCL_DOCKER_MODE", "cli"),
        seed_demo=_as_bool(os.getenv("SYCL_SEED_DEMO"), True),
        admin_username=os.getenv("SYCL_ADMIN_USERNAME", "admin"),
        admin_password=os.getenv("SYCL_ADMIN_PASSWORD", "Syclover@2026") or None,
        max_upload_bytes=int(os.getenv("SYCL_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))),
    )
