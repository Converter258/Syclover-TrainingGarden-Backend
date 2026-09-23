from datetime import datetime, timedelta

import pytest

from app.core.database import Database
from app.services.docker import ContainerError, DockerService
from app.services.reaper import expire_instances
from tests.conftest import auth_header


def test_players_only_list_and_control_their_own_independent_instances(client, player_headers, admin_headers):
    other_headers = auth_header(client, username="other_player")
    challenge = next(
        item for item in client.get("/api/v1/challenges", headers=player_headers).json()
        if item["slug"] == "welcome-header"
    )
    path = f"/api/v1/instances/{challenge['id']}"
    first = client.post(path, headers=player_headers)
    second = client.post(path, headers=other_headers)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert first.json()["public_port"] != second.json()["public_port"]
    assert [item["id"] for item in client.get("/api/v1/instances", headers=player_headers).json()] == [first.json()["id"]]
    assert [item["id"] for item in client.get("/api/v1/instances", headers=other_headers).json()] == [second.json()["id"]]
    assert client.get("/api/v1/instances", headers=admin_headers).json() == []
    other_id = second.json()["id"]
    assert client.get(f"/api/v1/instances/{other_id}", headers=player_headers).status_code == 404
    assert client.post(f"/api/v1/instances/{other_id}/extend", headers=player_headers).status_code == 404
    assert client.delete(f"/api/v1/instances/{other_id}", headers=player_headers).status_code == 404


def test_extension_moves_expiry_and_reaper_destroys_at_new_deadline(client, player_headers, settings):
    challenge = next(
        item for item in client.get("/api/v1/challenges", headers=player_headers).json()
        if item["slug"] == "welcome-header"
    )
    started = client.post(f"/api/v1/instances/{challenge['id']}", headers=player_headers).json()
    original_expiry = datetime.fromisoformat(started["expires_at"])
    extended = client.post(f"/api/v1/instances/{started['id']}/extend", headers=player_headers)
    assert extended.status_code == 200
    new_expiry = datetime.fromisoformat(extended.json()["expires_at"])
    assert new_expiry - original_expiry == timedelta(minutes=30)

    database = Database(settings.database_path)
    docker = client.app.state.docker
    assert expire_instances(database, docker, original_expiry + timedelta(seconds=1)) == []
    assert expire_instances(database, docker, new_expiry + timedelta(seconds=1)) == [started["id"]]
    assert client.get(f"/api/v1/instances/{started['id']}", headers=player_headers).json()["status"] == "stopped"
    assert client.post(f"/api/v1/instances/{started['id']}/extend", headers=player_headers).status_code == 409


def test_missing_flag_files_are_written_before_container_starts(monkeypatch):
    docker = DockerService(mode="cli")
    commands = []
    copied = {}

    def fake_run(command, timeout):
        commands.append(command)
        action = command[1]
        if action == "create":
            return "cid123"
        if action == "inspect" and "{{.Config.WorkingDir}}" in command:
            return "/app"
        if action == "cp":
            if command[2].startswith("cid123:"):
                if command[2] == "cid123:/app/flag":
                    return ""
                raise ContainerError("Could not find the file in container")
            from pathlib import Path

            copied[command[3]] = Path(command[2]).read_text(encoding="utf-8")
            return ""
        if action == "port":
            return "127.0.0.1:32000\n"
        if action == "inspect":
            return '{"Status":"running","Running":true,"Restarting":false,"ExitCode":0}'
        return ""

    monkeypatch.setattr(docker, "_run", fake_run)
    monkeypatch.setattr("app.services.docker.time.sleep", lambda _: None)
    started = docker.start(
        image="calc:1", internal_port=9999, name="sycl-test", user_id="u1", challenge_id="c1",
        flag="SYC{private}",
    )
    assert started.public_port == 32000
    assert copied == {"cid123:/flag": "SYC{private}\n", "cid123:/flag.txt": "SYC{private}\n"}
    assert commands.index(["docker", "start", "cid123"]) > max(
        index for index, command in enumerate(commands) if command[1] == "cp"
    )
    assert not any(command[3] == "cid123:/app/flag" for command in commands if command[1] == "cp" and len(command) > 3)


def test_failed_flag_injection_removes_the_created_container(monkeypatch):
    docker = DockerService(mode="cli")
    commands = []

    def fake_run(command, timeout):
        commands.append(command)
        if command[1] == "create":
            return "cid123"
        if command[1] == "inspect":
            return ""
        if command[1] == "cp":
            raise ContainerError("Docker daemon unavailable")
        return ""

    monkeypatch.setattr(docker, "_run", fake_run)
    with pytest.raises(ContainerError, match="daemon unavailable"):
        docker.start(
            image="calc:1", internal_port=9999, name="sycl-test", user_id="u1", challenge_id="c1",
            flag="SYC{private}",
        )
    assert ["docker", "rm", "--force", "--volumes", "cid123"] in commands
    assert not any(command[1] == "start" for command in commands)


def test_fun_achievements_track_first_try_comeback_and_categories(client, player_headers, admin_headers):
    challenges = client.get("/api/v1/challenges", headers=player_headers).json()
    web = next(item for item in challenges if item["slug"] == "welcome-header")
    second_web = next(item for item in challenges if item["slug"] == "broken-profile")
    pwn = next(item for item in challenges if item["slug"] == "service-under-fire")
    for _ in range(3):
        wrong = client.post(
            f"/api/v1/challenges/{second_web['id']}/submit",
            headers=player_headers, json={"flag": "SYC{wrong}"},
        )
        assert wrong.json()["correct"] is False
    for challenge, flag in (
        (web, "SYC{welcome_to_training_garden}"),
        (second_web, "SYC{profile_boundary_found}"),
        (pwn, "SYC{defense_is_an_engineering_discipline}"),
    ):
        result = client.post(f"/api/v1/challenges/{challenge['id']}/submit", headers=player_headers, json={"flag": flag})
        assert result.json()["correct"] is True
    created = client.post("/api/v1/challenges", headers=admin_headers, json={
        "title": "Crypto Badge Target", "slug": "crypto-badge-target", "description": "A third category for the badge.",
        "category": "Crypto", "mode": "ctf", "difficulty": "easy", "points": 50,
        "flag": "SYC{third_category}", "status": "published",
    })
    assert created.status_code == 201
    assert client.post(f"/api/v1/challenges/{created.json()['id']}/submit", headers=player_headers,
                       json={"flag": "SYC{third_category}"}).json()["correct"] is True
    badges = client.get("/api/v1/users/me/profile", headers=player_headers).json()["achievement_slugs"]
    assert {"first_try", "comeback", "versatile"}.issubset(badges)
