import asyncio
import uuid
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.core.database import Database
from app.core.security import digest_flag
from app.core.seed import seed_database
from app.main import create_app
from app.services.assets import store_bytes
from app.services.docker import DockerService


class ASGITestClient:
    """Small synchronous facade that avoids the deprecated TestClient/httpx bridge."""

    def __init__(self, app):
        self.app = app

    def request(self, method: str, path: str, **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs):
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs):
        return self.request("DELETE", path, **kwargs)


def seed_test_challenges(database: Database, settings: Settings) -> None:
    """Create explicit fixtures for API tests without restoring production demo seeding."""
    now = "2026-01-01T00:00:00+00:00"
    definitions = (
        {
            "title": "Welcome Header",
            "slug": "welcome-header",
            "description": "Inspect the service response carefully and recover the flag hidden in its metadata.",
            "category": "Web",
            "mode": "ctf",
            "difficulty": "easy",
            "points": 100,
            "docker_image": "nginx:alpine",
            "internal_port": 80,
            "flag": "SYC{welcome_to_training_garden}",
        },
        {
            "title": "Broken Profile",
            "slug": "broken-profile",
            "description": "Audit the profile endpoint and identify the authorization boundary that can be bypassed.",
            "category": "Web",
            "mode": "ctf",
            "difficulty": "normal",
            "points": 250,
            "docker_image": None,
            "internal_port": None,
            "flag": "SYC{profile_boundary_found}",
        },
        {
            "title": "Service Under Fire",
            "slug": "service-under-fire",
            "description": "Patch the vulnerable service, pass the health check, and keep the service available.",
            "category": "Pwn",
            "mode": "awdp",
            "difficulty": "hard",
            "points": 500,
            "docker_image": "nginx:alpine",
            "internal_port": 80,
            "flag": "SYC{defense_is_an_engineering_discipline}",
        },
    )
    with database.connect() as connection:
        if connection.execute("SELECT 1 FROM challenges LIMIT 1").fetchone():
            return
        challenge_ids: dict[str, str] = {}
        for item in definitions:
            challenge_id = str(uuid.uuid4())
            challenge_ids[item["slug"]] = challenge_id
            connection.execute(
                """
                INSERT INTO challenges (
                    id, title, slug, description, category, mode, difficulty, points,
                    docker_image, internal_port, build_status, flag_template, dynamic_flag,
                    flag_digest, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'none', ?, 0, ?, 'published', ?, ?)
                """,
                (
                    challenge_id, item["title"], item["slug"], item["description"], item["category"],
                    item["mode"], item["difficulty"], item["points"], item["docker_image"],
                    item["internal_port"], item["flag"], digest_flag(item["flag"], settings.secret_key),
                    now, now,
                ),
            )
        for kind, filename, content in (
            ("check_script", "check.sh", b"#!/bin/sh\nexit 0\n"),
            ("fix_script", "fix.sh", b"#!/bin/sh\nexit 0\n"),
        ):
            stored_name, _ = store_bytes(
                settings.storage_path, challenge_ids["service-under-fire"], filename, content
            )
            connection.execute(
                """
                INSERT INTO assets (
                    id, challenge_id, user_id, kind, original_name, stored_name, size_bytes,
                    validation_status, validation_output, created_at
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, 'valid', 'Test fixture', ?)
                """,
                (
                    str(uuid.uuid4()), challenge_ids["service-under-fire"], kind, filename,
                    stored_name, len(content), now,
                ),
            )


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_name="Test Garden",
        environment="test",
        secret_key="test-secret-key",
        database_path=tmp_path / "test.db",
        storage_path=tmp_path / "storage",
        cors_origins=("http://testserver",),
        token_ttl_minutes=60,
        instance_ttl_minutes=10,
        instance_public_host="testserver",
        instance_bind_address="127.0.0.1",
        instance_port_range_start=None,
        instance_port_range_end=None,
        instance_reaper_enabled=False,
        instance_reaper_interval_seconds=60,
        instance_start_async=False,
        flag_prefix="SYC",
        public_base_url="",
        docker_mode="mock",
        seed_demo=True,
        admin_username="Syclover",
        admin_password="AdminPass123!",
        max_upload_bytes=1024 * 1024,
        max_build_upload_bytes=8 * 1024 * 1024,
    )


@pytest.fixture()
def client(settings: Settings):
    application = create_app(settings)
    database = Database(settings.database_path)
    database.initialize()
    seed_database(database, settings)
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    seed_test_challenges(database, settings)
    application.state.settings = settings
    application.state.database = database
    application.state.docker = DockerService(settings.docker_mode)
    yield ASGITestClient(application)


def auth_header(client: ASGITestClient, username: str = "player", password: str = "PlayerPass123!"):
    response = client.post("/api/v1/auth/register", json={"username": username, "password": password})
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def player_headers(client: ASGITestClient):
    return auth_header(client)


@pytest.fixture()
def admin_headers(client: ASGITestClient):
    response = client.post("/api/v1/auth/login", json={"username": "Syclover", "password": "AdminPass123!"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
