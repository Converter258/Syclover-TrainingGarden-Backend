from __future__ import annotations

from dataclasses import replace

from app.core.config import Settings
from app.core.database import Database
from app.core.seed import seed_database
from app.main import create_app
from app.services.docker import ContainerError, DockerService
from tests.conftest import ASGITestClient, auth_header


def _async_settings(base: Settings) -> Settings:
    return replace(base, instance_start_async=True)


def test_async_start_returns_starting_then_becomes_reachable(settings):
    app = create_app(settings)
    resolved = _async_settings(settings)
    database = Database(settings.database_path)
    database.initialize()
    seed_database(database, settings)
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    app.state.settings = resolved
    app.state.database = database
    app.state.docker = DockerService("mock")
    client = ASGITestClient(app)
    headers = auth_header(client, "async_player", "AsyncPass123!")

    challenge = next(
        item
        for item in client.get("/api/v1/challenges?mode=ctf", headers=headers).json()
        if item["slug"] == "welcome-header"
    )
    started = client.post(f"/api/v1/instances/{challenge['id']}", headers=headers)
    assert started.status_code == 201
    body = started.json()
    assert body["status"] in {"starting", "running"}
    # the mock docker service resolves during the response cycle, so the instance is reachable
    polled = client.get(f"/api/v1/instances/{body['id']}", headers=headers)
    assert polled.status_code == 200
    assert polled.json()["status"] == "running"
    assert polled.json()["connect_command"]


def test_failed_background_start_is_recorded(settings):
    class BrokenDocker(DockerService):
        def __init__(self):
            super().__init__(mode="mock")

        def start(self, **kwargs):
            raise ContainerError("boom: image missing")

    app = create_app(settings)
    resolved = _async_settings(settings)
    database = Database(settings.database_path)
    database.initialize()
    seed_database(database, settings)
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    app.state.settings = resolved
    app.state.database = database
    app.state.docker = BrokenDocker()
    client = ASGITestClient(app)
    headers = auth_header(client, "broken_player", "BrokenPass123!")

    challenge = next(
        item
        for item in client.get("/api/v1/challenges?mode=ctf", headers=headers).json()
        if item["slug"] == "welcome-header"
    )
    started = client.post(f"/api/v1/instances/{challenge['id']}", headers=headers)
    assert started.status_code == 201
    body = started.json()
    assert body["status"] in {"starting", "failed"}

    polled = client.get(f"/api/v1/instances/{body['id']}", headers=headers).json()
    assert polled["status"] == "failed"
    assert "boom" in polled["error_message"]
    # a failed start must not block a retry
    retry = client.post(f"/api/v1/instances/{challenge['id']}", headers=headers)
    assert retry.status_code == 201
