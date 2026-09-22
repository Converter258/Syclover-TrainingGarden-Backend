import io
from zipfile import ZipFile

from tests.conftest import auth_header


def test_register_login_and_admin_permissions(client, player_headers, admin_headers):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/v1/auth/me", headers=player_headers).status_code == 200
    assert client.get("/api/v1/users", headers=player_headers).status_code == 403
    users = client.get("/api/v1/users", headers=admin_headers)
    assert users.status_code == 200
    assert {item["username"] for item in users.json()} == {"admin", "player"}


def test_flag_submission_awards_only_once_and_updates_scoreboard(client, player_headers):
    challenges = client.get("/api/v1/challenges", headers=player_headers).json()
    challenge = next(item for item in challenges if item["slug"] == "welcome-header")
    wrong = client.post(
        f"/api/v1/challenges/{challenge['id']}/submit",
        headers=player_headers,
        json={"flag": "SYC{wrong}"},
    )
    assert wrong.json()["correct"] is False
    correct = client.post(
        f"/api/v1/challenges/{challenge['id']}/submit",
        headers=player_headers,
        json={"flag": "SYC{welcome_to_training_garden}"},
    )
    assert correct.json()["awarded_points"] == 100
    repeat = client.post(
        f"/api/v1/challenges/{challenge['id']}/submit",
        headers=player_headers,
        json={"flag": "SYC{welcome_to_training_garden}"},
    )
    assert repeat.json()["awarded_points"] == 0
    ranking = client.get("/api/v1/scoreboard", headers=player_headers).json()["rankings"][0]
    assert ranking["username"] == "player"
    assert ranking["score"] == 100
    assert ranking["solves"] == 1
    assert client.get("/api/v1/scoreboard?mode=ctf", headers=player_headers).json()["rankings"][0]["score"] == 100
    assert client.get("/api/v1/scoreboard?mode=awdp", headers=player_headers).json()["rankings"][0]["score"] == 0
    detail = client.get(f"/api/v1/challenges/{challenge['id']}", headers=player_headers).json()
    assert detail["solves"] == 1
    assert detail["bloods"][0]["username"] == "player"
    assert detail["bloods"][0]["rank"] == 1
    for username in ("second", "third", "fourth"):
        headers = auth_header(client, username, f"{username.title()}Pass123!")
        solved = client.post(
            f"/api/v1/challenges/{challenge['id']}/submit",
            headers=headers,
            json={"flag": "SYC{welcome_to_training_garden}"},
        )
        assert solved.json()["correct"] is True
    detail = client.get(f"/api/v1/challenges/{challenge['id']}", headers=player_headers).json()
    assert detail["solves"] == 4
    assert [(entry["rank"], entry["username"]) for entry in detail["bloods"]] == [
        (1, "player"),
        (2, "second"),
        (3, "third"),
    ]


def test_mock_instance_lifecycle(client, player_headers):
    challenge = next(
        item for item in client.get("/api/v1/challenges", headers=player_headers).json()
        if item["slug"] == "welcome-header"
    )
    started = client.post(f"/api/v1/instances/{challenge['id']}", headers=player_headers)
    assert started.status_code == 201
    assert started.json()["status"] == "running"
    assert client.post(f"/api/v1/instances/{challenge['id']}", headers=player_headers).status_code == 409
    stopped = client.delete(f"/api/v1/instances/{started.json()['id']}", headers=player_headers)
    assert stopped.status_code == 200
    assert client.get("/api/v1/instances", headers=player_headers).json()[0]["status"] == "stopped"


def test_admin_challenge_and_attachment(client, admin_headers, player_headers):
    payload = {
        "title": "Crypto Orchard",
        "slug": "crypto-orchard",
        "description": "Recover the secret from a deliberately weak construction.",
        "category": "Crypto",
        "mode": "ctf",
        "difficulty": "normal",
        "points": 300,
        "flag": "SYC{orchard}",
        "status": "published",
    }
    created = client.post("/api/v1/challenges", headers=admin_headers, json=payload)
    assert created.status_code == 201
    challenge_id = created.json()["id"]
    upload = client.post(
        f"/api/v1/challenges/{challenge_id}/attachments?filename=notes.txt",
        headers={**admin_headers, "Content-Type": "application/octet-stream"},
        content=b"training notes",
    )
    assert upload.status_code == 201
    detail = client.get(f"/api/v1/challenges/{challenge_id}", headers=player_headers).json()
    assert detail["attachments"][0]["original_name"] == "notes.txt"
    download = client.get(detail["attachments"][0]["download_url"], headers=player_headers)
    assert download.content == b"training notes"


