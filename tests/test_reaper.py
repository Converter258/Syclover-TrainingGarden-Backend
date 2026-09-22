from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.database import Database
from app.services.docker import ContainerError, ContainerState, DockerService
from app.services.reaper import expire_instances, prune_orphan_containers, sweep


def _database(tmp_path) -> Database:
    database = Database(tmp_path / "reaper.db")
    database.initialize()
    now = datetime.now(UTC).isoformat()
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO users (id, username, password_hash, role, is_active, created_at) "
            "VALUES ('u1', 'player', 'x', 'player', 1, ?)",
            (now,),
        )
        connection.execute(
            """
            INSERT INTO challenges (
                id, title, slug, description, category, mode, difficulty, points,
                docker_image, internal_port, flag_digest, status, created_at, updated_at
            ) VALUES ('c1', 'Calc', 'calc', 'd', 'Pwn', 'ctf', 'noob', 50, 'calc:1', 9999,
                      'digest', 'published', ?, ?)
            """,
            (now, now),
        )
    return database


def _instance(database: Database, instance_id: str, *, status: str, expires_at: datetime,
              container_id: str | None = "abc123", name: str | None = None) -> None:
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO instances (
                id, user_id, challenge_id, container_id, container_name, public_host,
                public_port, status, expires_at, created_at
            ) VALUES (?, 'u1', 'c1', ?, ?, 'localhost', 40000, ?, ?, ?)
            """,
            (
                instance_id,
                container_id,
                name or f"sycl-{instance_id}",
                status,
                expires_at.isoformat(),
                datetime.now(UTC).isoformat(),
            ),
        )


class RecordingDocker(DockerService):
    def __init__(self, containers: list[dict[str, str]] | None = None, fail: bool = False):
        super().__init__(mode="mock")
        self.stopped: list[str] = []
        self.containers = containers or []
        self.fail = fail

    def stop(self, container_id: str) -> None:
        if self.fail:
            raise ContainerError("daemon unavailable")
        self.stopped.append(container_id)

    def list_managed_containers(self) -> list[dict[str, str]]:
        return list(self.containers)


def test_expired_instance_is_stopped_and_marked(tmp_path):
    database = _database(tmp_path)
    docker = RecordingDocker()
    _instance(database, "expired", status="running", expires_at=datetime.now(UTC) - timedelta(minutes=5))
    _instance(database, "alive", status="running", expires_at=datetime.now(UTC) + timedelta(minutes=5))

    expired = expire_instances(database, docker)

    assert expired == ["expired"]
    assert docker.stopped == ["abc123"]
    with database.connect() as connection:
        rows = {
            row["id"]: row["status"]
            for row in connection.execute("SELECT id, status FROM instances").fetchall()
        }
    assert rows == {"expired": "stopped", "alive": "running"}


def test_expiry_records_container_errors_without_losing_the_row(tmp_path):
    database = _database(tmp_path)
    docker = RecordingDocker(fail=True)
    _instance(database, "expired", status="running", expires_at=datetime.now(UTC) - timedelta(minutes=1))

    expire_instances(database, docker)

    with database.connect() as connection:
        row = connection.execute("SELECT status, error_message FROM instances").fetchone()
    assert row["status"] == "stopped"
    assert "daemon unavailable" in row["error_message"]


def test_orphan_containers_are_stopped(tmp_path):
    database = _database(tmp_path)
    _instance(database, "alive", status="running", expires_at=datetime.now(UTC) + timedelta(minutes=5),
              name="sycl-live")
    docker = RecordingDocker(
        containers=[
            {"id": "111111", "name": "sycl-live"},
            {"id": "222222", "name": "sycl-abandoned"},
        ]
    )

    removed = prune_orphan_containers(database, docker)

    assert removed == ["sycl-abandoned"]
    assert docker.stopped == ["222222"]


def test_sweep_reports_both_reclamations(tmp_path):
    database = _database(tmp_path)
    _instance(database, "expired", status="running", expires_at=datetime.now(UTC) - timedelta(minutes=1))
    docker = RecordingDocker(containers=[{"id": "999", "name": "sycl-ghost"}])

    result = sweep(database, docker)

    assert result["expired"] == ["expired"]
    assert result["orphans"] == ["sycl-ghost"]


def test_docker_port_spec_honours_bind_address_and_range():
    from app.services.docker import _port_spec

    # without a configured range Docker picks the port, so no host port is given
    assert _port_spec("0.0.0.0", 9999, None) == "0.0.0.0::9999"
    assert _port_spec("127.0.0.1", 9999, None) == "127.0.0.1::9999"
    assert _port_spec("::", 9999, None) == "[::]::9999"
    # with a range the platform allocates the host port explicitly
    assert _port_spec("127.0.0.1", 9999, 30000) == "127.0.0.1:30000:9999"
    assert _port_spec("0.0.0.0", 9999, 30042) == "0.0.0.0:30042:9999"


def test_docker_start_rejects_non_ip_bind_address(monkeypatch):
    docker = DockerService(mode="cli")
    monkeypatch.setattr(docker, "_run", lambda command, timeout: "cid123")
    with pytest.raises(ContainerError):
        docker.start(
            image="calc:1",
            internal_port=9999,
            name="sycl-test",
            user_id="u1",
            challenge_id="c1",
            bind_address="example.com",
        )


def test_docker_start_allocates_a_port_from_the_configured_range(monkeypatch, tmp_path):
    docker = DockerService(mode="cli")
    commands: list[list[str]] = []
    publishing: dict[str, str] = {}
    monkeypatch.setattr("app.services.docker.time.sleep", lambda _: None)

    def fake_run(command, timeout):
        commands.append(command)
        if command[1] == "run":
            publishing["host_port"] = command[command.index("-p") + 1].rsplit(":", 2)[1]
            return "cid123"
        if command[1] == "inspect":
            return '{"Status":"running","Running":true,"Restarting":false,"ExitCode":0}'
        # Docker reports back the host port the platform asked for.
        return f"0.0.0.0:{publishing['host_port']}\n"

    monkeypatch.setattr(docker, "_run", fake_run)
    started = docker.start(
        image="calc:1",
        internal_port=9999,
        name="sycl-test",
        user_id="u1",
        challenge_id="c1",
        bind_address="0.0.0.0",
        port_range=range(32000, 32100),
    )

    # The platform must pin the host port itself; letting Docker choose would land
    # outside the small window operators open at the cloud provider.
    assert 32000 <= started.public_port < 32100
    assert f"0.0.0.0:{started.public_port}:9999" in commands[0]


def test_docker_start_rejects_port_outside_configured_range(monkeypatch):
    docker = DockerService(mode="cli")
    stopped: list[str] = []
    monkeypatch.setattr("app.services.docker.time.sleep", lambda _: None)

    def fake_run(command, timeout):
        if command[1] == "run":
            return "cid123"
        if command[1] == "port":
            return "0.0.0.0:41000\n"
        if command[1] == "inspect":
            return '{"Status":"running","Running":true,"Restarting":false,"ExitCode":0}'
        stopped.append(command[-1])
        return ""

    monkeypatch.setattr(docker, "_run", fake_run)
    with pytest.raises(ContainerError):
        docker.start(
            image="calc:1",
            internal_port=9999,
            name="sycl-test",
            user_id="u1",
            challenge_id="c1",
            port_range=range(32000, 32100),
        )
    assert stopped == ["cid123"]


def test_container_state_distinguishes_running_exited_and_missing(monkeypatch):
    docker = DockerService(mode="cli")

    def fake_run(command, timeout):
        container_id = command[-1]
        if container_id == "running123":
            return (
                '{"Status":"running","Running":true,"Restarting":false,'
                '"ExitCode":0,"OOMKilled":false,"Error":""}'
            )
        if container_id == "exited123":
            return (
                '{"Status":"exited","Running":false,"Restarting":false,'
                '"ExitCode":137,"OOMKilled":true,"Error":""}'
            )
        raise ContainerError(f"Error: No such object: {container_id}")

    monkeypatch.setattr(docker, "_run", fake_run)

    running = docker.container_state("running123")
    exited = docker.container_state("exited123")
    assert running is not None and running.active
    assert exited == ContainerState("exited", False, False, 137, True, "")
    assert docker.container_state("missing123") is None
    assert "exit code: 137" in docker.failure_message(exited)
    assert "OOM" in docker.failure_message(exited)


def test_container_state_does_not_hide_daemon_errors(monkeypatch):
    docker = DockerService(mode="cli")
    monkeypatch.setattr(
        docker,
        "_run",
        lambda command, timeout: (_ for _ in ()).throw(ContainerError("daemon unavailable")),
    )

    with pytest.raises(ContainerError, match="daemon unavailable"):
        docker.container_state("container123")


def test_start_rejects_a_container_that_exits_after_publishing_its_port(monkeypatch):
    docker = DockerService(mode="cli")
    stopped: list[str] = []

    def fake_run(command, timeout):
        if command[1] == "run":
            return "cid123"
        if command[1] == "port":
            return "127.0.0.1:32000\n"
        if command[1] == "inspect":
            return (
                '{"Status":"exited","Running":false,"Restarting":false,'
                '"ExitCode":2,"OOMKilled":false,"Error":"bad command"}'
            )
        if command[1] == "logs":
            return "application crashed"
        if command[1] == "rm":
            stopped.append(command[-1])
        return ""

    monkeypatch.setattr(docker, "_run", fake_run)

    with pytest.raises(ContainerError, match="exit code: 2"):
        docker.start(
            image="calc:1",
            internal_port=9999,
            name="sycl-test",
            user_id="u1",
            challenge_id="c1",
        )
    assert stopped == ["cid123"]


def test_start_rejects_a_container_that_entered_a_restart_loop(monkeypatch):
    docker = DockerService(mode="cli")

    def fake_run(command, timeout):
        if command[1] == "run":
            return "cid123"
        if command[1] == "port":
            return "127.0.0.1:32000\n"
        if command[1] == "inspect":
            return (
                '{"Status":"running","Running":true,"Restarting":false,'
                '"ExitCode":0,"OOMKilled":false,"Error":""}\t3'
            )
        return ""

    monkeypatch.setattr(docker, "_run", fake_run)

    with pytest.raises(ContainerError, match="restart count: 3"):
        docker.start(
            image="calc:1",
            internal_port=9999,
            name="sycl-test",
            user_id="u1",
            challenge_id="c1",
        )


def test_settings_parse_bind_address_range_and_reaper(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("SYCL_INSTANCE_BIND_ADDRESS", "0.0.0.0")
    monkeypatch.setenv("SYCL_INSTANCE_PORT_RANGE_START", "30000")
    monkeypatch.setenv("SYCL_INSTANCE_PORT_RANGE_END", "30010")
    monkeypatch.setenv("SYCL_INSTANCE_REAPER_ENABLED", "false")
    monkeypatch.setenv("SYCL_INSTANCE_REAPER_INTERVAL_SECONDS", "5")
    get_settings.cache_clear()
    settings = get_settings()

    assert settings.instance_bind_address == "0.0.0.0"
    assert settings.instance_port_range == range(30000, 30011)
    assert settings.instance_reaper_enabled is False
    assert settings.instance_reaper_interval_seconds == 15

    monkeypatch.setenv("SYCL_INSTANCE_BIND_ADDRESS", "not an address")
    get_settings.cache_clear()
    assert get_settings().instance_bind_address == "127.0.0.1"
    get_settings.cache_clear()
