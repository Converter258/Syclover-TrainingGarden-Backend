import io
import sqlite3
from zipfile import ZipFile

from app.core.database import Database
from app.core.seed import seed_database
from app.services.assets import validate_patch_archive
from tests.conftest import auth_header


def _zip_bytes(entries: dict[str, str | bytes]) -> bytes:
    archive = io.BytesIO()
    with ZipFile(archive, "w") as bundle:
        for name, content in entries.items():
            bundle.writestr(name, content)
    return archive.getvalue()


def test_production_seed_creates_root_admin_but_no_default_challenges(settings):
    database = Database(settings.database_path)
    database.initialize()
    seed_database(database, settings)
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM challenges").fetchone()[0] == 0
        root = connection.execute(
            "SELECT username, role FROM users WHERE username = 'Syclover'"
        ).fetchone()
    assert dict(root) == {"username": "Syclover", "role": "root_admin"}


def test_legacy_admin_migration_keeps_other_admins_as_content_admins(settings):
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'player' CHECK (role IN ('player', 'admin')),
                is_active INTEGER NOT NULL DEFAULT 1,
                avatar_url TEXT,
                signature TEXT,
                direction TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO users (id, username, password_hash, role, is_active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
            [
                ("legacy-root", "admin", "hash", "admin", "2026-01-01T00:00:00+00:00"),
                ("content-admin", "content", "hash", "admin", "2026-01-01T00:00:00+00:00"),
            ],
        )
    database = Database(settings.database_path)
    database.initialize()
    seed_database(database, settings)
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT username, role FROM users WHERE id IN ('legacy-root', 'content-admin') ORDER BY id"
        ).fetchall()
    assert [dict(row) for row in rows] == [
        {"username": "content", "role": "admin"},
        {"username": "Syclover", "role": "root_admin"},
    ]


def test_root_admin_boundary_and_regular_admin_scope(client, admin_headers):
    member_headers = auth_header(client, "content_admin", "ContentAdmin123!")
    member = client.get("/api/v1/auth/me", headers=member_headers).json()
    promoted = client.patch(
        f"/api/v1/users/{member['id']}",
        headers=admin_headers,
        json={"role": "admin"},
    )
    assert promoted.status_code == 200
    ordinary = client.post(
        "/api/v1/auth/login",
        json={"username": "content_admin", "password": "ContentAdmin123!"},
    )
    ordinary_headers = {"Authorization": f"Bearer {ordinary.json()['access_token']}"}

    assert client.get("/api/v1/users", headers=ordinary_headers).status_code == 403
    assert client.post(
        "/api/v1/challenges/tags",
        headers=ordinary_headers,
        json={"name": "admin-created", "description": "Created by a regular admin"},
    ).status_code == 201
    created = client.post(
        "/api/v1/challenges",
        headers=ordinary_headers,
        json={
            "title": "Admin Managed Challenge",
            "slug": "admin-managed-challenge",
            "description": "A challenge created by the content administrator.",
            "category": "Web",
            "mode": "ctf",
            "difficulty": "easy",
            "points": 100,
            "flag": "SYC{admin_managed}",
            "status": "published",
        },
    )
    assert created.status_code == 201
    assert client.post(
        f"/api/v1/users/{member['id']}/achievements/core_member",
        headers=ordinary_headers,
    ).status_code == 403


def test_patch_zip_validation_enforces_awdp_layout(tmp_path):
    valid_pwn = tmp_path / "pwn.zip"
    valid_pwn.write_bytes(_zip_bytes({"fix.sh": "#!/bin/sh\nexit 0\n", "target": b"binary"}))
    assert validate_patch_archive(valid_pwn, "Pwn")[0] is True

    valid_web = tmp_path / "web.zip"
    valid_web.write_bytes(_zip_bytes({"fix.sh": "#!/bin/sh\nexit 0\n", "index.html": "fixed"}))
    assert validate_patch_archive(valid_web, "Web")[0] is True

    missing_fix = tmp_path / "missing-fix.zip"
    missing_fix.write_bytes(_zip_bytes({"target": b"binary"}))
    assert validate_patch_archive(missing_fix, "Pwn")[0] is False

    extra_binary = tmp_path / "extra-binary.zip"
    extra_binary.write_bytes(
        _zip_bytes({"fix.sh": "#!/bin/sh\nexit 0\n", "one": b"1", "two": b"2"})
    )
    assert validate_patch_archive(extra_binary, "Pwn")[0] is False

    unsafe = tmp_path / "unsafe.zip"
    unsafe.write_bytes(_zip_bytes({"fix.sh": "#!/bin/sh\nexit 0\n", "../escape": "no"}))
    assert validate_patch_archive(unsafe, "Web")[0] is False


def test_peak_geek_badge_is_granted_after_all_marked_challenges(client, admin_headers, player_headers):
    flags = ("SYC{peak_one}", "SYC{peak_two}")
    challenge_ids = []
    for index, flag in enumerate(flags, start=1):
        response = client.post(
            "/api/v1/challenges",
            headers=admin_headers,
            json={
                "title": f"Peak Geek 2025 #{index}",
                "slug": f"peak-geek-2025-{index}",
                "description": "A marked Peak Geek 2025 event challenge.",
                "category": "Web",
                "mode": "ctf",
                "difficulty": "easy",
                "points": 100,
                "flag": flag,
                "status": "published",
            },
        )
        assert response.status_code == 201
        challenge_ids.append(response.json()["id"])

    first = client.post(
        f"/api/v1/challenges/{challenge_ids[0]}/submit",
        headers=player_headers,
        json={"flag": flags[0]},
    )
    assert first.status_code == 200
    profile_before = client.get("/api/v1/users/me/profile", headers=player_headers).json()
    assert "peak_geek_2025" not in profile_before["achievement_slugs"]

    second = client.post(
        f"/api/v1/challenges/{challenge_ids[1]}/submit",
        headers=player_headers,
        json={"flag": flags[1]},
    )
    assert second.status_code == 200
    profile_after = client.get("/api/v1/users/me/profile", headers=player_headers).json()
    assert "peak_geek_2025" in profile_after["achievement_slugs"]