def test_awdp_asset_validation_deploy_and_ownership(client, player_headers, admin_headers):
    challenge = next(
        item for item in client.get("/api/v1/challenges?mode=awdp", headers=player_headers).json()
        if item["slug"] == "service-under-fire"
    )
    asset = client.post(
        f"/api/v1/awdp/{challenge['id']}/assets?kind=patch&filename=fix.patch",
        headers={**player_headers, "Content-Type": "application/octet-stream"},
        content=b"--- a/index.html\n+++ b/index.html\n@@ -1 +1 @@\n-old\n+fixed\n",
    )
    assert asset.status_code == 201
    assert asset.json()["validation_status"] == "valid"
    instance = client.post(f"/api/v1/instances/{challenge['id']}", headers=player_headers).json()
    event = client.post(
        f"/api/v1/awdp/instances/{instance['id']}/deploy/{asset.json()['id']}",
        headers=player_headers,
    )
    assert event.status_code == 200
    assert event.json()["success"] is True
    defense_detail = client.get(
        f"/api/v1/challenges/{challenge['id']}", headers=player_headers
    ).json()
    assert defense_detail["defense_solves"] == 1
    assert defense_detail["attack_solves"] == 0
    client.post(
        f"/api/v1/challenges/{challenge['id']}/submit",
        headers=player_headers,
        json={"flag": "SYC{defense_is_an_engineering_discipline}"},
    )
    attack_detail = client.get(
        f"/api/v1/challenges/{challenge['id']}", headers=player_headers
    ).json()
    assert attack_detail["attack_solves"] == 1
    assert attack_detail["defense_bloods"][0]["username"] == "player"
    other = auth_header(client, "other", "OtherPass123!")
    assert client.get(asset.json()["download_url"], headers=other).status_code == 404
    assert client.delete(
        f"/api/v1/challenges/{challenge['id']}", headers=admin_headers
    ).status_code == 200


def test_awdp_requires_admin_scripts_and_player_cannot_upload_fix(client, admin_headers, player_headers):
    payload = {
        "title": "Web Defense Alpha",
        "slug": "web-defense-alpha",
        "description": "Defend the web service and keep its health check available.",
        "category": "Web",
        "mode": "awdp",
        "difficulty": "insane",
        "points": 800,
        "docker_image": "nginx:alpine",
        "internal_port": 80,
        "flag": "SYC{awdp_alpha}",
        "status": "draft",
    }
    created = client.post("/api/v1/challenges", headers=admin_headers, json=payload)
    assert created.status_code == 201
    invalid_category = client.post(
        "/api/v1/challenges",
        headers=admin_headers,
        json={**payload, "slug": "invalid-awdp-category", "category": "Crypto"},
    )
    assert invalid_category.status_code == 422
    challenge_id = created.json()["id"]
    cannot_publish = client.patch(
        f"/api/v1/challenges/{challenge_id}", headers=admin_headers, json={"status": "published"}
    )
    assert cannot_publish.status_code == 422
    for kind in ("check_script", "fix_script"):
        uploaded = client.post(
            f"/api/v1/awdp/{challenge_id}/scripts?kind={kind}&filename={kind}.sh",
            headers={**admin_headers, "Content-Type": "application/octet-stream"},
            content=b"#!/bin/sh\nexit 0\n",
        )
        assert uploaded.status_code == 201
        assert uploaded.json()["validation_status"] == "valid"
    published = client.patch(
        f"/api/v1/challenges/{challenge_id}", headers=admin_headers, json={"status": "published"}
    )
    assert published.status_code == 200
    forbidden_fix = client.post(
        f"/api/v1/awdp/{challenge_id}/assets?kind=fix_script&filename=fix.sh",
        headers={**player_headers, "Content-Type": "application/octet-stream"},
        content=b"#!/bin/sh\nexit 0\n",
    )
    assert forbidden_fix.status_code == 422


