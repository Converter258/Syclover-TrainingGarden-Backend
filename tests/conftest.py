import asyncio
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.core.database import Database
from app.core.seed import seed_database
from app.main import create_app
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
        docker_mode="mock",
        seed_demo=True,
        admin_username="admin",
        admin_password="AdminPass123!",
        max_upload_bytes=1024 * 1024,
    )


@pytest.fixture()
def client(settings: Settings):
    application = create_app(settings)
    database = Database(settings.database_path)
    database.initialize()
    seed_database(database, settings)
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    application.state.settings = settings
    application.state.database = database
    application.state.docker = DockerService(settings.docker_mode)
    yield ASGITestClient(application)


def auth_header(client: ASGITestClient, username: str = "player", password: str = "PlayerPass123!"):
    response = client.post("/api/v1/auth/register", json={"username": username, "password": password})
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def player_headers(client: TestClient):
    return auth_header(client)


@pytest.fixture()
def admin_headers(client: TestClient):
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "AdminPass123!"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
