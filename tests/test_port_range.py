"""Host port allocation from ``SYCL_INSTANCE_PORT_RANGE_*`` (Alpha0.0.6-hotfix.2).

Operators open a small window of ports at the cloud provider, so the platform has
to publish the challenge container on a port inside that window. Docker's own
choice comes from the host ephemeral range, which is why the platform pins the
port explicitly and retries when it loses a race for it.
"""

from __future__ import annotations

import pytest

from app.services.docker import ContainerError, DockerService


def _docker(monkeypatch, run, **kwargs):
    docker = DockerService(mode="cli")
    monkeypatch.setattr(docker, "_run", run)
    monkeypatch.setattr("app.services.docker.time.sleep", lambda _: None)
    monkeypatch.setattr("app.services.docker.random.randrange", lambda _: 0)
    return docker


def _start(docker, **overrides):
    arguments = dict(
        image="calc:1",
        internal_port=9999,
        name="sycl-test",
        user_id="u1",
        challenge_id="c1",
        bind_address="0.0.0.0",
    )
    arguments.update(overrides)
    return docker.start(**arguments)


def _runner(commands, publish=None, run_error=None):
    """Fake docker CLI: records commands and answers ``port``/``inspect``.

    ``run_error`` receives the ``docker run`` command and returns an error message
    (the start fails) or ``None`` (the start succeeds).
    """
    published: dict[str, str] = {}

    def fake_run(command, timeout):
        commands.append(command)
        if command[1] == "run":
            message = run_error(command) if run_error is not None else None
            if message:
                raise ContainerError(message)
            _, host_port, _ = command[command.index("-p") + 1].rsplit(":", 2)
            published["port"] = host_port
            return "cid123"
        if command[1] == "inspect":
            return '{"Status":"running","Running":true,"Restarting":false,"ExitCode":0}'
        if command[1] == "port":
            if publish is not None:
                return publish(command)
            return f"0.0.0.0:{published['port']}\n"
        return ""

    return fake_run


def test_configured_range_pins_the_host_port(monkeypatch):
    commands: list[list[str]] = []
    docker = _docker(monkeypatch, _runner(commands))

    started = _start(docker, port_range=range(30000, 30080))

    assert 30000 <= started.public_port <= 30079
    assert f"0.0.0.0:{started.public_port}:9999" in commands[0]


def test_single_port_range_is_pinned(monkeypatch):
    commands: list[list[str]] = []
    docker = _docker(monkeypatch, _runner(commands))

    started = _start(docker, port_range=range(30000, 30001))

    assert started.public_port == 30000
    assert "0.0.0.0:30000:9999" in commands[0]


def test_unconfigured_range_still_lets_docker_choose(monkeypatch):
    commands: list[list[str]] = []
    docker = _docker(monkeypatch, _runner(commands, publish=lambda _: "0.0.0.0:45678\n"))

    started = _start(docker, port_range=None)

    assert started.public_port == 45678
    assert "0.0.0.0::9999" in commands[0]


def test_a_taken_port_is_skipped_and_the_next_one_is_used(monkeypatch):
    commands: list[list[str]] = []
    attempts: list[str] = []

    def run_error(command):
        spec = command[command.index("-p") + 1]
        attempts.append(spec)
        if len(attempts) == 1:
            return (
                "docker: Error response from daemon: driver failed programming external "
                "connectivity: Bind for 0.0.0.0:30000 failed: port is already allocated"
            )
        return None

    docker = _docker(monkeypatch, _runner(commands, run_error=run_error))

    started = _start(docker, port_range=range(30000, 30005))

    assert len(attempts) == 2, attempts
    assert attempts[0] == "0.0.0.0:30000:9999"
    assert attempts[1] != attempts[0]
    assert 30000 <= started.public_port <= 30004
    # the half-created container is removed before retrying with the same name
    assert ["docker", "rm", "--force", "--volumes", "sycl-test"] in commands


def test_exhausted_range_reports_a_clear_error(monkeypatch):
    attempts: list[str] = []

    def run_error(command):
        attempts.append(command[command.index("-p") + 1])
        return "Bind for 0.0.0.0:30000 failed: port is already allocated"

    docker = _docker(monkeypatch, _runner([], run_error=run_error))

    with pytest.raises(ContainerError) as failure:
        _start(docker, port_range=range(30000, 30004))

    message = str(failure.value)
    assert "30000-30003" in message
    assert "all 4 port(s) are in use" in message
    assert "SYCL_INSTANCE_PORT_RANGE_START/END" in message
    assert len(attempts) == 4, f"every candidate should be tried once: {attempts}"


def test_a_real_failure_is_not_retried_on_another_port(monkeypatch):
    commands: list[list[str]] = []
    attempts: list[str] = []

    def run_error(command):
        attempts.append(command[command.index("-p") + 1])
        return "docker: Error response from daemon: pull access denied for calc:1"

    docker = _docker(monkeypatch, _runner(commands, run_error=run_error))

    with pytest.raises(ContainerError, match="pull access denied"):
        _start(docker, port_range=range(30000, 30080))

    assert len(attempts) == 1
    assert not [command for command in commands if command[1] == "rm"]


def test_container_that_dies_after_start_is_not_retried(monkeypatch):
    """Only port conflicts are retried; a broken challenge must fail immediately."""
    commands: list[list[str]] = []
    runs = {"count": 0}

    def fake_run(command, timeout):
        commands.append(command)
        if command[1] == "run":
            runs["count"] += 1
            return "cid123"
        if command[1] == "port":
            return command[-1].replace("/tcp", "") + "\n"
        if command[1] == "inspect":
            return '{"Status":"exited","Running":false,"Restarting":false,"ExitCode":1,"OOMKilled":false,"Error":""}'
        return ""

    docker = _docker(monkeypatch, fake_run)

    with pytest.raises(ContainerError, match="exited during startup"):
        _start(docker, port_range=range(30000, 30080))

    assert runs["count"] == 1


def test_docker_publishing_a_foreign_port_is_still_rejected(monkeypatch):
    """The range check stays as a backstop even though the port is now pinned."""
    commands: list[list[str]] = []
    docker = _docker(monkeypatch, _runner(commands, publish=lambda _: "0.0.0.0:41000\n"))

    with pytest.raises(ContainerError, match="outside the configured range 30000-30079"):
        _start(docker, port_range=range(30000, 30080))


def test_empty_range_is_rejected(monkeypatch):
    docker = _docker(monkeypatch, _runner([]))

    with pytest.raises(ContainerError, match="port range is empty"):
        _start(docker, port_range=range(30000, 30000))