def test_admin_can_take_offline_and_delete_challenge_and_member(client, admin_headers):
    member_headers = auth_header(client, "remove_me", "RemoveMe123!")
    member = client.get("/api/v1/auth/me", headers=member_headers).json()
    deleted_member = client.delete(f"/api/v1/users/{member['id']}", headers=admin_headers)
    assert deleted_member.status_code == 200
    assert client.get("/api/v1/auth/me", headers=member_headers).status_code == 401

    challenge = next(
        item for item in client.get("/api/v1/challenges?mode=ctf", headers=admin_headers).json()
        if item["slug"] == "broken-profile"
    )
    offline = client.patch(
        f"/api/v1/challenges/{challenge['id']}", headers=admin_headers, json={"status": "draft"}
    )
    assert offline.status_code == 200
    assert offline.json()["status"] == "draft"
    deleted = client.delete(f"/api/v1/challenges/{challenge['id']}", headers=admin_headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/challenges/{challenge['id']}", headers=admin_headers).status_code == 404


def test_admin_builds_uploaded_archive_immediately(client, admin_headers):
    payload = {
        "title": "Uploaded Calc",
        "slug": "uploaded-calc",
        "description": "# Calculator\n\nExploit the uploaded calculator service.",
        "category": "Pwn",
        "mode": "ctf",
        "difficulty": "noob",
        "points": 50,
        "flag": "SYC{calc}",
        "status": "draft",
    }
    challenge = client.post("/api/v1/challenges", headers=admin_headers, json=payload).json()
    archive = io.BytesIO()
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("calc/Dockerfile", "FROM alpine:3.20\nEXPOSE 9999\nCMD [\"true\"]\n")
        bundle.writestr("calc/readme.txt", "build context")
    built = client.post(
        f"/api/v1/challenges/{challenge['id']}/build?filename=calc.zip",
        headers={**admin_headers, "Content-Type": "application/zip"},
        content=archive.getvalue(),
    )
    assert built.status_code == 200
    assert built.json()["status"] == "success"
    assert built.json()["internal_port"] == 9999
    detail = client.get(f"/api/v1/challenges/{challenge['id']}", headers=admin_headers).json()
    assert detail["build_status"] == "success"
    assert detail["docker_image"].endswith(":alpha0.0.3-hotfix.2")

    unsafe = io.BytesIO()
    with ZipFile(unsafe, "w") as bundle:
        bundle.writestr("../Dockerfile", "FROM scratch")
    rejected = client.post(
        f"/api/v1/challenges/{challenge['id']}/build?filename=unsafe.zip",
        headers={**admin_headers, "Content-Type": "application/zip"},
        content=unsafe.getvalue(),
    )
    assert rejected.status_code == 422


def test_hint_lifecycle_and_player_visibility(client, admin_headers, player_headers):
    challenge = next(
        item for item in client.get("/api/v1/challenges?mode=ctf", headers=admin_headers).json()
        if item["slug"] == "welcome-header"
    )
    created = client.post(
        f"/api/v1/challenges/{challenge['id']}/hints",
        headers=admin_headers,
        json={"title": "Headers", "content": "Inspect the **response headers**.", "status": "draft"},
    )
    assert created.status_code == 201
    hint = created.json()
    assert client.get(
        f"/api/v1/challenges/{challenge['id']}/hints", headers=player_headers
    ).json() == []
    online = client.patch(
        f"/api/v1/challenges/hints/{hint['id']}",
        headers=admin_headers,
        json={"status": "published"},
    )
    assert online.status_code == 200
    visible = client.get(
        f"/api/v1/challenges/{challenge['id']}/hints", headers=player_headers
    ).json()
    assert visible[0]["content"] == "Inspect the **response headers**."
    assert client.delete(
        f"/api/v1/challenges/hints/{hint['id']}", headers=admin_headers
    ).status_code == 200
