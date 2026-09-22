from __future__ import annotations

from dataclasses import replace

from app.core.config import Settings
from app.core.database import Database
from app.core.seed import seed_database
from app.api.routes.instances import _launch_instance
from app.main import create_app
from app.services.docker import ContainerError, ContainerState, DockerService, StartedContainer
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


def test_runtime_exit_records_docker_reason_and_logs(client, player_headers):
    challenge = next(
        item
        for item in client.get("/api/v1/challenges?mode=ctf", headers=player_headers).json()
        if item["slug"] == "welcome-header"
    )
    started = client.post(f"/api/v1/instances/{challenge['id']}", headers=player_headers).json()

    class ExitedDocker(DockerService):
        def __init__(self):
            super().__init__(mode="mock")

        def container_state(self, container_id):
            return ContainerState("exited", False, False, 137, True, "")

        def container_logs(self, container_id, tail=5):
            return "worker was killed"

    client.app.state.docker = ExitedDocker()
    polled = client.get(f"/api/v1/instances/{started['id']}", headers=player_headers)

    assert polled.status_code == 200
    body = polled.json()
    assert body["status"] == "failed"
    assert "exit code: 137" in body["error_message"]
    assert "OOM" in body["error_message"]
    assert "worker was killed" in body["error_message"]


def test_transient_docker_error_does_not_mark_a_running_instance_failed(client, player_headers):
    challenge = next(
        item
        for item in client.get("/api/v1/challenges?mode=ctf", headers=player_headers).json()
        if item["slug"] == "welcome-header"
    )
    started = client.post(f"/api/v1/instances/{challenge['id']}", headers=player_headers).json()

    class UnavailableDocker(DockerService):
        def __init__(self):
            super().__init__(mode="mock")

        def container_state(self, container_id):
            raise ContainerError("Docker daemon timed out")

    client.app.state.docker = UnavailableDocker()
    polled = client.get(f"/api/v1/instances/{started['id']}", headers=player_headers)

    assert polled.status_code == 200
    assert polled.json()["status"] == "running"
    assert polled.json()["error_message"] is None


def test_late_background_container_is_reclaimed_when_instance_was_deleted(settings):
    database = Database(settings.database_path)
    database.initialize()

    class LateDocker(DockerService):
        def __init__(self):
            super().__init__(mode="mock")
            self.stopped: list[str] = []

        def start(self, **kwargs):
            return StartedContainer("late-container", 32000)

        def stop(self, container_id):
            self.stopped.append(container_id)

    docker = LateDocker()
    _launch_instance("deleted-instance", "127.0.0.1", {}, database, docker)

    assert docker.stopped == ["late-container"]
